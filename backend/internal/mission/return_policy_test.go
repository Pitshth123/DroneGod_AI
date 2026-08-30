package mission

import (
	"errors"
	"testing"
	"time"
)

func returnEngine(clock Clock, enable func(*Engine)) *Engine {
	e := NewEngine(clock)
	enable(e)
	e.EnableReturnPolicyPreFlip()
	return e
}

func TestReturnPolicyCompatibilityAndModeResolution(t *testing.T) {
	cases := []struct {
		name string
		mode Mode
		rtl  bool
		want ReturnPolicy
	}{
		{"single/grouped none", ModeGrouped, false, ReturnNone},
		{"grouped rtl all", ModeGrouped, true, ReturnRTLAllAfterMission},
		{"separate rtl all", ModeSeparate, true, ReturnRTLAllAfterMission},
		{"swarm delegates", ModeSwarmLeader, true, ReturnSwarm},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p, err := (MissionPlan{Mode: tc.mode, RtlAfter: tc.rtl}).resolveReturnPolicy()
			if err != nil || p.ReturnPolicy != tc.want || !p.ReturnPolicyExplicit {
				t.Fatalf("resolved=%+v err=%v want=%s", p, err, tc.want)
			}
		})
	}
	if got := ResolveWaveReturnPolicy(true); got != ReturnWaveManaged {
		t.Fatalf("WAVE policy=%s", got)
	}
}

func TestReturnPolicyRejectsConflictAndSeparateEarlyReturn(t *testing.T) {
	_, err := (MissionPlan{Mode: ModeSwarmLeader, RtlAfter: true,
		ReturnPolicyExplicit: true, ReturnPolicy: ReturnRTLAllAfterMission}).resolveReturnPolicy()
	if !errors.Is(err, ErrReturnPolicyConflict) {
		t.Fatalf("conflict err=%v", err)
	}
	_, err = (MissionPlan{Mode: ModeSeparate, RtlAfter: true,
		SeparateReturnTiming: SeparateReturnEachOnRouteComplete}).resolveReturnPolicy()
	if !errors.Is(err, ErrSeparateEarlyReturn) {
		t.Fatalf("early return err=%v", err)
	}
}

func TestSingleNaturalCompletionReturnExactlyOnce(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableAuthority)
	p := groupedPlan([]Waypoint{wpA}, 7)
	p.RtlAfter = true
	runID := mustStart(t, e, p)
	e.Observe(7, wpA.Lat, wpA.Lon, 0)
	s := e.Snapshot()
	if s.State != StateCompleted || s.Active || s.Authority || s.ReturnState != ReturnStatePending {
		t.Fatalf("completion handoff=%+v", s)
	}
	in, ok := e.ClaimReturnIntent()
	if !ok || in.RunID != runID || in.Policy != ReturnRTLAllAfterMission || len(in.Participants) != 1 {
		t.Fatalf("intent=%+v ok=%v", in, ok)
	}
	if _, duplicate := e.ClaimReturnIntent(); duplicate {
		t.Fatal("duplicate completion/telemetry must not claim Return twice")
	}
	e.Observe(7, wpA.Lat, wpA.Lon, 0)
	if _, duplicate := e.ClaimReturnIntent(); duplicate {
		t.Fatal("duplicate terminal telemetry revived Return")
	}
}

func TestNaturalCompletionNoneHasNoReturn(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableAuthority)
	mustStart(t, e, groupedPlan([]Waypoint{wpA}, 1))
	e.Observe(1, wpA.Lat, wpA.Lon, 0)
	if s := e.Snapshot(); s.State != StateCompleted || s.ReturnState != ReturnStateInactive {
		t.Fatalf("snapshot=%+v", s)
	}
	if _, ok := e.ClaimReturnIntent(); ok {
		t.Fatal("NONE emitted Return")
	}
}

func TestWaitMustFinishBeforeReturn(t *testing.T) {
	c := newClock()
	e := returnEngine(c.now, (*Engine).EnableWaitAuthority)
	p := groupedPlan([]Waypoint{{Seq: 0, Lat: wpA.Lat, Lon: wpA.Lon, WaitSeconds: 5}}, 1)
	p.RtlAfter = true
	mustStart(t, e, p)
	e.Observe(1, wpA.Lat, wpA.Lon, 0)
	if s := e.Snapshot(); s.State != StateWaiting || s.ReturnState != ReturnStateInactive {
		t.Fatalf("before WAIT completion=%+v", s)
	}
	c.advance(5 * time.Second)
	e.Poll()
	if s := e.Snapshot(); s.State != StateCompleted || s.ReturnState != ReturnStatePending {
		t.Fatalf("after WAIT completion=%+v", s)
	}
}

func TestCancelAndFailsafeSuppressReturn(t *testing.T) {
	t.Run("cancel before completion", func(t *testing.T) {
		e := returnEngine(nil, (*Engine).EnableAuthority)
		p := groupedPlan([]Waypoint{wpA}, 1)
		p.RtlAfter = true
		id := mustStart(t, e, p)
		_ = e.Cancel(id)
		e.Observe(1, wpA.Lat, wpA.Lon, 0)
		if _, ok := e.ClaimReturnIntent(); ok {
			t.Fatal("cancelled mission emitted Return")
		}
	})
	t.Run("cancel wins at pending boundary", func(t *testing.T) {
		e := returnEngine(nil, (*Engine).EnableAuthority)
		p := groupedPlan([]Waypoint{wpA}, 1)
		p.RtlAfter = true
		id := mustStart(t, e, p)
		e.Observe(1, wpA.Lat, wpA.Lon, 0)
		_ = e.Cancel(id)
		if s := e.Snapshot(); s.State != StateCancelled || s.ReturnState != ReturnStateSuppressed {
			t.Fatalf("cancel boundary=%+v", s)
		}
	})
	t.Run("failsafe owns safety action", func(t *testing.T) {
		e := returnEngine(nil, (*Engine).EnableAuthority)
		p := groupedPlan([]Waypoint{wpA}, 1)
		p.RtlAfter = true
		mustStart(t, e, p)
		e.Observe(1, wpA.Lat, wpA.Lon, 0)
		if !e.Interrupt(1, "battery", "critical battery") {
			t.Fatal("failsafe did not suppress pending Return")
		}
		if s := e.Snapshot(); s.State != StateInterrupted || s.ReturnState != ReturnStateSuppressed {
			t.Fatalf("failsafe snapshot=%+v", s)
		}
	})
}

func TestGroupedBarrierNoEarlyReturn(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableGroupedMultiAuthority)
	p := groupedPlan([]Waypoint{wpA}, 1, 2, 3)
	p.RtlAfter = true
	mustStart(t, e, p)
	intents, ok, err := e.ClaimAuthorityGroupedGotos()
	if err != nil || !ok || len(intents) != 3 {
		t.Fatalf("group intents=%+v ok=%v err=%v", intents, ok, err)
	}
	for _, in := range intents[:2] {
		e.Observe(in.DroneID, in.Lat, in.Lon, 0)
	}
	if s := e.Snapshot(); s.State != StateRunning || s.ReturnState != ReturnStateInactive {
		t.Fatalf("early barrier=%+v", s)
	}
	last := intents[2]
	e.Observe(last.DroneID, last.Lat, last.Lon, 0)
	if s := e.Snapshot(); s.State != StateCompleted || s.ReturnState != ReturnStatePending {
		t.Fatalf("complete barrier=%+v", s)
	}
}

func separateReturnPlan() MissionPlan {
	return MissionPlan{
		PlanID: "separate-return", Mode: ModeSeparate, Participants: []uint32{1, 2}, RtlAfter: true,
		Routes: []Route{
			{DroneID: 1, Points: []Waypoint{{Seq: 0, Lat: 14.0, Lon: 100.0}}},
			{DroneID: 2, Points: []Waypoint{{Seq: 0, Lat: 15.0, Lon: 101.0}}},
		},
	}
}

func TestSeparateReturnsAllOnlyAfterAllRoutes(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableSeparateAuthority)
	mustStart(t, e, separateReturnPlan())
	e.Observe(1, 14.0, 100.0, 0)
	if s := e.Snapshot(); s.State != StateRunning || s.ReturnState != ReturnStateInactive {
		t.Fatalf("one route complete=%+v", s)
	}
	e.Observe(2, 15.0, 101.0, 0)
	in, ok := e.ClaimReturnIntent()
	if !ok || in.Policy != ReturnRTLAllAfterMission || len(in.Participants) != 2 {
		t.Fatalf("all routes intent=%+v ok=%v", in, ok)
	}
}

func TestSwarmReturnNeverProducesLeaderRTLIntent(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableSwarmLeaderAuthority)
	p := MissionPlan{PlanID: "swarm-return", Mode: ModeSwarmLeader,
		Participants: []uint32{1, 2, 3}, LeaderID: 1, RtlAfter: true,
		Routes: []Route{{Points: []Waypoint{wpA}}}}
	mustStart(t, e, p)
	e.Observe(1, wpA.Lat, wpA.Lon, 0)
	in, ok := e.ClaimReturnIntent()
	if !ok || in.Policy != ReturnSwarm || len(in.Participants) != 3 {
		t.Fatalf("swarm intent=%+v ok=%v", in, ok)
	}
}

func TestWaveAlwaysHasExactlyOneManagedReturnPolicy(t *testing.T) {
	w := NewWaveEngine(nil)
	if err := w.Start([]WaveGroup{{ID: 1, Members: []uint32{1}}, {ID: 2, Members: []uint32{2}}}, true); err != nil {
		t.Fatal(err)
	}
	if got := w.Snapshot().ReturnPolicy; got != ReturnWaveManaged {
		t.Fatalf("WAVE policy=%s", got)
	}
	if ResolveWaveReturnPolicy(false) != ReturnWaveManaged || ResolveWaveReturnPolicy(true) != ReturnWaveManaged {
		t.Fatal("generic rtl_after created a second/non-WAVE Return policy")
	}
}

func TestReturnPersistenceRestartsCommandInert(t *testing.T) {
	for _, returning := range []bool{false, true} {
		t.Run(map[bool]string{false: "pending", true: "returning"}[returning], func(t *testing.T) {
			e := returnEngine(nil, (*Engine).EnableAuthority)
			p := groupedPlan([]Waypoint{wpA}, 1)
			p.RtlAfter = true
			mustStart(t, e, p)
			e.Observe(1, wpA.Lat, wpA.Lon, 0)
			if returning {
				e.ClaimReturnIntent()
			}
			record, ok := e.DurableRecord("old-core")
			if !ok {
				t.Fatal("missing record")
			}
			restored := returnEngine(nil, (*Engine).EnableAuthority)
			if err := restored.RestoreDurableMission(*record); err != nil {
				t.Fatal(err)
			}
			s := restored.Snapshot()
			if s.Active || s.Authority || !s.RecoveryRequired || s.State != StateInterrupted ||
				s.ReturnState != ReturnStateRecoveryRequired {
				t.Fatalf("restart must be command-inert: %+v", s)
			}
			if _, claim := restored.ClaimReturnIntent(); claim {
				t.Fatal("restart auto-resumed Return")
			}
		})
	}
}

func TestCorruptReturnPersistenceFailsClosed(t *testing.T) {
	e := returnEngine(nil, (*Engine).EnableAuthority)
	p := groupedPlan([]Waypoint{wpA}, 1)
	p.RtlAfter = true
	mustStart(t, e, p)
	e.Observe(1, wpA.Lat, wpA.Lon, 0)
	record, _ := e.DurableRecord("core")
	record.ReturnState = ReturnState(999)
	if err := ValidateDurableMissionRecord(*record); err == nil {
		t.Fatal("invalid Return persistence accepted")
	}
}
