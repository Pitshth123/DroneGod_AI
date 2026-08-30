package mission

import (
	"fmt"
	"math"
	"sort"
	"time"
)

// V3-S09-A — Multi-drone GROUPED Core authority (SHADOW-READY, authority NOT yet
// flipped).  This file adds the Core-owned execution model for the exact scope
// characterized in frontend/tests/test_grouped_multi_characterization.py:
//
//   - one shared route, >= 2 participants, single shared index
//   - each drone flies to a FORMATION-OFFSET target
//         target[d] = pos[d] + (waypoint - group_centroid)
//     (legacy swarm_logic.group_goto_targets), never the raw waypoint
//   - per-drone GOTOs are ordered front-of-travel-first (legacy movement_order)
//   - per-drone frozen altitude (MissionPlan.AltitudeFor)
//   - arrival is judged PER DRONE against its own frozen offset target
//   - the shared index advances only when EVERY participant has arrived (barrier)
//
// It is gated behind EnableGroupedMultiAuthority(), which NO live profile token
// wires up (server.go only maps core-single / core-single-wait). The grouped-multi
// API dispatcher exists behind that in-process opt-in for PRE-FLIP testing, while
// production configuration and frontend eligibility still keep this scope on the
// Legacy Python executor until a separately reviewed authority-flip round.

// groupOffsetTargets ports legacy swarm_logic.group_goto_targets(keep_formation).
// It translates the group centroid onto (wpLat,wpLon) and moves every drone by the
// same vector, preserving the formation.  A single position degenerates to the raw
// waypoint (centroid == its own position), which is why single-drone authority can
// keep judging against the raw waypoint.  Summation runs over sorted ids so the
// result is deterministic regardless of map iteration order.
func groupOffsetTargets(positions map[uint32][2]float64, wpLat, wpLon float64) map[uint32][2]float64 {
	out := make(map[uint32][2]float64, len(positions))
	if len(positions) == 0 {
		return out
	}
	if len(positions) == 1 {
		for id := range positions {
			out[id] = [2]float64{wpLat, wpLon}
		}
		return out
	}
	ids := sortedPosIDs(positions)
	var sumLat, sumLon float64
	for _, id := range ids {
		sumLat += positions[id][0]
		sumLon += positions[id][1]
	}
	n := float64(len(ids))
	clat, clon := sumLat/n, sumLon/n
	dlat, dlon := wpLat-clat, wpLon-clon
	for _, id := range ids {
		p := positions[id]
		out[id] = [2]float64{p[0] + dlat, p[1] + dlon}
	}
	return out
}

// groupGotoOrder ports legacy app.py `_goto_order` + swarm_logic.movement_order:
// the drone furthest along the travel direction (centroid -> waypoint) is sent
// first, so the front of the group clears the airspace before the ones behind it.
// `known` are the positions we actually have; `participants` is the full sorted
// participant set.  Participants without a known position are appended last in id
// order (legacy: `order + [d for d in ids if d not in order]`).
func groupGotoOrder(known map[uint32][2]float64, participants []uint32, wpLat, wpLon float64) []uint32 {
	if len(participants) <= 1 || len(known) == 0 {
		return append([]uint32(nil), participants...)
	}
	ids := sortedPosIDs(known)
	var sumLat, sumLon float64
	for _, id := range ids {
		sumLat += known[id][0]
		sumLon += known[id][1]
	}
	n := float64(len(ids))
	clat, clon := sumLat/n, sumLon/n
	dlat, dlon := wpLat-clat, wpLon-clon
	if absf(dlat) < 1e-9 && absf(dlon) < 1e-9 {
		return append([]uint32(nil), participants...)
	}
	// Choose the dominant travel axis and the projection that measures "how far
	// along the travel direction" each drone is (x = lon, y = lat).
	var proj func(p [2]float64) float64
	if absf(dlon) >= absf(dlat) {
		if dlon > 0 { // RIGHT / east
			proj = func(p [2]float64) float64 { return p[1] }
		} else { // LEFT / west
			proj = func(p [2]float64) float64 { return -p[1] }
		}
	} else {
		if dlat > 0 { // FWD / north
			proj = func(p [2]float64) float64 { return p[0] }
		} else { // BWD / south
			proj = func(p [2]float64) float64 { return -p[0] }
		}
	}
	ordered := append([]uint32(nil), ids...)
	sort.Slice(ordered, func(i, j int) bool {
		pi, pj := proj(known[ordered[i]]), proj(known[ordered[j]])
		if pi != pj {
			return pi > pj // furthest along travel direction first
		}
		return ordered[i] < ordered[j] // tie-break: id ascending
	})
	// Append participants with no known position, in id order.
	inOrder := make(map[uint32]bool, len(ordered))
	for _, id := range ordered {
		inOrder[id] = true
	}
	for _, id := range participants {
		if !inOrder[id] {
			ordered = append(ordered, id)
		}
	}
	return ordered
}

func sortedPosIDs(m map[uint32][2]float64) []uint32 {
	ids := make([]uint32, 0, len(m))
	for id := range m {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

func absf(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

// PositionSample carries the authoritative age/validity of an FC telemetry
// position.  Age comes from fleet.Drone's existing last-message safety signal;
// reading a cached Snapshot therefore cannot manufacture a new observation time.
type PositionSample struct {
	Lat, Lon float64
	Age      time.Duration
	Valid    bool
}

// ParticipantRejection is the stable operator-visible identity of one rejected
// GROUPED participant command.
type ParticipantRejection struct {
	RunID   uint64
	Index   int
	DroneID uint32
	Reason  string
}

func validPosition(lat, lon float64) bool {
	return !math.IsNaN(lat) && !math.IsNaN(lon) &&
		!math.IsInf(lat, 0) && !math.IsInf(lon, 0) &&
		lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 &&
		!(lat == 0 && lon == 0)
}

// ValidNavigationPosition is the shared fail-closed coordinate predicate for
// mission geometry and API authority preflights.
func ValidNavigationPosition(lat, lon float64) bool {
	return validPosition(lat, lon)
}

// ValidateAuthorityGroupedMulti is the EXACT S09-A capability predicate.  It
// accepts a plan only when every required condition is true; any unsupported
// feature keeps the plan Python-owned.  It is deliberately separate from the
// single-drone validateAuthorityBase so widening multi-drone scope can never
// widen the single-drone V1/V2 scope (or vice versa).
//
// Round-1 supported: GROUPED, >= 2 participants, shared route, per-drone altitude.
// Round-1 deferred (must stay Python-owned): WAIT, payload/servo actions,
// rtl_after, SEPARATE, SWARM leader, WAVE.
func (p *MissionPlan) ValidateAuthorityGroupedMulti() error {
	if err := p.Validate(); err != nil {
		return err
	}
	if p.Mode != ModeGrouped {
		return fmt.Errorf("%w: mode %s", ErrAuthorityUnsupported, p.Mode)
	}
	// Count UNIQUE participants: [1,1] is a single drone, not a multi-drone group.
	if n := len(canonicalParticipants(p.Participants)); n < 2 {
		return fmt.Errorf("%w: multi-drone GROUPED requires >= 2 unique participants, got %d",
			ErrAuthorityUnsupported, n)
	}
	if p.RtlAfter || p.ReturnPolicy != ReturnNone {
		return fmt.Errorf("%w: rtl_after is deferred", ErrAuthorityUnsupported)
	}
	for _, wp := range p.sharedRoute().Points {
		if wp.Action != ActionNone {
			return fmt.Errorf("%w: payload action is deferred", ErrAuthorityUnsupported)
		}
		if wp.WaitSeconds != 0 {
			return fmt.Errorf("%w: multi-drone WAIT is deferred", ErrAuthorityUnsupported)
		}
	}
	return nil
}

// ClaimAuthorityGroupedGotos atomically claims the current shared waypoint for the
// whole group EXACTLY ONCE and returns one GotoIntent per participant, ordered
// front-of-travel-first, each targeting its frozen formation-offset point at its
// frozen altitude.  Repeated calls before the group advances return (nil,false,nil)
// — the group barrier prevents a duplicate send for the same index.  A terminal /
// non-running / cancelled / interrupted / failed run emits nothing, so a late claim
// after takeover can never write to the FC.
//
// Positions come from the freshest telemetry seen by Observe (run.lastPos); a
// participant with no known position falls back to the raw waypoint, matching
// legacy `wp_targets.setdefault(d, (wp.lat, wp.lon))`.
func (e *Engine) ClaimAuthorityGroupedGotos() ([]GotoIntent, bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if !e.authority || !e.authorityGroupedMulti || run == nil || run.State != StateRunning {
		return nil, false, nil
	}
	validationPlan := e.authorityValidationPlan(run.Plan)
	if err := validationPlan.ValidateAuthorityGroupedMulti(); err != nil {
		return nil, false, err
	}
	route := run.Plan.sharedRoute()
	idx := run.currentIndex
	if idx < 0 || idx >= len(route.Points) {
		return nil, false, nil
	}
	if run.groupClaimedIndex == idx {
		return nil, false, nil // barrier: already dispatched this index
	}
	wp := route.Points[idx]

	// Known positions for the centroid come from trusted, FRESH telemetry.  A
	// missing (never-seen) or stale position is treated as unavailable: that drone
	// falls back to the raw waypoint (legacy setdefault) instead of dragging the
	// centroid, and the reason is recorded for audit/observability.
	known := make(map[uint32][2]float64, len(run.Participants))
	now := e.clock()
	for _, id := range run.Participants {
		p, ok := run.lastPos[id]
		if !ok {
			continue // never seen -> unavailable
		}
		if e.posFreshness > 0 {
			at, seen := run.lastPosAt[id]
			if !seen || now.Sub(at) > e.posFreshness {
				continue // stale -> unavailable
			}
		}
		known[id] = p
	}
	offsets := groupOffsetTargets(known, wp.Lat, wp.Lon)

	// Freeze one target per participant (fallback to raw waypoint when unknown),
	// so per-drone arrival is judged against exactly what we commanded.
	run.groupTargets = make(map[uint32][2]float64, len(run.Participants))
	for _, id := range run.Participants {
		if t, ok := offsets[id]; ok {
			run.groupTargets[id] = t
		} else {
			run.groupTargets[id] = [2]float64{wp.Lat, wp.Lon}
		}
	}

	order := groupGotoOrder(known, run.Participants, wp.Lat, wp.Lon)
	intents := make([]GotoIntent, 0, len(order))
	for _, id := range order {
		t := run.groupTargets[id]
		intents = append(intents, GotoIntent{
			RunID:   run.RunID,
			Index:   idx,
			DroneID: id,
			Lat:     t[0],
			Lon:     t[1],
			Alt:     run.Plan.AltitudeFor(id, wp.Alt),
		})
	}
	run.groupClaimedIndex = idx
	return intents, true, nil
}

// groupTargetFor returns the frozen offset target a multi-drone GROUPED participant
// is currently commanded to, and whether arrival should be judged against it yet.
// Arrival is judged only after the group GOTOs for the current index were claimed
// (frozen), so a stray telemetry sample between advance and the next dispatch can
// never advance the barrier early.
func (r *Run) groupTargetFor(droneID uint32) (lat, lon float64, ready bool) {
	if r.groupTargets == nil || r.groupClaimedIndex != r.currentIndex {
		return 0, 0, false
	}
	t, ok := r.groupTargets[droneID]
	if !ok {
		return 0, 0, false
	}
	return t[0], t[1], true
}

// SeedPositions is the compatibility entry point for genuinely fresh samples.
func (e *Engine) SeedPositions(positions map[uint32][2]float64) {
	samples := make(map[uint32]PositionSample, len(positions))
	for id, p := range positions {
		samples[id] = PositionSample{Lat: p[0], Lon: p[1], Valid: validPosition(p[0], p[1])}
	}
	e.SeedPositionSamples(samples)
}

// SeedPositionSamples preserves authoritative FC sample age.  Re-reading an old
// fleet snapshot yields the same observedAt and cannot make it fresh.
func (e *Engine) SeedPositionSamples(samples map[uint32]PositionSample) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.State.IsTerminal() || run.lastPos == nil {
		return
	}
	now := e.clock()
	for id, sample := range samples {
		if !run.isParticipant(id) {
			continue
		}
		if !sample.Valid || !validPosition(sample.Lat, sample.Lon) || sample.Age < 0 {
			delete(run.lastPos, id)
			delete(run.lastPosAt, id)
			continue
		}
		observedAt := now.Add(-sample.Age)
		if prior, ok := run.lastPosAt[id]; ok && !observedAt.After(prior) {
			continue
		}
		run.lastPos[id] = [2]float64{sample.Lat, sample.Lon}
		run.lastPosAt[id] = observedAt
	}
}

// NoteParticipantRejected records a best-effort waypoint GOTO rejection for the
// current authoritative index. GROUPED-multi and SWARM_LEADER both inherit the
// Legacy behavior: a rejected GOTO is not retried and does not terminally fail the
// route; progress simply stalls until the missing arrival can never be observed.
// The identity checks make delayed results from an old run/index stale-safe.
func (e *Engine) NoteParticipantRejected(runID uint64, index int, droneID uint32, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.RunID != runID || run.State.IsTerminal() ||
		!run.isParticipant(droneID) || index != run.currentIndex {
		return false
	}
	claimed := false
	switch run.Plan.Mode {
	case ModeGrouped:
		claimed = run.groupClaimedIndex == index
	case ModeSwarmLeader:
		claimed = droneID == run.leaderID() && run.authorityClaimedIndex == index
	}
	if !claimed {
		return false
	}
	if run.groupRejects == nil {
		run.groupRejects = make(map[uint32]string)
	}
	if reason == "" {
		reason = "participant GOTO rejected"
	}
	run.groupRejects[droneID] = reason
	e.setState(run, run.State, "participant-rejected", OwnerCoreEvent,
		fmt.Sprintf("Drone %d GOTO rejected at waypoint %d: %s", droneID, index, reason))
	return true
}

// GroupRejections returns a copy of the current group index's per-participant
// rejections (droneID -> reason).  Observability/audit only.
func (e *Engine) GroupRejections() map[uint32]string {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil || len(e.run.groupRejects) == 0 {
		return nil
	}
	out := make(map[uint32]string, len(e.run.groupRejects))
	for id, r := range e.run.groupRejects {
		out[id] = r
	}
	return out
}
