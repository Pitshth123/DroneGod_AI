package api

import (
	"context"
	"fmt"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/mission"
)

// V3-S09-A multi-drone GROUPED command dispatch — PRE-FLIP, OFF by default.
//
// This is the authority-flip scheduler: it turns the Core engine's per-index group
// GOTO intents into real per-drone sends through command.Service/safety.  It is
// reached ONLY when the engine's grouped-multi authority opt-in is enabled, which
// no live profile token sets (server.go maps only core-single / core-single-wait),
// so production never executes any of this.  It is built + tested now so the
// takeover/guard/stagger contract is proven before the flip is reviewed.
//
// Design contract (mirrors the single-drone path, per drone):
//   - front-of-travel order comes from the engine claim (groupGotoOrder)
//   - ~150 ms intentional launch spacing between drones (missionGroupStagger)
//   - each drone's send is independently cancellable + final-write guarded
//   - the ownership lock is NOT held during the stagger delay or the FC ACK
//   - a per-drone rejection is best-effort (records, does not fail the whole run)

const missionGroupStagger = 150 * time.Millisecond

// missionGroupSend is one participant's claimed GROUPED GOTO awaiting/executing its
// send, with its own cancel + guard so a takeover on this drone stops only this
// send.  gen guards single-flight per group index.
type missionGroupSend struct {
	runID         uint64
	index         int
	droneID       uint32
	lat, lon, alt float64
	ctx           context.Context
	cancel        context.CancelFunc
	guard         *missionSendGuard
	gen           uint64
	delay         time.Duration
}

func (s *Server) staggerLocked() time.Duration {
	if s.missionStagger > 0 {
		return s.missionStagger
	}
	return missionGroupStagger
}

// dispatchGroupedMultiAuthority claims the current group index and launches one
// staggered, cancellable, guarded send per participant.  OFF unless the engine's
// grouped-multi opt-in is enabled.
func (s *Server) dispatchGroupedMultiAuthority() {
	if !s.missionAuthority || s.mission == nil || s.missionExec == nil {
		return
	}
	// Seed the centroid from trusted fleet telemetry so the first WP does not
	// collapse onto the raw waypoint before the observe loop has ticked.
	s.seedMissionPositionsFromFleet()

	s.missionDispatchMu.Lock()
	sends, ok := s.claimGroupSendsLocked()
	s.missionDispatchMu.Unlock()
	if !ok {
		_ = s.persistMissionState(false)
		return
	}
	for _, gs := range sends {
		s.launchGroupSend(gs)
	}
}

// claimGroupSendsLocked claims the next group index (exactly once) and arms a
// per-drone ctx+guard for each participant.  Caller holds missionDispatchMu.
func (s *Server) claimGroupSendsLocked() ([]*missionGroupSend, bool) {
	if !s.missionAuthority || s.mission == nil || s.missionExec == nil {
		return nil, false
	}
	// Single-flight per group index: never claim the next index while any per-drone
	// send from the current one is still pending/in-flight.
	if len(s.missionGroupSends) > 0 {
		return nil, false
	}
	intents, ok, err := s.mission.ClaimAuthorityGroupedGotos()
	if err != nil {
		// A validation/eligibility error is a fail-closed condition for the whole
		// run (stale-safe); it is not a per-participant rejection.
		if snap := s.mission.Snapshot(); snap.Active {
			s.mission.Fail(snap.RunID, err.Error())
		}
		return nil, false
	}
	if !ok || len(intents) == 0 {
		return nil, false
	}
	base := s.ctx
	if base == nil {
		base = context.Background()
	}
	s.missionGroupGen++
	gen := s.missionGroupGen
	s.missionGroupSends = make(map[uint32]*missionGroupSend, len(intents))
	sends := make([]*missionGroupSend, 0, len(intents))
	stagger := s.staggerLocked()
	for i, in := range intents {
		ctx, cancel := context.WithCancel(base)
		guard := &missionSendGuard{}
		ctx = fleet.WithSendGuard(ctx, guard)
		gs := &missionGroupSend{
			runID: in.RunID, index: in.Index, droneID: in.DroneID,
			lat: in.Lat, lon: in.Lon, alt: in.Alt,
			ctx: ctx, cancel: cancel, guard: guard, gen: gen,
			delay: time.Duration(i) * stagger,
		}
		s.missionGroupSends[in.DroneID] = gs
		sends = append(sends, gs)
	}
	return sends, true
}

// launchGroupSend schedules a single participant send after its stagger delay.  A
// test scheduler (missionSchedule) captures the pending send instead of really
// waiting; production uses time.AfterFunc.  The send re-checks ctx before writing,
// so a takeover during the delay aborts it.
func (s *Server) launchGroupSend(gs *missionGroupSend) {
	fire := func() { s.runGroupSend(gs) }
	s.missionDispatchMu.Lock()
	sched := s.missionSchedule
	s.missionDispatchMu.Unlock()
	if sched != nil {
		sched(gs.delay, fire)
		return
	}
	if gs.delay <= 0 {
		go fire()
		return
	}
	time.AfterFunc(gs.delay, fire)
}

// runGroupSend performs one participant's GOTO with the ownership lock released.
// If the send was cancelled (takeover) before/while pending, it never writes.  A
// genuine rejection is recorded as best-effort — it does not fail the whole run.
func (s *Server) runGroupSend(gs *missionGroupSend) {
	defer gs.cancel()
	if gs.ctx.Err() != nil {
		s.clearGroupSend(gs)
		return // cancelled before firing -> pending GOTO must never write
	}
	err := s.missionExec.Goto(gs.ctx, gs.droneID, gs.lat, gs.lon, gs.alt)

	s.missionDispatchMu.Lock()
	preempted := gs.ctx.Err() != nil
	if s.missionGroupGen == gs.gen {
		delete(s.missionGroupSends, gs.droneID)
	}
	s.missionDispatchMu.Unlock()

	if err != nil && !preempted {
		// Legacy best-effort: the group barrier keeps waiting for this drone; the
		// rejection is recorded/auditable and the run is NOT failed.
		if s.mission.NoteParticipantRejected(gs.runID, gs.index, gs.droneID, err.Error()) &&
			s.events != nil {
			s.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, gs.droneID, "mission-participant-rejection",
				fmt.Sprintf("run_id=%d waypoint=%d drone_id=%d reason=%s",
					gs.runID, gs.index, gs.droneID, err.Error()))
		}
		_ = s.persistMissionState(true)
	}
	s.dispatchReturnAuthority()
}

func (s *Server) clearGroupSend(gs *missionGroupSend) {
	s.missionDispatchMu.Lock()
	if s.missionGroupGen == gs.gen {
		delete(s.missionGroupSends, gs.droneID)
	}
	s.missionDispatchMu.Unlock()
}

// cancelGroupSendsLocked cancels every pending/in-flight per-drone group send so an
// operator takeover / CancelMission / failsafe stops them before they reach the FC.
// Caller holds missionDispatchMu.  Bumping the generation makes any still-unwinding
// send a no-op so it cannot clear a newer claim's slot.
func (s *Server) cancelGroupSendsLocked() {
	if len(s.missionGroupSends) == 0 {
		s.missionGroupGen++
		return
	}
	for _, gs := range s.missionGroupSends {
		if gs.guard != nil {
			gs.guard.Cancel()
		}
		if gs.cancel != nil {
			gs.cancel()
		}
	}
	s.missionGroupSends = nil
	s.missionGroupGen++
}

// missionPositionSamples returns current navigation samples without mutating
// mission state. Both GROUPED centroid seeding and SEPARATE collision preflight
// use this single production source so neither can accidentally fall back to
// snapshot-read time as a freshness signal.
func (s *Server) missionPositionSamples() map[uint32]mission.PositionSample {
	var samples map[uint32]mission.PositionSample
	switch {
	case s.missionPosSampleProvider != nil:
		samples = s.missionPosSampleProvider()
	case s.missionPosProvider != nil:
		fresh := s.missionPosProvider()
		samples = make(map[uint32]mission.PositionSample, len(fresh))
		for id, p := range fresh {
			samples[id] = mission.PositionSample{Lat: p[0], Lon: p[1], Valid: true}
		}
	case s.mgr != nil:
		samples = make(map[uint32]mission.PositionSample)
		for _, id := range s.mgr.IDs() {
			d := s.mgr.Drone(id)
			if d == nil {
				continue
			}
			st := d.SafetyState()
			samples[id] = missionPositionSampleFromSafety(st)
		}
	}
	return samples
}

// seedMissionPositionsFromFleet preserves the authoritative FC sample age and
// GPS validity from fleet.Drone.SafetyState. Snapshot read time is never used.
func (s *Server) seedMissionPositionsFromFleet() {
	if s.mission == nil {
		return
	}
	if samples := s.missionPositionSamples(); len(samples) > 0 {
		s.mission.SeedPositionSamples(samples)
	}
}
