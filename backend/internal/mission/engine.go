package mission

import (
	"errors"
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
	ErrActiveRun = errors.New("mission: another run is already active")
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
	RunID        uint64
	Plan         MissionPlan // frozen at Start
	OperationID  string      // idempotency key of the Start that created this run
	State        State
	Revision     uint64
	Participants []uint32

	// GROUPED / SWARM_LEADER
	currentIndex int
	arrived      map[uint32]bool

	// SEPARATE
	sepIndex map[uint32]int

	waits map[uint32]*WaitState // scope -> wait (0 = grouped)

	lastTransition Transition
	terminalReason string
	history        []Transition
}

// Engine is the SHADOW mission model.  It holds at most one run and never sends
// flight commands (F2 constraint).  Safe for concurrent use.
type Engine struct {
	clock     Clock
	mu        sync.Mutex
	run       *Run
	nextRunID uint64
}

// NewEngine returns a shadow engine.  If clock is nil, time.Now is used.
func NewEngine(clock Clock) *Engine {
	if clock == nil {
		clock = time.Now
	}
	return &Engine{clock: clock}
}

// ---- lifecycle ------------------------------------------------------------

// Start is StartOp with no operation id (idempotent by plan id only).
func (e *Engine) Start(plan MissionPlan) (uint64, error) {
	return e.StartOp(plan, "")
}

// StartOp validates and freezes a plan, creates a Core-owned run_id, and moves
// to RUNNING (contract B4).  It is idempotent: re-Starting with the same
// operation_id (retry / double-click / reconnect) — or the same plan_id —
// returns the existing run instead of creating a second one.  A *different*
// plan while a run is active is rejected (no two missions at once).
func (e *Engine) StartOp(plan MissionPlan, operationID string) (uint64, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if e.run != nil && !e.run.State.IsTerminal() {
		if (operationID != "" && e.run.OperationID == operationID) ||
			e.run.Plan.PlanID == plan.PlanID {
			return e.run.RunID, nil // idempotent re-Start
		}
		return 0, ErrActiveRun
	}
	if err := plan.Validate(); err != nil {
		return 0, err
	}

	e.nextRunID++
	participants := append([]uint32(nil), plan.Participants...)
	sort.Slice(participants, func(i, j int) bool { return participants[i] < participants[j] })
	run := &Run{
		RunID:        e.nextRunID,
		Plan:         plan,
		OperationID:  operationID,
		State:        StateIdle,
		Participants: participants,
		arrived:      make(map[uint32]bool),
		sepIndex:     make(map[uint32]int),
		waits:        make(map[uint32]*WaitState),
	}
	e.run = run

	e.transition(run, StateValidating, "validate", OwnerSystem, "plan accepted")
	e.transition(run, StateReady, "ready", OwnerSystem, "plan frozen")
	e.transition(run, StateRunning, "start", OwnerOperator, "mission started")
	if run.Plan.Mode == ModeSeparate {
		for _, id := range participants {
			run.sepIndex[id] = 0
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
func (e *Engine) Interrupt(droneID uint32, category, reason string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.State.IsTerminal() {
		return
	}
	if !run.isParticipant(droneID) {
		return
	}
	run.waits = make(map[uint32]*WaitState)
	if reason == "" {
		reason = category + " failsafe"
	}
	e.transition(run, StateInterrupted, "failsafe", OwnerFailsafe, reason)
}

// ---- observations ---------------------------------------------------------

// Observe feeds a telemetry position for a drone.  This is where arrival is
// judged in the Core (the contract B8 / F1-D3 move away from browser-JS arrival):
// a drone arrives when its horizontal distance to its current target is within
// the plan arrival radius.  Observe never sends a command.
func (e *Engine) Observe(droneID uint32, lat, lon, alt float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.State != StateRunning {
		return // only judge arrival while actively transiting (not during WAIT/terminal)
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
	if geo.HaversineM(lat, lon, wp.Lat, wp.Lon) > run.Plan.arrivalRadius() {
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
		// SEPARATE another drone may still be flying while this one holds.
		if !run.hasTransiting() {
			e.setState(run, StateWaiting, "arrive-wait", OwnerTelemetry,
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
// completion.  For GROUPED it advances the shared index; for SEPARATE the drone.
func (e *Engine) advance(run *Run, scope uint32, fromIndex int) {
	switch run.Plan.Mode {
	case ModeGrouped, ModeSwarmLeader:
		run.arrived = make(map[uint32]bool)
		run.currentIndex = fromIndex + 1
		if run.currentIndex >= len(run.Plan.sharedRoute().Points) {
			e.transition(run, StateCompleted, "complete", OwnerSystem, "all waypoints reached")
			return
		}
		e.setState(run, StateRunning, "advance", OwnerSystem,
			indexReason("WP", run.currentIndex))
	case ModeSeparate:
		run.sepIndex[scope] = fromIndex + 1
		if run.allSeparateDone() {
			e.transition(run, StateCompleted, "complete", OwnerSystem, "all routes finished")
			return
		}
		run.recomputeActiveState(e)
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
	RunID          uint64
	PlanID         string
	Mode           Mode
	State          State
	Revision       uint64
	Participants   []uint32
	CurrentIndex   int            // GROUPED / SWARM_LEADER
	SepIndex       map[uint32]int // SEPARATE
	Arrived        []uint32
	Waits          []WaitSnapshot
	LastTransition Transition
	TerminalReason string
}

// Snapshot returns the current authoritative shadow state.
func (e *Engine) Snapshot() Snapshot {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil {
		return Snapshot{Active: false, State: StateIdle}
	}
	now := e.clock()
	snap := Snapshot{
		Active:         !run.State.IsTerminal(),
		RunID:          run.RunID,
		PlanID:         run.Plan.PlanID,
		Mode:           run.Plan.Mode,
		State:          run.State,
		Revision:       run.Revision,
		Participants:   append([]uint32(nil), run.Participants...),
		CurrentIndex:   run.currentIndex,
		LastTransition: run.lastTransition,
		TerminalReason: run.terminalReason,
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
	return snap
}

// Active reports whether a non-terminal run exists.  Cheap check used by the
// server's observation ticker to skip work when no mission is running.
func (e *Engine) Active() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.run != nil && !e.run.State.IsTerminal()
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

// arrivalParticipantIDs are the drones whose arrival drives progression.
// GROUPED: all participants.  SWARM_LEADER: the leader only (followers are held
// by the Go swarm formation loop, not this engine — no duplicate authority).
func (r *Run) arrivalParticipantIDs() []uint32 {
	if r.Plan.Mode == ModeSwarmLeader {
		leader := r.Plan.LeaderID
		if leader == 0 && len(r.Participants) > 0 {
			leader = r.Participants[0]
		}
		return []uint32{leader}
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
			continue
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
