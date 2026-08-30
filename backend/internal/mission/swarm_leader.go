package mission

import "fmt"

// V3-S09-C — SWARM Leader Path Core authority (PRE-FLIP, OFF by default).
//
// Legacy (app.py `_wp_advance` with `ids=[swarm_head]`): when a swarm is active the
// waypoint mission sends a GOTO ONLY to the Head/Leader at the RAW waypoint; the Go
// swarm formation loop drags the followers to keep formation.  So the mission is the
// navigation authority for the LEADER only, and the swarm.Manager is the authority
// for the FOLLOWERS — two authorities over DIFFERENT drones, never the same one.
// Progression is driven by the leader's arrival (the shadow engine already models
// this: arrivalParticipantIDs == leader only).
//
// Gated behind EnableSwarmLeaderAuthority(); no live profile token wires it up.
//
// Operator takeover policy is PRE-FLIP: a follower is excluded for the run; a
// leader is replaced by the next eligible active participant in the original
// operator order. The run/index are preserved and excluded members never rejoin.
// Battery/link leader failsafe semantics remain unchanged and still interrupt.

// leaderID is the current in-run leader. Plan.LeaderID remains frozen evidence
// of the originally selected Legacy Head.
//
// Once succession membership is tracked (every SWARM_LEADER run initialises it at
// Start and on durable restore), a zero currentLeaderID is the deliberate "no
// eligible successor remains" state, not an uninitialised one. It must NOT fall
// back to the frozen plan leader: that drone has been excluded/operator-controlled,
// and reporting it as the current leader would put a stale, operator-owned aircraft
// into snapshot and durable evidence. The fallback survives only for runs without
// swarm membership tracking.
func (r *Run) leaderID() uint32 {
	if r.currentLeaderID != 0 {
		return r.currentLeaderID
	}
	if len(r.swarmOriginalParticipants) != 0 {
		return 0
	}
	return r.Plan.LeaderID
}

// ValidateAuthoritySwarmLeader is the exact S09-C capability predicate.
//
// Supported: SWARM_LEADER, >= 2 unique participants (leader + >=1 follower), one
// shared route, per-leader altitude.
// Deferred (Python-owned): WAIT, actions, rtl_after.
func (p *MissionPlan) ValidateAuthoritySwarmLeader() error {
	if err := p.Validate(); err != nil { // also checks LeaderID is a participant
		return err
	}
	if p.Mode != ModeSwarmLeader {
		return fmt.Errorf("%w: mode %s", ErrAuthorityUnsupported, p.Mode)
	}
	if n := len(canonicalParticipants(p.Participants)); n < 2 {
		return fmt.Errorf("%w: SWARM_LEADER requires a leader + >=1 follower (>=2 participants), got %d",
			ErrAuthorityUnsupported, n)
	}
	if p.LeaderID == 0 {
		return fmt.Errorf("%w: SWARM_LEADER requires an explicit non-zero Legacy Head",
			ErrAuthorityUnsupported)
	}
	if p.RtlAfter || p.ReturnPolicy != ReturnNone {
		return fmt.Errorf("%w: rtl_after is deferred", ErrAuthorityUnsupported)
	}
	for _, wp := range p.sharedRoute().Points {
		if wp.Action != ActionNone {
			return fmt.Errorf("%w: payload action is deferred", ErrAuthorityUnsupported)
		}
		if wp.WaitSeconds != 0 {
			return fmt.Errorf("%w: SWARM_LEADER WAIT is deferred", ErrAuthorityUnsupported)
		}
	}
	return nil
}

// ClaimAuthoritySwarmLeaderGoto claims the current shared waypoint EXACTLY ONCE and
// returns a single GotoIntent for the LEADER only, at the raw waypoint and leader
// altitude.  Followers are never returned — the mission must never command a
// follower (the swarm formation loop owns them).  A terminal run emits nothing.
func (e *Engine) ClaimAuthoritySwarmLeaderGoto() (GotoIntent, bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if !e.authority || !e.authoritySwarmLeader || run == nil || run.State != StateRunning ||
		run.successionPending {
		return GotoIntent{}, false, nil
	}
	validationPlan := e.authorityValidationPlan(run.Plan)
	if err := validationPlan.ValidateAuthoritySwarmLeader(); err != nil {
		return GotoIntent{}, false, err
	}
	route := run.Plan.sharedRoute()
	idx := run.currentIndex
	if idx < 0 || idx >= len(route.Points) {
		return GotoIntent{}, false, nil
	}
	if run.authorityClaimedIndex == idx {
		return GotoIntent{}, false, nil // barrier: already dispatched this index
	}
	leader := run.leaderID()
	wp := route.Points[idx]
	run.authorityClaimedIndex = idx
	return GotoIntent{
		RunID:   run.RunID,
		Index:   idx,
		DroneID: leader,
		Lat:     wp.Lat,
		Lon:     wp.Lon,
		Alt:     run.Plan.AltitudeFor(leader, wp.Alt),
	}, true, nil
}

// FollowerIDs returns the non-leader participants — the drones the mission must
// NEVER command (they stay under the swarm formation manager).  Observability/audit
// and a guard the API adapter can assert against.
func (e *Engine) FollowerIDs() []uint32 {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.Plan.Mode != ModeSwarmLeader {
		return nil
	}
	leader := run.leaderID()
	var out []uint32
	for _, id := range run.activeParticipants {
		if id != leader {
			out = append(out, id)
		}
	}
	return out
}

// SwarmTakeoverTransition is the authority/membership handoff that the API must
// reconcile with swarm.Manager while missionDispatchMu prevents command claims.
type SwarmTakeoverTransition struct {
	RunID                uint64
	TargetID             uint32
	OldLeaderID          uint32
	NewLeaderID          uint32
	CurrentIndex         int
	Generation           uint64
	ActiveParticipants   []uint32
	ExcludedParticipants []uint32
	Interrupted          bool
	Duplicate            bool
	Reason               string
}

// BeginSwarmOperatorTakeover excludes targetID and, when necessary, selects the
// first eligible successor in original mission order. The caller must already
// have revoked the old leader's mission send guard. Progression is frozen until
// CompleteSwarmOperatorTakeover reconciles formation ownership.
func (e *Engine) BeginSwarmOperatorTakeover(targetID uint32,
	eligible func(uint32) bool) (SwarmTakeoverTransition, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if !e.authority || !e.authoritySwarmLeader || run == nil || run.State.IsTerminal() ||
		run.Plan.Mode != ModeSwarmLeader || targetID == 0 {
		return SwarmTakeoverTransition{}, false
	}
	if !run.isActiveParticipant(targetID) {
		return SwarmTakeoverTransition{RunID: run.RunID, TargetID: targetID, Duplicate: true}, true
	}
	oldLeader := run.leaderID()
	active := make([]uint32, 0, len(run.activeParticipants)-1)
	for _, id := range run.activeParticipants {
		if id != targetID {
			active = append(active, id)
		}
	}
	excludedSet := make(map[uint32]bool, len(run.excludedParticipants)+1)
	for _, id := range run.excludedParticipants {
		excludedSet[id] = true
	}
	excludedSet[targetID] = true
	excluded := make([]uint32, 0, len(excludedSet))
	for _, id := range run.swarmOriginalParticipants {
		if excludedSet[id] {
			excluded = append(excluded, id)
		}
	}
	newLeader := oldLeader
	if targetID == oldLeader {
		newLeader = 0
		for _, id := range run.swarmOriginalParticipants {
			if excludedSet[id] || !containsParticipant(active, id) {
				continue
			}
			if eligible == nil || eligible(id) {
				newLeader = id
				break
			}
		}
	}
	run.swarmGeneration++
	reason := fmt.Sprintf("operator takeover excluded Drone %d", targetID)
	transition := SwarmTakeoverTransition{
		RunID: run.RunID, TargetID: targetID, OldLeaderID: oldLeader,
		NewLeaderID: newLeader, CurrentIndex: run.currentIndex,
		Generation: run.swarmGeneration, ActiveParticipants: append([]uint32(nil), active...),
		ExcludedParticipants: append([]uint32(nil), excluded...), Reason: reason,
	}
	run.activeParticipants = active
	run.excludedParticipants = excluded
	run.returnParticipants = append([]uint32(nil), active...)
	run.successionReason = reason
	if len(active) < 2 || newLeader == 0 {
		run.currentLeaderID = 0
		run.successionPending = false
		transition.Interrupted = true
		transition.Reason = reason + "; no eligible SWARM_LEADER successor/minimum formation"
		run.successionReason = transition.Reason
		run.waits = make(map[uint32]*WaitState)
		e.transition(run, StateInterrupted, "swarm-takeover-no-successor", OwnerOperator, transition.Reason)
		return transition, true
	}
	run.currentLeaderID = newLeader
	run.successionPending = true
	if newLeader != oldLeader {
		run.authorityClaimedIndex = -1
		run.arrived = make(map[uint32]bool)
		run.leaderObservationAfter = e.clock()
		reason = fmt.Sprintf("operator takeover excluded leader Drone %d; successor Drone %d pending formation rebind",
			targetID, newLeader)
	} else {
		reason = fmt.Sprintf("operator takeover excluded follower Drone %d; formation rebind pending", targetID)
	}
	run.successionReason = reason
	transition.Reason = reason
	e.setState(run, StateRunning, "swarm-takeover-pending", OwnerOperator, reason)
	return transition, true
}

// CompleteSwarmOperatorTakeover opens progression only after swarm.Manager has
// atomically rebuilt follower ownership. A failed/stale rebind interrupts with
// no automatic navigation command.
func (e *Engine) CompleteSwarmOperatorTakeover(runID, generation uint64, ok bool, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.RunID != runID || run.State.IsTerminal() ||
		run.swarmGeneration != generation || !run.successionPending {
		return false
	}
	run.successionPending = false
	if !ok {
		if reason == "" {
			reason = "SWARM formation rebind failed"
		}
		run.successionReason = reason
		run.waits = make(map[uint32]*WaitState)
		e.transition(run, StateInterrupted, "swarm-takeover-rebind-failed", OwnerSystem, reason)
		return true
	}
	if reason == "" {
		reason = "SWARM takeover/succession committed"
	}
	run.successionReason = reason
	e.setState(run, StateRunning, "swarm-takeover-committed", OwnerOperator, reason)
	return true
}
