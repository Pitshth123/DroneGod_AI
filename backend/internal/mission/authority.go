package mission

import "fmt"

// GotoIntent is a side-effect-free command intent emitted by the mission state
// machine for the F4 Core-authority adapter.  The mission package still never
// imports command/fleet/MAVLink; only the API layer may execute this intent via
// command.Service -> safety.Envelope.
type GotoIntent struct {
	RunID   uint64
	Index   int
	DroneID uint32
	Lat     float64
	Lon     float64
	Alt     float64
}

// HoldIntent is the F5 side-effect-free request to hold the authority drone at
// an arrived waypoint before the Core-owned WAIT deadline runs.
type HoldIntent struct {
	RunID   uint64
	Index   int
	DroneID uint32
}

// ClaimAuthorityGoto atomically claims the current waypoint exactly once for
// the first Core-authority scope.  Repeated wakeups/retries cannot produce a
// duplicate intent for the same run/index.  Unsupported plans return an error
// and must remain under Python authority.
func (e *Engine) ClaimAuthorityGoto() (GotoIntent, bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if !e.authority || e.authorityGroupedMulti || e.authoritySeparate || e.authoritySwarmLeader || run == nil || run.State != StateRunning {
		// Multi-drone GROUPED / SEPARATE / SWARM-leader use their own claim methods;
		// the single-drone claim stays inert so authority scopes can never both act.
		return GotoIntent{}, false, nil
	}
	var authorityErr error
	validationPlan := e.authorityValidationPlan(run.Plan)
	if e.authorityWait {
		authorityErr = validationPlan.ValidateAuthorityV2()
	} else {
		authorityErr = validationPlan.ValidateAuthorityV1()
	}
	if authorityErr != nil {
		return GotoIntent{}, false, authorityErr
	}
	route := run.Plan.sharedRoute()
	idx := run.currentIndex
	if idx < 0 || idx >= len(route.Points) {
		return GotoIntent{}, false, nil
	}
	if run.authorityClaimedIndex == idx {
		return GotoIntent{}, false, nil
	}

	droneID := run.Participants[0]
	wp := route.Points[idx]
	run.authorityClaimedIndex = idx
	return GotoIntent{
		RunID:   run.RunID,
		Index:   idx,
		DroneID: droneID,
		Lat:     wp.Lat,
		Lon:     wp.Lon,
		Alt:     run.Plan.AltitudeFor(droneID, wp.Alt),
	}, true, nil
}

// ClaimAuthorityHold atomically claims the active GROUPED WAIT exactly once.
// It is valid only while the run is WAITING; cancel/failsafe/terminal state
// therefore suppresses any late HOLD just like ClaimAuthorityGoto suppresses
// late GOTO intents.
func (e *Engine) ClaimAuthorityHold() (HoldIntent, bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if !e.authority || !e.authorityWait || run == nil || run.State != StateWaiting {
		return HoldIntent{}, false, nil
	}
	validationPlan := e.authorityValidationPlan(run.Plan)
	if err := validationPlan.ValidateAuthorityV2(); err != nil {
		return HoldIntent{}, false, err
	}
	w := run.waits[0]
	if w == nil || w.Index < 0 {
		return HoldIntent{}, false, nil
	}
	if run.authorityClaimedWaitIndex == w.Index {
		return HoldIntent{}, false, nil
	}
	run.authorityClaimedWaitIndex = w.Index
	return HoldIntent{
		RunID: run.RunID, Index: w.Index, DroneID: run.Participants[0],
	}, true, nil
}

// Fail terminates only the matching active run.  It is stale-safe so an old
// command result can never fail a newer mission.
func (e *Engine) Fail(runID uint64, reason string) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if run == nil || run.RunID != runID || run.State.IsTerminal() {
		return
	}
	if reason == "" {
		reason = "mission command failed"
	}
	run.waits = make(map[uint32]*WaitState)
	e.transition(run, StateFailed, "command-failed", OwnerSystem, reason)
}

// AuthorityDebugString is deliberately small and command-free; useful in logs
// without exposing the frozen plan internals.
func (i GotoIntent) AuthorityDebugString() string {
	return fmt.Sprintf("run=%d wp=%d drone=%d", i.RunID, i.Index, i.DroneID)
}
