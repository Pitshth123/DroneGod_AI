package mission

import (
	"errors"
	"testing"
	"time"
)

// V3-S09-C SWARM leader-path engine tests.  Mission commands the LEADER only, at
// the raw waypoint; followers are never commanded by the mission.

func slRoute() []Waypoint {
	return []Waypoint{
		{Seq: 0, Lat: 14.0000, Lon: 100.0000},
		{Seq: 1, Lat: 14.0010, Lon: 100.0000},
	}
}

func commitSwarmTakeover(t *testing.T, e *Engine, target uint32,
	eligible func(uint32) bool) SwarmTakeoverTransition {
	t.Helper()
	transition, ok := e.BeginSwarmOperatorTakeover(target, eligible)
	if !ok {
		t.Fatalf("takeover D%d was not handled", target)
	}
	if !transition.Interrupted && !transition.Duplicate &&
		!e.CompleteSwarmOperatorTakeover(transition.RunID, transition.Generation, true, "test rebind") {
		t.Fatalf("takeover D%d did not commit", target)
	}
	return transition
}

func swarmLeaderPlan(leader uint32, participants ...uint32) MissionPlan {
	alts := make(map[uint32]float64, len(participants))
	for _, id := range participants {
		alts[id] = 30 + float64(id)
	}
	return MissionPlan{
		PlanID: "swl", Mode: ModeSwarmLeader, Participants: participants, LeaderID: leader,
		Routes: []Route{{DroneID: 0, Points: slRoute()}}, ParticipantAltitudes: alts,
	}
}

func startSwarmLeader(t *testing.T, e *Engine, leader uint32, participants ...uint32) uint64 {
	t.Helper()
	e.EnableSwarmLeaderAuthority()
	id, err := e.StartOp(swarmLeaderPlan(leader, participants...), "swl-op")
	if err != nil {
		t.Fatalf("StartOp swarm-leader: %v", err)
	}
	return id
}

func claimLeader(t *testing.T, e *Engine) GotoIntent {
	t.Helper()
	in, ok, err := e.ClaimAuthoritySwarmLeaderGoto()
	if err != nil || !ok {
		t.Fatalf("expected leader claim: ok=%v err=%v", ok, err)
	}
	return in
}

func TestSwarmLeaderClaimsLeaderOnly(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3)
	in := claimLeader(t, e)
	wp := slRoute()[0]
	if in.DroneID != 1 || in.Lat != wp.Lat || in.Lon != wp.Lon || in.Alt != 31 {
		t.Fatalf("leader GOTO must target raw wp at leader alt: %+v", in)
	}
	if got := e.FollowerIDs(); len(got) != 2 || got[0] != 2 || got[1] != 3 {
		t.Fatalf("followers must be [2 3], got %v", got)
	}
	// barrier: one claim per index.
	if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); ok {
		t.Fatal("re-claim before advance must emit nothing")
	}
}

func TestSwarmLeaderRejectsZeroLeaderLikeLegacyNoHeadAbort(t *testing.T) {
	e := NewEngine(nil)
	e.EnableSwarmLeaderAuthority()
	if _, err := e.StartOp(swarmLeaderPlan(0, 2, 5, 8), "no-head"); !errors.Is(err, ErrAuthorityUnsupported) {
		t.Fatalf("LeaderID=0 must be ineligible, got %v", err)
	}
}

func TestSwarmLeaderFollowerArrivalDoesNotAdvance(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3)
	_ = claimLeader(t, e)
	wp := slRoute()[0]
	e.Observe(2, wp.Lat, wp.Lon, 0) // follower arrives -> must NOT advance
	if e.Snapshot().CurrentIndex != 0 {
		t.Fatal("follower arrival must not advance a SWARM_LEADER mission")
	}
	e.Observe(1, wp.Lat, wp.Lon, 0) // leader arrives -> advance
	if e.Snapshot().CurrentIndex != 1 {
		t.Fatal("leader arrival should advance")
	}
}

func TestSwarmLeaderNeverCommandsFollower(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3)
	r := slRoute()
	for idx := 0; idx < len(r); idx++ {
		in := claimLeader(t, e)
		if in.DroneID != 1 {
			t.Fatalf("mission must only ever command the leader, got D%d at wp %d", in.DroneID, idx)
		}
		e.Observe(1, r[idx].Lat, r[idx].Lon, 0)
	}
	if s := e.Snapshot(); s.State != StateCompleted {
		t.Fatalf("leader finishing the route completes the run: %+v", s)
	}
}

func TestSwarmLeaderOperatorCancelIsTerminal(t *testing.T) {
	e := NewEngine(nil)
	id := startSwarmLeader(t, e, 1, 1, 2, 3)
	_ = claimLeader(t, e)
	_ = e.Cancel(id)
	if e.Snapshot().Active {
		t.Fatal("operator cancel must make route terminal")
	}
	if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); ok {
		t.Fatal("terminal mission must emit no stale leader GOTO")
	}
}

func TestSwarmLeaderFailsafeParityLeaderHaltsFollowerExcluded(t *testing.T) {
	for _, tc := range []struct {
		name     string
		droneID  uint32
		category string
		terminal bool
	}{
		{"leader battery", 1, "battery", true},
		{"leader link", 1, "link", true},
		{"follower battery", 2, "battery", false},
		{"follower link", 3, "link", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			startSwarmLeader(t, e, 1, 1, 2, 3)
			_ = claimLeader(t, e)
			interrupted := e.Interrupt(tc.droneID, tc.category, tc.name)
			if interrupted != tc.terminal {
				t.Fatalf("Interrupt=%v want %v", interrupted, tc.terminal)
			}
			if e.Snapshot().Active == tc.terminal {
				t.Fatalf("active=%v terminal=%v", e.Snapshot().Active, tc.terminal)
			}
		})
	}
}

func TestSwarmLeaderNoDualAuthority(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3)
	if _, ok, _ := e.ClaimAuthorityGoto(); ok {
		t.Fatal("single-drone claim must be inert in SWARM_LEADER mode")
	}
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("grouped claim must be inert in SWARM_LEADER mode")
	}
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
		t.Fatal("separate claim must be inert in SWARM_LEADER mode")
	}
	if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); !ok {
		t.Fatal("only the swarm-leader claim should emit")
	}
}

func TestSwarmLeaderRejectsUnsupportedPlans(t *testing.T) {
	tests := []struct {
		name string
		plan MissionPlan
	}{
		{"single participant", swarmLeaderPlan(1, 1)},
		{"zero leader", swarmLeaderPlan(0, 1, 2)},
		{"wait deferred", func() MissionPlan {
			p := swarmLeaderPlan(1, 1, 2)
			p.Routes[0].Points[0].WaitSeconds = 60
			return p
		}()},
		{"action deferred", func() MissionPlan {
			p := swarmLeaderPlan(1, 1, 2)
			p.Routes[0].Points[1].Action = ActionServoA
			return p
		}()},
		{"rtl_after deferred", func() MissionPlan {
			p := swarmLeaderPlan(1, 1, 2)
			p.RtlAfter = true
			return p
		}()},
		{"leader not a participant", swarmLeaderPlan(9, 1, 2)},
		{"grouped mode rejected", groupedMultiPlan(gmRoute, 1, 2)},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			e.EnableSwarmLeaderAuthority()
			if _, err := e.StartOp(tc.plan, "op"); !errors.Is(err, ErrAuthorityUnsupported) && !errors.Is(err, ErrRouteMismatch) {
				t.Fatalf("want unsupported/route error, got %v", err)
			}
			if e.Snapshot().Active {
				t.Fatal("unsupported plan must create no run")
			}
		})
	}
}

func TestSwarmFollowerTakeoverExcludesOnlyFollowerAndNeverRejoins(t *testing.T) {
	e := NewEngine(nil)
	runID := startSwarmLeader(t, e, 1, 1, 2, 3, 4)
	first := claimLeader(t, e)
	tr := commitSwarmTakeover(t, e, 3, func(uint32) bool { return true })
	snap := e.Snapshot()
	if tr.Interrupted || !snap.Active || snap.RunID != runID || snap.CurrentIndex != first.Index ||
		snap.CurrentLeaderID != 1 || len(snap.ActiveParticipants) != 3 ||
		len(snap.ExcludedParticipants) != 1 || snap.ExcludedParticipants[0] != 3 {
		t.Fatalf("follower exclusion changed wrong state: transition=%+v snapshot=%+v", tr, snap)
	}
	if got := e.FollowerIDs(); len(got) != 2 || got[0] != 2 || got[1] != 4 {
		t.Fatalf("remaining followers=%v want [2 4]", got)
	}
	e.Observe(3, slRoute()[0].Lat, slRoute()[0].Lon, 0)
	if after := e.Snapshot(); after.CurrentIndex != first.Index || len(after.ExcludedParticipants) != 1 {
		t.Fatalf("excluded telemetry rejoined/advanced run: %+v", after)
	}
	dup := commitSwarmTakeover(t, e, 3, func(uint32) bool { return true })
	if !dup.Duplicate || e.Snapshot().Revision != snap.Revision {
		t.Fatalf("duplicate takeover mutated membership: transition=%+v snapshot=%+v", dup, e.Snapshot())
	}
}

func TestSwarmLeaderTakeoverPromotesInsideSameRunAndIndex(t *testing.T) {
	// Deterministic clock: the post-promotion observation gate is an EXCLUSIVE
	// boundary (see TestSwarmPromotionRejectsPrePromotionTelemetry), so the
	// successor's arrival must carry a timestamp strictly after the promotion.
	// A real clock makes this depend on OS tick granularity rather than on the
	// behaviour under test.
	clk := newClock()
	e := NewEngine(clk.now)
	runID := startSwarmLeader(t, e, 1, 1, 2, 3, 4)
	_ = claimLeader(t, e)
	e.Observe(1, slRoute()[0].Lat, slRoute()[0].Lon, 0)
	if e.Snapshot().CurrentIndex != 1 {
		t.Fatal("setup did not advance to WP2")
	}
	old := claimLeader(t, e)
	tr := commitSwarmTakeover(t, e, 1, func(uint32) bool { return true })
	snap := e.Snapshot()
	if tr.NewLeaderID != 2 || snap.RunID != runID || snap.CurrentIndex != old.Index ||
		snap.CurrentLeaderID != 2 || !snap.Active {
		t.Fatalf("leader succession restarted/changed route: transition=%+v snapshot=%+v", tr, snap)
	}
	newIntent := claimLeader(t, e)
	if newIntent.DroneID != 2 || newIntent.Index != old.Index {
		t.Fatalf("successor must receive current WP only: old=%+v new=%+v", old, newIntent)
	}
	clk.advance(time.Millisecond) // evidence genuinely observed after the handoff
	e.Observe(1, slRoute()[1].Lat, slRoute()[1].Lon, 0)
	if e.Snapshot().State != StateRunning {
		t.Fatal("old leader arrival advanced/completed after promotion")
	}
	e.Observe(2, slRoute()[1].Lat, slRoute()[1].Lon, 0)
	if e.Snapshot().State != StateCompleted {
		t.Fatal("new leader arrival did not drive progression")
	}
}

func TestSwarmSuccessorElectionUsesOriginalOrderAndSkipsIneligible(t *testing.T) {
	for _, tc := range []struct {
		name       string
		ineligible map[uint32]bool
		want       uint32
	}{
		{"next original", nil, 2},
		{"skip D2", map[uint32]bool{2: true}, 3},
		{"skip D2 D3", map[uint32]bool{2: true, 3: true}, 4},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			startSwarmLeader(t, e, 1, 1, 2, 3, 4)
			tr := commitSwarmTakeover(t, e, 1, func(id uint32) bool { return !tc.ineligible[id] })
			if tr.NewLeaderID != tc.want || e.Snapshot().CurrentLeaderID != tc.want {
				t.Fatalf("successor=%d snapshot=%d want %d", tr.NewLeaderID, e.Snapshot().CurrentLeaderID, tc.want)
			}
		})
	}

	// Input order, not numeric ID order, is the deterministic source of truth.
	e := NewEngine(nil)
	startSwarmLeader(t, e, 9, 9, 7, 3, 5)
	tr := commitSwarmTakeover(t, e, 9, func(uint32) bool { return true })
	if tr.NewLeaderID != 7 {
		t.Fatalf("original-order successor=%d want 7", tr.NewLeaderID)
	}
}

func TestSwarmMultipleSuccessionsPreserveRunIndexAndExclusions(t *testing.T) {
	e := NewEngine(nil)
	runID := startSwarmLeader(t, e, 1, 1, 2, 3, 4)
	_ = claimLeader(t, e)
	e.Observe(1, slRoute()[0].Lat, slRoute()[0].Lon, 0)
	commitSwarmTakeover(t, e, 4, func(uint32) bool { return true })
	commitSwarmTakeover(t, e, 1, func(uint32) bool { return true })
	commitSwarmTakeover(t, e, 2, func(uint32) bool { return true })
	snap := e.Snapshot()
	if snap.RunID != runID || snap.CurrentIndex != 1 || snap.CurrentLeaderID != 0 ||
		snap.State != StateInterrupted {
		t.Fatalf("minimum-size succession must interrupt same run/index: %+v", snap)
	}
	if len(snap.ExcludedParticipants) != 3 || snap.ExcludedParticipants[0] != 1 ||
		snap.ExcludedParticipants[1] != 2 || snap.ExcludedParticipants[2] != 4 {
		t.Fatalf("excluded order/state lost across promotions: %v", snap.ExcludedParticipants)
	}
	if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); ok {
		t.Fatal("no-successor/minimum-size interruption invented a GOTO")
	}
}

func TestSwarmNoEligibleSuccessorInterruptsWithoutModeConversion(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3)
	tr, ok := e.BeginSwarmOperatorTakeover(1, func(uint32) bool { return false })
	if !ok || !tr.Interrupted {
		t.Fatalf("no successor must interrupt: %+v ok=%v", tr, ok)
	}
	snap := e.Snapshot()
	if snap.Active || snap.Authority || snap.Mode != ModeSwarmLeader || snap.State != StateInterrupted {
		t.Fatalf("no successor converted/resumed mission: %+v", snap)
	}
}

func TestSwarmPromotionRejectsPrePromotionTelemetry(t *testing.T) {
	now := time.Unix(1000, 0)
	e := NewEngine(func() time.Time { return now })
	startSwarmLeader(t, e, 1, 1, 2, 3)
	_ = claimLeader(t, e)
	tr := commitSwarmTakeover(t, e, 1, func(uint32) bool { return true })
	if tr.NewLeaderID != 2 {
		t.Fatalf("successor=%d", tr.NewLeaderID)
	}
	e.ObservePositionSample(2, slRoute()[0].Lat, slRoute()[0].Lon, 0, 0, true)
	if e.Snapshot().CurrentIndex != 0 {
		t.Fatal("sample at promotion boundary falsely advanced route")
	}
	now = now.Add(time.Millisecond)
	e.ObservePositionSample(2, slRoute()[0].Lat, slRoute()[0].Lon, 0, 0, true)
	if e.Snapshot().CurrentIndex != 1 {
		t.Fatal("post-promotion fresh successor sample did not advance route")
	}
}

// V3-S09-C — a succession in flight must never be able to resurrect a run that a
// stronger authority already terminated. The formation rebind reply is a late
// callback from a previous generation of the world.
//
//	Initial state:       SWARM_LEADER run, leader D1, succession to D2 begun but the
//	                     formation rebind has not replied yet (successionPending).
//	Injected failure:    operator Cancel / failsafe Interrupt lands first, then the
//	                     late rebind reply arrives.
//	Expected Safe State: the run stays terminal and command-free.
//	MUST NOT happen:     the late reply committing the succession, clearing terminal
//	                     state, or unblocking a GOTO.
func TestSwarmSuccessionCannotResurrectATerminatedRun(t *testing.T) {
	for _, tc := range []struct {
		name      string
		terminate func(*testing.T, *Engine, uint64)
		want      State
	}{
		{"operator cancel", func(t *testing.T, e *Engine, runID uint64) {
			if err := e.Cancel(runID); err != nil {
				t.Fatal(err)
			}
		}, StateCancelled},
		{"incoming leader failsafe", func(t *testing.T, e *Engine, _ uint64) {
			// The excluded drone is operator-controlled and no longer part of the
			// run, so its failsafe must NOT interrupt the mission it left.
			if e.Interrupt(1, "battery", "excluded drone battery") {
				t.Fatal("excluded drone failsafe interrupted a run it no longer belongs to")
			}
			if !e.Interrupt(2, "battery", "battery critical during succession") {
				t.Fatal("incoming leader failsafe must interrupt")
			}
		}, StateInterrupted},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			runID := startSwarmLeader(t, e, 1, 1, 2, 3)
			_ = claimLeader(t, e)
			transition, ok := e.BeginSwarmOperatorTakeover(1, func(uint32) bool { return true })
			if !ok || transition.NewLeaderID != 2 || !e.Snapshot().SuccessionPending {
				t.Fatalf("precondition: pending succession: %+v ok=%v", transition, ok)
			}

			tc.terminate(t, e, runID)
			if got := e.Snapshot().State; got != tc.want {
				t.Fatalf("termination state=%s want %s", got, tc.want)
			}
			if e.CompleteSwarmOperatorTakeover(transition.RunID, transition.Generation,
				true, "late formation rebind") {
				t.Fatal("late rebind reply committed a succession on a terminated run")
			}
			snap := e.Snapshot()
			if snap.Active || snap.Authority || snap.State != tc.want {
				t.Fatalf("late rebind reply resurrected the run: %+v", snap)
			}
			if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); ok {
				t.Fatal("terminated run emitted a GOTO after the late rebind reply")
			}
		})
	}
}

// A rebind reply from a superseded generation must not commit a newer succession.
func TestSwarmStaleGenerationRebindReplyCannotCommitNewerSuccession(t *testing.T) {
	e := NewEngine(nil)
	startSwarmLeader(t, e, 1, 1, 2, 3, 4)
	first, ok := e.BeginSwarmOperatorTakeover(4, func(uint32) bool { return true })
	if !ok {
		t.Fatal("first takeover not handled")
	}
	if !e.CompleteSwarmOperatorTakeover(first.RunID, first.Generation, true, "rebind 1") {
		t.Fatal("first succession did not commit")
	}
	second, ok := e.BeginSwarmOperatorTakeover(1, func(uint32) bool { return true })
	if !ok || second.NewLeaderID != 2 || second.Generation == first.Generation {
		t.Fatalf("precondition: second succession: %+v ok=%v", second, ok)
	}
	if e.CompleteSwarmOperatorTakeover(second.RunID, first.Generation, true, "stale reply") {
		t.Fatal("stale-generation reply committed the newer succession")
	}
	if !e.Snapshot().SuccessionPending {
		t.Fatal("stale reply cleared the pending succession")
	}
	if _, ok, _ := e.ClaimAuthoritySwarmLeaderGoto(); ok {
		t.Fatal("pending succession emitted a GOTO before its own rebind committed")
	}
	if !e.CompleteSwarmOperatorTakeover(second.RunID, second.Generation, true, "rebind 2") {
		t.Fatal("matching reply must still commit the newer succession")
	}
}
