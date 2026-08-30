package mission

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/swarmgod/backend/pkg/geo"
)

// Clock returns the current time; injectable so WAIT deadlines are deterministic
// in tests (no real waiting), mirroring the frontend _wp_clock pattern.
type Clock func() time.Time

var (
	// ErrActiveRun is returned when Start is called with a different plan while a
	// run is already active (contract B4: no two missions at once).
	ErrActiveRun        = errors.New("mission: another run is already active")
	ErrRecoveryRequired = errors.New("mission: unfinished persisted run requires operator recovery")
	ErrStaleOperation   = errors.New("mission: operation_id belongs to a previous run")
	ErrInvalidRunID     = errors.New("mission: run_id must be monotonic and non-zero")
)

// WaitState is an active HOLD/WAIT at a waypoint (contract B1 wait metadata).
type WaitState struct {
	Scope    uint32 // 0 = grouped/leader scope; otherwise a drone id (SEPARATE)
	Index    int
	Deadline time.Time
	TotalS   int
}

// Run is the live state of one mission (contract B3 run identity).  All fields
// are owned by the Engine and only mutated under its lock.
type Run struct {
	RunID         uint64
	Plan          MissionPlan // frozen at Start
	OperationID   string      // idempotency key of the Start that created this run
	State         State
	Revision      uint64
	Participants  []uint32
	createdUnixMs int64

	// S10 recovery metadata is durable evidence only. Active timers, command
	// claims, contexts, generation tokens and transport guards are never restored.
	recoveryRequired      bool
	recoveryPreviousState State
	recoveryReason        string
	recoveryWaits         []WaitSnapshot
	originSessionID       string

	returnPolicy       ReturnPolicy
	returnState        ReturnState
	returnParticipants []uint32
	returnReason       string
	returnUpdatedAtMs  int64

	// S09-C SWARM_LEADER membership is mutable only inside this run. The Plan
	// remains frozen evidence of the original operator order/leader.
	currentLeaderID           uint32
	swarmOriginalParticipants []uint32
	activeParticipants        []uint32
	excludedParticipants      []uint32
	swarmGeneration           uint64
	successionPending         bool
	successionReason          string
	leaderObservationAfter    time.Time

	// GROUPED / SWARM_LEADER
	currentIndex              int
	arrived                   map[uint32]bool
	authorityClaimedIndex     int // F4: last waypoint claimed for Core GOTO; -1 = none
	authorityClaimedWaitIndex int // F5: last WAIT claimed for Core HOLD; -1 = none

	// S09-A multi-drone GROUPED authority (nil / -1 unless that authority mode is
	// enabled and a group index has been claimed).  groupTargets are the frozen
	// per-drone formation-offset points the group was last commanded to; arrival is
	// judged against these, not the raw waypoint.  lastPos is the freshest observed
	// position per participant, used to compute the centroid at claim time.
	groupClaimedIndex int
	groupTargets      map[uint32][2]float64
	lastPos           map[uint32][2]float64
	lastPosAt         map[uint32]time.Time // when each participant position was last seeded (freshness)

	// groupRejects records per-participant command rejections for the current group
	// index (droneID -> reason).  A rejected participant GOTO does NOT fail the run
	// (legacy stays best-effort): the group barrier simply keeps waiting for that
	// drone.  Cleared when the group advances.  Observability/audit only.
	groupRejects map[uint32]string

	// SEPARATE
	sepIndex        map[uint32]int
	sepClaimedIndex map[uint32]int // S09-B: last index dispatched per drone; -1 = none

	waits map[uint32]*WaitState // scope -> wait (0 = grouped)

	lastTransition Transition
	terminalReason string
	history        []Transition
}

// Engine owns mission state only. Even when Core authority is enabled it emits
// side-effect-free intents; the API adapter executes them through command.Service
// after the Engine lock is released. The mission package never imports the
// fleet/command/MAVLink send path.
type Engine struct {
	clock                 Clock
	mu                    sync.Mutex
	run                   *Run
	nextRunID             uint64
	authority             bool
	authorityWait         bool // F5 opt-in; F4 core-single keeps WAIT ineligible
	authorityGroupedMulti bool // S09-A opt-in; separate from single-drone V1/V2 scope
	authoritySeparate     bool // S09-B opt-in; separate from all other authority scopes
	authoritySwarmLeader  bool // S09-C opt-in; leader-only, followers owned by swarm mgr
	returnPolicyPreFlip   bool // S09-F in-process opt-in; no deployment token maps here
	authorityScope        string
	recoveryIssue         *RecoveryIssue

	// posFreshness bounds how old a seeded participant position may be before it is
	// treated as unavailable when computing a GROUPED formation centroid.  A missing
	// or stale position falls back to the raw waypoint for that drone (legacy
	// setdefault) and is recorded so it is distinguishable from a never-seen one.
	posFreshness time.Duration
}

// DefaultPositionFreshness is the default staleness bound for GROUPED centroid
// positions.  It is a Core policy value, not a UI throttle.
const DefaultPositionFreshness = 3 * time.Second

// NewEngine returns a shadow engine.  If clock is nil, time.Now is used.
func NewEngine(clock Clock) *Engine {
	if clock == nil {
		clock = time.Now
	}
	return &Engine{clock: clock, posFreshness: DefaultPositionFreshness}
}

// SetPositionFreshness overrides the GROUPED centroid position staleness bound.
// A non-positive value disables the freshness check (any seeded position counts).
func (e *Engine) SetPositionFreshness(d time.Duration) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.posFreshness = d
}

// AuthorityGroupedMultiEnabled reports whether the S09-A multi-drone GROUPED
// authority opt-in is active on this engine.  Used by the API adapter to route to
// the (test-only, OFF-by-default) multi-send path instead of the single-drone one.
func (e *Engine) AuthorityGroupedMultiEnabled() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.authority && e.authorityGroupedMulti
}

// EnableAuthority opts this engine into the guarded Core-authority state path.
// It only changes state/eligibility behavior; command execution stays outside
// this package and consumes ClaimAuthorityGoto intents through the API adapter.
func (e *Engine) EnableAuthority() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.authority = true
	e.authorityWait = false
	e.authorityScope = "core-single"
}

// EnableWaitAuthority is the separate F5 opt-in. It preserves the F4 boundary:
// core-single cannot silently gain WAIT/HOLD behavior just because code exists.
func (e *Engine) EnableWaitAuthority() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.authority = true
	e.authorityWait = true
	e.authorityScope = "core-single-wait"
}

// EnableGroupedMultiAuthority is the S09-A opt-in for multi-drone GROUPED. It is a
// SEPARATE, exact scope: no live profile token wires it up (server.go only maps
// core-single / core-single-wait), so enabling it is currently test/harness-only.
// It deliberately does NOT set authorityWait — round-1 multi-drone GROUPED is
// no-WAIT; WAIT/actions/rtl_after stay Python-owned via ValidateAuthorityGroupedMulti.
func (e *Engine) EnableGroupedMultiAuthority() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.authority = true
	e.authorityGroupedMulti = true
	e.authorityScope = "core-grouped-multi"
}

// EnableSeparateAuthority is the S09-B opt-in for SEPARATE (per-drone independent
// routes).  Like the other multi-drone opt-ins, no live profile token wires it up.
// WAIT/actions/rtl_after stay Python-owned via ValidateAuthoritySeparate.
func (e *Engine) EnableSeparateAuthority() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.authority = true
	e.authoritySeparate = true
	e.authorityScope = "core-separate"
}

// AuthoritySeparateEnabled reports whether the S09-B SEPARATE authority opt-in is
// active, so the API adapter can route to the (OFF-by-default) SEPARATE dispatcher.
func (e *Engine) AuthoritySeparateEnabled() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.authority && e.authoritySeparate
}

// EnableSwarmLeaderAuthority is the S09-C opt-in for the SWARM leader path.  No live
// profile token wires it up.  WAIT/actions/rtl_after stay Python-owned.
func (e *Engine) EnableSwarmLeaderAuthority() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.authority = true
	e.authoritySwarmLeader = true
	e.authorityScope = "core-swarm-leader"
}

// EnableReturnPolicyPreFlip allows deterministic in-process validation of S09-F.
// No environment/profile token calls this method; existing live scopes continue
// to reject rtl_after until a separately reviewed authority flip.
func (e *Engine) EnableReturnPolicyPreFlip() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil || e.run.State.IsTerminal() {
		e.returnPolicyPreFlip = true
	}
}

func (e *Engine) authorityValidationPlan(plan MissionPlan) MissionPlan {
	if e.returnPolicyPreFlip {
		plan.RtlAfter = false
		plan.ReturnPolicy = ReturnNone
	}
	return plan
}

// AuthoritySwarmLeaderEnabled reports whether the S09-C opt-in is active.
func (e *Engine) AuthoritySwarmLeaderEnabled() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.authority && e.authoritySwarmLeader
}

// AuthorityEnabled reports whether this engine was explicitly opted into Core
// waypoint authority. Eligibility remains plan-specific and is checked at Start.
func (e *Engine) AuthorityEnabled() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.authority
}

// ---- lifecycle ------------------------------------------------------------

// Start is StartOp with no operation id (idempotent by plan id only).
func (e *Engine) Start(plan MissionPlan) (uint64, error) {
	return e.StartOp(plan, "")
}

// StartOp validates and freezes a plan and uses the in-memory counter. Production
// S10 callers use StartOpWithRunID with a SQLite-allocated monotonic id.
func (e *Engine) StartOp(plan MissionPlan, operationID string) (uint64, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.startOpLocked(plan, operationID, 0)
}

// StartOpWithRunID starts a run with a durable, process-restart-safe id allocated
// by the Core store before navigation ownership is acquired.
func (e *Engine) StartOpWithRunID(plan MissionPlan, operationID string, runID uint64) (uint64, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if runID == 0 {
		return 0, ErrInvalidRunID
	}
	return e.startOpLocked(plan, operationID, runID)
}

func (e *Engine) startOpLocked(plan MissionPlan, operationID string, assignedRunID uint64) (uint64, error) {
	if e.recoveryIssue != nil || (e.run != nil && e.run.recoveryRequired) {
		return 0, ErrRecoveryRequired
	}
	if e.run != nil {
		if e.run.returnState.pendingAuthority() {
			return 0, ErrActiveRun
		}
		if !e.run.State.IsTerminal() {
			if (operationID != "" && e.run.OperationID == operationID) ||
				e.run.Plan.PlanID == plan.PlanID {
				return e.run.RunID, nil // idempotent re-Start in this process
			}
			return 0, ErrActiveRun
		}
		if operationID != "" && e.run.OperationID == operationID {
			return 0, ErrStaleOperation
		}
	}
	var err error
	plan, err = plan.resolveReturnPolicy()
	if err != nil {
		return 0, err
	}
	if err := plan.Validate(); err != nil {
		return 0, err
	}
	if e.authority {
		// Existing predicates intentionally reject Return. This test-only gate
		// proves the resolved policy without widening any live token.
		validationPlan := e.authorityValidationPlan(plan)
		var authorityErr error
		switch {
		case e.authorityGroupedMulti:
			authorityErr = validationPlan.ValidateAuthorityGroupedMulti()
		case e.authoritySeparate:
			authorityErr = validationPlan.ValidateAuthoritySeparate()
		case e.authoritySwarmLeader:
			authorityErr = validationPlan.ValidateAuthoritySwarmLeader()
		case e.authorityWait:
			authorityErr = validationPlan.ValidateAuthorityV2()
		default:
			authorityErr = validationPlan.ValidateAuthorityV1()
		}
		if authorityErr != nil {
			return 0, authorityErr
		}
	}

	if assignedRunID == 0 {
		e.nextRunID++
		assignedRunID = e.nextRunID
	} else {
		if assignedRunID <= e.nextRunID {
			return 0, ErrInvalidRunID
		}
		e.nextRunID = assignedRunID
	}
	plan = plan.clone()
	participants := canonicalParticipants(plan.Participants)
	run := &Run{
		RunID:                     assignedRunID,
		Plan:                      plan,
		OperationID:               operationID,
		State:                     StateIdle,
		Participants:              participants,
		createdUnixMs:             e.clock().UnixMilli(),
		arrived:                   make(map[uint32]bool),
		authorityClaimedIndex:     -1,
		authorityClaimedWaitIndex: -1,
		groupClaimedIndex:         -1,
		lastPos:                   make(map[uint32][2]float64),
		lastPosAt:                 make(map[uint32]time.Time),
		groupRejects:              make(map[uint32]string),
		sepIndex:                  make(map[uint32]int),
		sepClaimedIndex:           make(map[uint32]int),
		waits:                     make(map[uint32]*WaitState),
		returnPolicy:              plan.ReturnPolicy,
		returnState:               ReturnStateInactive,
		returnParticipants:        append([]uint32(nil), participants...),
	}
	if plan.Mode == ModeSwarmLeader {
		run.currentLeaderID = plan.LeaderID
		run.swarmOriginalParticipants = orderedParticipants(plan.Participants)
		run.activeParticipants = append([]uint32(nil), run.swarmOriginalParticipants...)
	}
	e.run = run

	e.transition(run, StateValidating, "validate", OwnerSystem, "plan accepted")
	e.transition(run, StateReady, "ready", OwnerSystem, "plan frozen")
	e.transition(run, StateRunning, "start", OwnerOperator, "mission started")
	if run.Plan.Mode == ModeSeparate {
		for _, id := range participants {
			run.sepIndex[id] = 0
			run.sepClaimedIndex[id] = -1
		}
	}
	return run.RunID, nil
}

// Cancel is stale-safe and idempotent (contract B5).  Cancelling a run_id that
// is not the active run is a no-op — it never touches a newer run.
func (e *Engine) Cancel(runID uint64) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.RunID != runID {
		return nil // stale / unknown cancel: no authority over any run
	}
	if run.returnState.pendingAuthority() {
		e.suppressReturnLocked(run, "operator cancel")
		if run.State == StateCompleted {
			e.transition(run, StateCancelled, "cancelled-return", OwnerOperator, "operator cancel")
		}
		return nil
	}
	if run.State.IsTerminal() {
		return nil // already terminal: idempotent
	}
	// invalidate future transitions BEFORE anything else (fail-closed order, A4/B5)
	run.waits = make(map[uint32]*WaitState)
	e.transition(run, StateCancelling, "cancel", OwnerOperator, "operator cancel")
	e.transition(run, StateCancelled, "cancelled", OwnerOperator, "operator cancel")
	return nil
}

// Interrupt applies a Core failsafe preemption (battery/link) fail-closed
// (contract B7).  Only a participant of the active run interrupts it; the shadow
// issues no command (Core owns the failsafe RTL).  Mission does not auto-resume.
// It returns true only when this call actually transitioned the run to INTERRUPTED,
// so a caller can avoid cancelling in-flight mission sends for a failsafe that
// belongs to a non-participant (or a terminal run).
func (e *Engine) Interrupt(droneID uint32, category, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil {
		return false
	}
	if !run.isActiveParticipant(droneID) {
		return false
	}
	if run.returnState.pendingAuthority() {
		if reason == "" {
			reason = category + " failsafe"
		}
		e.suppressReturnLocked(run, reason)
		e.transition(run, StateInterrupted, "failsafe-return", OwnerFailsafe, reason)
		return true
	}
	if run.State.IsTerminal() {
		return false
	}
	// SWARM_LEADER progression belongs to the fixed leader.  A follower
	// battery/link failsafe is handled by fleet RTL + swarm.Manager excluding that
	// follower; it must not terminally interrupt the leader route.
	if run.Plan.Mode == ModeSwarmLeader && droneID != run.leaderID() {
		return false
	}
	run.waits = make(map[uint32]*WaitState)
	if reason == "" {
		reason = category + " failsafe"
	}
	e.transition(run, StateInterrupted, "failsafe", OwnerFailsafe, reason)
	return true
}

// ---- observations ---------------------------------------------------------

// Observe feeds a newly-received valid telemetry position.  Production code
// polling fleet snapshots uses ObservePositionSample so the FC message age is
// preserved; this compatibility entry point represents a genuinely fresh sample.
func (e *Engine) Observe(droneID uint32, lat, lon, alt float64) {
	e.ObservePositionSample(droneID, lat, lon, alt, 0, validPosition(lat, lon))
}

// ObservePositionSample applies a cached fleet sample without refreshing it.  A
// stale/invalid/no-GPS sample is unavailable both for centroid and arrival.
func (e *Engine) ObservePositionSample(droneID uint32, lat, lon, alt float64,
	age time.Duration, valid bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil {
		return
	}
	if !valid || !validPosition(lat, lon) || age < 0 {
		if !run.State.IsTerminal() && run.isActiveParticipant(droneID) {
			delete(run.lastPos, droneID)
			delete(run.lastPosAt, droneID)
		}
		return
	}
	now := e.clock()
	observedAt := now.Add(-age)
	if !run.State.IsTerminal() && run.lastPos != nil && run.isActiveParticipant(droneID) {
		// Polling the same cached snapshot only reproduces its original timestamp.
		// Never move freshness backwards if samples arrive out of order.
		if prior, ok := run.lastPosAt[droneID]; !ok || observedAt.After(prior) {
			run.lastPos[droneID] = [2]float64{lat, lon}
			run.lastPosAt[droneID] = observedAt
		}
	}
	if run.State != StateRunning || run.successionPending ||
		(e.posFreshness > 0 && age > e.posFreshness) {
		return
	}
	if run.Plan.Mode == ModeSwarmLeader && droneID == run.leaderID() &&
		!run.leaderObservationAfter.IsZero() && !observedAt.After(run.leaderObservationAfter) {
		// Cached/pre-promotion leader evidence cannot complete the current WP.
		// The boundary is deliberately EXCLUSIVE: a sample sharing the promotion's
		// exact timestamp is indistinguishable from evidence captured just before
		// the handoff, so it is rejected. Stalling until the next fresh sample is
		// the safe direction; advancing on ambiguous evidence is not.
		return
	}
	switch run.Plan.Mode {
	case ModeGrouped, ModeSwarmLeader:
		e.observeGrouped(run, droneID, lat, lon)
	case ModeSeparate:
		e.observeSeparate(run, droneID, lat, lon)
	}
}

func (e *Engine) observeGrouped(run *Run, droneID uint32, lat, lon float64) {
	// Only arrival-relevant participants count (SWARM_LEADER = leader only).
	if !run.isArrivalParticipant(droneID) || run.arrived[droneID] {
		return
	}
	route := run.Plan.sharedRoute()
	if run.currentIndex >= len(route.Points) {
		return
	}
	wp := route.Points[run.currentIndex]
	// Multi-drone GROUPED authority judges arrival against the drone's frozen
	// formation-offset target (what it was actually commanded to); shadow / single
	// / SWARM_LEADER keep judging against the raw shared waypoint.
	tlat, tlon := wp.Lat, wp.Lon
	if e.authorityGroupedMulti {
		gLat, gLon, ready := run.groupTargetFor(droneID)
		if !ready {
			return // GOTOs for this index not dispatched yet — no arrival to judge
		}
		tlat, tlon = gLat, gLon
	}
	if geo.HaversineM(lat, lon, tlat, tlon) > run.Plan.arrivalRadius() {
		return
	}
	run.arrived[droneID] = true
	// GROUPED advances only when all arrival participants have arrived.
	for _, id := range run.arrivalParticipantIDs() {
		if !run.arrived[id] {
			return
		}
	}
	e.reachWaypoint(run, 0, run.currentIndex, wp)
}

func (e *Engine) observeSeparate(run *Run, droneID uint32, lat, lon float64) {
	if !run.isParticipant(droneID) {
		return
	}
	if _, waiting := run.waits[droneID]; waiting {
		return // this drone is holding at a WAIT; deadline (Poll) advances it
	}
	route, ok := run.Plan.routeFor(droneID)
	if !ok {
		return
	}
	idx := run.sepIndex[droneID]
	if idx >= len(route.Points) {
		return // this drone finished its route
	}
	wp := route.Points[idx]
	if geo.HaversineM(lat, lon, wp.Lat, wp.Lon) > run.Plan.arrivalRadius() {
		return
	}
	e.reachWaypoint(run, droneID, idx, wp)
}

// reachWaypoint is called when the waypoint at scope/index is reached.  If it
// carries a WAIT it enters WAITING for that scope; otherwise it advances
// immediately (the payload action is modelled as instantaneous in the shadow —
// no servo command is issued; only progression/timing of WAIT is modelled).
func (e *Engine) reachWaypoint(run *Run, scope uint32, index int, wp Waypoint) {
	if wp.WaitSeconds > 0 {
		run.waits[scope] = &WaitState{
			Scope:    scope,
			Index:    index,
			Deadline: e.clock().Add(time.Duration(wp.WaitSeconds) * time.Second),
			TotalS:   wp.WaitSeconds,
		}
		// Only flip the mission to WAITING when nothing else is transiting; in
		// SEPARATE another drone may still be flying while this one holds. Either
		// way a newly entered WAIT is operator-visible recovery evidence and MUST
		// create a durable revision boundary: without it a concurrent SEPARATE WAIT
		// would leave the run RUNNING at an unchanged revision and the Server would
		// skip the SQLite write, losing the hold on restart.
		if !run.hasTransiting() {
			e.setState(run, StateWaiting, "arrive-wait", OwnerTelemetry,
				waitReason(scope, index, wp.WaitSeconds))
		} else {
			e.setState(run, StateRunning, "arrive-wait", OwnerTelemetry,
				waitReason(scope, index, wp.WaitSeconds))
		}
		return
	}
	e.advance(run, scope, index)
}

// Poll checks active WAIT deadlines against the clock and advances any that are
// due (mirrors the frontend _wp_wait_tick; the timer is only a poll trigger, not
// authority).  Returns true if any transition happened.
func (e *Engine) Poll() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.State.IsTerminal() || len(run.waits) == 0 {
		return false
	}
	now := e.clock()
	changed := false
	for _, scope := range sortedScopes(run.waits) {
		w := run.waits[scope]
		if w == nil {
			continue
		}
		if !now.Before(w.Deadline) { // now >= deadline
			delete(run.waits, scope)
			e.advance(run, scope, w.Index)
			changed = true
		}
	}
	if changed {
		run.recomputeActiveState(e)
	}
	return changed
}

// advance moves the given scope past `fromIndex` to the next waypoint, or marks
// completion. For GROUPED it advances the shared index; for SEPARATE the drone.
// Command dispatch is deliberately outside this state lock; the API observer
// claims the next side-effect-free intent after progression completes.
func (e *Engine) advance(run *Run, scope uint32, fromIndex int) {
	switch run.Plan.Mode {
	case ModeGrouped, ModeSwarmLeader:
		run.arrived = make(map[uint32]bool)
		// Force the next index to re-freeze its offset targets before arrival is
		// judged again (no-op for shadow/single: groupTargets is already nil).
		run.groupTargets = nil
		if len(run.groupRejects) > 0 {
			run.groupRejects = make(map[uint32]string)
		}
		run.currentIndex = fromIndex + 1
		if run.currentIndex >= len(run.Plan.sharedRoute().Points) {
			e.transition(run, StateCompleted, "complete", OwnerSystem, "all waypoints reached")
			e.armNaturalReturnLocked(run)
			return
		}
		e.setState(run, StateRunning, "advance", OwnerSystem,
			indexReason("WP", run.currentIndex))
	case ModeSeparate:
		run.sepIndex[scope] = fromIndex + 1
		if run.allSeparateDone() {
			e.transition(run, StateCompleted, "complete", OwnerSystem, "all routes finished")
			e.armNaturalReturnLocked(run)
			return
		}
		// SEPARATE per-drone index progression is operator-visible mission progress
		// and MUST create a durable revision boundary, even though the run stays
		// RUNNING while other drones keep flying. recomputeActiveState alone only
		// bumps the revision when the RUNNING/WAITING label changes, so a lone
		// drone advance would otherwise leave Revision unchanged and the Server
		// would skip the SQLite write — losing the advanced sepIndex on restart.
		// Record the advance as an explicit transition to the reconciled active
		// state (WAITING only when every still-unfinished scope is now holding).
		next, owner := StateRunning, OwnerSystem
		if !run.hasTransiting() && len(run.waits) > 0 {
			next, owner = StateWaiting, OwnerTelemetry
		}
		e.setState(run, next, "advance", owner, sepIndexReason(scope, run.sepIndex[scope]))
	}
}

// ---- snapshot -------------------------------------------------------------

// WaitSnapshot is a read-only view of one active WAIT (contract B6).
type WaitSnapshot struct {
	Scope      uint32
	Index      int
	RemainingS float64
	TotalS     int
}

// Snapshot is the GetMissionState shadow response (contract B6).  It is what a
// reconnecting UI would read to rebuild the mission display.
type Snapshot struct {
	Active         bool
	Authority      bool
	RunID          uint64
	OperationID    string
	PlanID         string
	Plan           MissionPlan
	Mode           Mode
	State          State
	Revision       uint64
	Participants   []uint32
	CurrentIndex   int            // GROUPED / SWARM_LEADER
	SepIndex       map[uint32]int // SEPARATE
	Arrived        []uint32
	Waits          []WaitSnapshot
	Rejections     []ParticipantRejection
	LastTransition Transition
	TerminalReason string

	RecoveryRequired      bool
	RecoveryCompatible    bool
	RecoveryPreviousState State
	RecoveryReason        string
	PersistenceSchema     uint32
	PersistedRevision     uint64
	AuthorityScope        string
	OriginSessionID       string
	CoreSessionID         string
	PersistenceFault      string
	ReturnPolicy          ReturnPolicy
	ReturnState           ReturnState
	ReturnParticipants    []uint32
	ReturnReason          string
	CurrentLeaderID       uint32
	ActiveParticipants    []uint32
	ExcludedParticipants  []uint32
	SwarmGeneration       uint64
	SuccessionPending     bool
	SuccessionReason      string
}

// Snapshot returns the current authoritative shadow state.
func (e *Engine) Snapshot() Snapshot {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil {
		if e.recoveryIssue != nil {
			return Snapshot{
				Active: false, Authority: false, RunID: e.recoveryIssue.RunID,
				State: StateInterrupted, TerminalReason: e.recoveryIssue.Reason,
				RecoveryRequired: true, RecoveryCompatible: false,
				RecoveryReason:    e.recoveryIssue.Reason,
				PersistenceSchema: e.recoveryIssue.SchemaVersion,
			}
		}
		return Snapshot{Active: false, State: StateIdle, RecoveryCompatible: true}
	}
	now := e.clock()
	snap := Snapshot{
		Active:                !run.State.IsTerminal() && !run.recoveryRequired,
		Authority:             e.authority && !run.State.IsTerminal() && !run.recoveryRequired,
		RunID:                 run.RunID,
		OperationID:           run.OperationID,
		PlanID:                run.Plan.PlanID,
		Plan:                  run.Plan.clone(),
		Mode:                  run.Plan.Mode,
		State:                 run.State,
		Revision:              run.Revision,
		Participants:          append([]uint32(nil), run.Participants...),
		CurrentIndex:          run.currentIndex,
		LastTransition:        run.lastTransition,
		TerminalReason:        run.terminalReason,
		RecoveryRequired:      run.recoveryRequired,
		RecoveryCompatible:    true,
		RecoveryPreviousState: run.recoveryPreviousState,
		RecoveryReason:        run.recoveryReason,
		PersistenceSchema:     DurableMissionSchemaVersion,
		AuthorityScope:        e.authorityScope,
		OriginSessionID:       run.originSessionID,
		ReturnPolicy:          run.returnPolicy,
		ReturnState:           run.returnState,
		ReturnParticipants:    append([]uint32(nil), run.returnParticipants...),
		ReturnReason:          run.returnReason,
		CurrentLeaderID:       run.leaderID(),
		ActiveParticipants:    append([]uint32(nil), run.activeParticipants...),
		ExcludedParticipants:  append([]uint32(nil), run.excludedParticipants...),
		SwarmGeneration:       run.swarmGeneration,
		SuccessionPending:     run.successionPending,
		SuccessionReason:      run.successionReason,
	}
	if run.Plan.Mode == ModeSeparate {
		snap.SepIndex = make(map[uint32]int, len(run.sepIndex))
		for k, v := range run.sepIndex {
			snap.SepIndex[k] = v
		}
	}
	for id, ok := range run.arrived {
		if ok {
			snap.Arrived = append(snap.Arrived, id)
		}
	}
	sort.Slice(snap.Arrived, func(i, j int) bool { return snap.Arrived[i] < snap.Arrived[j] })
	for id, reason := range run.groupRejects {
		snap.Rejections = append(snap.Rejections, ParticipantRejection{
			RunID: run.RunID, Index: run.currentIndex, DroneID: id, Reason: reason,
		})
	}
	sort.Slice(snap.Rejections, func(i, j int) bool {
		return snap.Rejections[i].DroneID < snap.Rejections[j].DroneID
	})
	if run.recoveryRequired {
		snap.Waits = append(snap.Waits, run.recoveryWaits...)
	} else {
		for _, scope := range sortedScopes(run.waits) {
			w := run.waits[scope]
			rem := w.Deadline.Sub(now).Seconds()
			if rem < 0 {
				rem = 0
			}
			snap.Waits = append(snap.Waits, WaitSnapshot{
				Scope: scope, Index: w.Index, RemainingS: rem, TotalS: w.TotalS,
			})
		}
	}
	return snap
}

// Active reports whether a non-terminal run exists.  Cheap check used by the
// server's observation ticker to skip work when no mission is running.
func (e *Engine) Active() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.run != nil && !e.run.State.IsTerminal()
}

// ReturnDispatchPending is separate from mission Active: natural completion
// retires mission navigation before Return may be claimed.
func (e *Engine) ReturnDispatchPending() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.run != nil && e.run.returnState == ReturnStatePending
}

func (e *Engine) ClaimReturnIntent() (ReturnIntent, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if !e.authority || !e.returnPolicyPreFlip || run == nil || run.State != StateCompleted ||
		run.returnState != ReturnStatePending {
		return ReturnIntent{}, false
	}
	e.transitionReturnLocked(run, ReturnStateReturning, "post-mission Return authority acquired")
	return ReturnIntent{
		RunID: run.RunID, OperationID: run.OperationID, Policy: run.returnPolicy,
		Participants: append([]uint32(nil), run.returnParticipants...),
		RequestID:    fmt.Sprintf("mission-return:%d", run.RunID),
	}, true
}

func (e *Engine) CompleteReturn(runID uint64) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil || e.run.RunID != runID || e.run.returnState != ReturnStateReturning {
		return false
	}
	e.transitionReturnLocked(e.run, ReturnStateCompleted, "Return command ownership completed")
	return true
}

func (e *Engine) FailReturn(runID uint64, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil || e.run.RunID != runID || !e.run.returnState.pendingAuthority() {
		return false
	}
	if reason == "" {
		reason = "Return command failed"
	}
	e.transitionReturnLocked(e.run, ReturnStateFailed, reason)
	return true
}

func (e *Engine) SuppressReturn(runID uint64, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil || e.run.RunID != runID || !e.run.returnState.pendingAuthority() {
		return false
	}
	e.suppressReturnLocked(e.run, reason)
	return true
}

func (e *Engine) armNaturalReturnLocked(run *Run) {
	if e.authority && e.returnPolicyPreFlip && run.returnPolicy != ReturnNone {
		e.transitionReturnLocked(run, ReturnStatePending, "natural mission completion")
	}
}

func (e *Engine) suppressReturnLocked(run *Run, reason string) {
	if reason == "" {
		reason = "Return invalidated"
	}
	e.transitionReturnLocked(run, ReturnStateSuppressed, reason)
}

func (e *Engine) transitionReturnLocked(run *Run, state ReturnState, reason string) {
	run.Revision++
	run.returnState = state
	run.returnReason = reason
	run.returnUpdatedAtMs = e.clock().UnixMilli()
}

// History returns a copy of the transition history (for shadow comparison/audit).
func (e *Engine) History() []Transition {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.run == nil {
		return nil
	}
	return append([]Transition(nil), e.run.history...)
}

// ---- internal helpers -----------------------------------------------------

// transition records a state change unconditionally (used for definite steps).
func (e *Engine) transition(run *Run, to State, event string, owner Owner, reason string) {
	run.Revision++
	tr := Transition{
		From: run.State, To: to, Event: event, Owner: owner, Reason: reason,
		Revision: run.Revision, AtUnixMs: e.clock().UnixMilli(),
	}
	run.State = to
	run.lastTransition = tr
	run.history = append(run.history, tr)
	if to.IsTerminal() {
		run.terminalReason = reason
	}
}

// setState records a transition only if the state actually changes; otherwise it
// still bumps revision so observers see progress (e.g. WP index advanced).
func (e *Engine) setState(run *Run, to State, event string, owner Owner, reason string) {
	e.transition(run, to, event, owner, reason)
}

func (r *Run) isParticipant(id uint32) bool {
	for _, p := range r.Participants {
		if p == id {
			return true
		}
	}
	return false
}

func (r *Run) isActiveParticipant(id uint32) bool {
	if r.Plan.Mode != ModeSwarmLeader {
		return r.isParticipant(id)
	}
	for _, participant := range r.activeParticipants {
		if participant == id {
			return true
		}
	}
	return false
}

// arrivalParticipantIDs are the drones whose arrival drives progression.
// GROUPED: all participants.  SWARM_LEADER: the leader only (followers are held
// by the Go swarm formation loop, not this engine — no duplicate authority).
func (r *Run) arrivalParticipantIDs() []uint32 {
	if r.Plan.Mode == ModeSwarmLeader {
		return []uint32{r.leaderID()}
	}
	return r.Participants
}

func (r *Run) isArrivalParticipant(id uint32) bool {
	for _, p := range r.arrivalParticipantIDs() {
		if p == id {
			return true
		}
	}
	return false
}

func (r *Run) allSeparateDone() bool {
	for _, id := range r.Participants {
		route, ok := r.Plan.routeFor(id)
		if !ok {
			return false // structurally invalid: never silently skip a participant
		}
		if r.sepIndex[id] < len(route.Points) {
			return false
		}
	}
	return true
}

// hasTransiting reports whether any not-yet-finished scope is currently flying
// (i.e. not holding at a WAIT).  GROUPED/SWARM: transiting unless the shared wait
// is active.  SEPARATE: transiting if any unfinished drone is not waiting.
func (r *Run) hasTransiting() bool {
	switch r.Plan.Mode {
	case ModeGrouped, ModeSwarmLeader:
		_, waiting := r.waits[0]
		return !waiting
	case ModeSeparate:
		for _, id := range r.Participants {
			route, ok := r.Plan.routeFor(id)
			if !ok {
				continue
			}
			if r.sepIndex[id] >= len(route.Points) {
				continue // finished
			}
			if _, waiting := r.waits[id]; !waiting {
				return true
			}
		}
		return false
	}
	return false
}

// recomputeActiveState sets RUNNING vs WAITING for a still-active run: WAITING
// only when nothing is transiting (all not-yet-finished scopes are holding).
func (r *Run) recomputeActiveState(e *Engine) {
	if r.State.IsTerminal() {
		return
	}
	if r.hasTransiting() {
		if r.State != StateRunning {
			e.setState(r, StateRunning, "resume", OwnerSystem, "progression")
		}
	} else if len(r.waits) > 0 {
		if r.State != StateWaiting {
			e.setState(r, StateWaiting, "wait", OwnerTelemetry, "all scopes holding")
		}
	}
}
