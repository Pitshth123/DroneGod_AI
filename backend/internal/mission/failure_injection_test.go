package mission

import (
	"testing"
	"time"
)

// F7 failure-injection coverage keeps these scenarios deterministic and entirely
// command-free.  API/command-path tests separately verify the side-effect adapter.

func TestFailureInjectionBatteryAndLinkDuringWaitNeverResume(t *testing.T) {
	for _, category := range []string{"battery", "link"} {
		t.Run(category, func(t *testing.T) {
			clk := newClock()
			e := NewEngine(clk.now)
			e.EnableWaitAuthority()
			wait := wpA
			wait.WaitSeconds = 60
			id := mustStart(t, e, groupedPlan([]Waypoint{wait, wpB}, 1))

			if _, ok, err := e.ClaimAuthorityGoto(); err != nil || !ok {
				t.Fatalf("initial GOTO claim: ok=%v err=%v", ok, err)
			}
			e.Observe(1, wait.Lat, wait.Lon, 0)
			if _, ok, err := e.ClaimAuthorityHold(); err != nil || !ok {
				t.Fatalf("WAIT HOLD claim: ok=%v err=%v", ok, err)
			}

			e.Interrupt(1, category, category+" injected during WAIT")
			clk.advance(2 * 60 * 1e9) // 2 minutes; fake clock uses time.Duration nanoseconds
			if e.Poll() {
				t.Fatal("interrupted WAIT must not advance after its old deadline")
			}
			if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
				t.Fatalf("interrupted run must emit no later GOTO: ok=%v err=%v", ok, err)
			}
			s := e.Snapshot()
			if s.RunID != id || s.State != StateInterrupted || s.Active {
				t.Fatalf("interrupt must remain terminal: %+v", s)
			}
		})
	}
}

func TestFailureInjectionCancelAtWaypointBoundarySuppressesNextCommand(t *testing.T) {
	for _, tc := range []struct {
		name        string
		arriveFirst bool
	}{
		{"cancel-before-arrival", false},
		{"cancel-after-arrival-before-next-dispatch", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			e.EnableAuthority()
			p := groupedPlan([]Waypoint{wpA, wpB}, 1)
			id := mustStart(t, e, p)
			if _, ok, err := e.ClaimAuthorityGoto(); err != nil || !ok {
				t.Fatalf("initial claim: ok=%v err=%v", ok, err)
			}
			if tc.arriveFirst {
				e.Observe(1, wpA.Lat, wpA.Lon, 0)
			}
			if err := e.Cancel(id); err != nil {
				t.Fatal(err)
			}
			if !tc.arriveFirst {
				e.Observe(1, wpA.Lat, wpA.Lon, 0) // stale telemetry after cancel
			}
			if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
				t.Fatalf("cancel boundary must suppress next command: ok=%v err=%v", ok, err)
			}
			if s := e.Snapshot(); s.State != StateCancelled {
				t.Fatalf("state=%s want CANCELLED", s.State)
			}
		})
	}
}

func TestFailureInjectionCoreRestartDoesNotResumeInMemoryMission(t *testing.T) {
	old := NewEngine(nil)
	old.EnableAuthority()
	mustStart(t, old, authorityPlan())
	if _, ok, err := old.ClaimAuthorityGoto(); err != nil || !ok {
		t.Fatalf("old core initial claim: ok=%v err=%v", ok, err)
	}

	// V2 intentionally has no mission persistence. A restarted Core constructs a
	// fresh Engine and therefore must be IDLE rather than silently resuming.
	restarted := NewEngine(nil)
	restarted.EnableAuthority()
	if s := restarted.Snapshot(); s.Active || s.RunID != 0 || s.State != StateIdle {
		t.Fatalf("restarted Core must be idle/no auto-resume: %+v", s)
	}
	if _, ok, err := restarted.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("fresh Core must emit no command: ok=%v err=%v", ok, err)
	}
}

// S11-G — out-of-order telemetry. A newer sample followed by an older one must not
// move recorded freshness backward, otherwise a stale fix could later be treated as
// fresh and seed a false centroid/arrival.
//
//	Initial state:       an active run with one fresh position recorded at T.
//	Injected failure:    a later-arriving but OLDER observation (large age).
//	Expected Safe State: recorded position/timestamp stay at the newer sample; a
//	                     genuinely fresh later sample still updates.
//	MUST NOT happen:     freshness regressing to the older sample.
func TestFailureInjectionOutOfOrderTelemetryDoesNotMoveFreshnessBackward(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.EnableAuthority()
	// Single far waypoint so no observation ever registers an arrival/advance.
	mustStart(t, e, groupedPlan([]Waypoint{{Seq: 0, Lat: 14.0, Lon: 100.0}}, 1))

	e.ObservePositionSample(1, 15.0, 101.0, 0, 0, true) // fresh sample recorded at T
	newPos := e.run.lastPos[1]
	newAt := e.run.lastPosAt[1]

	clk.advance(10 * time.Second)
	// Arrives later on the wall clock but describes an OLDER observation (age 30s →
	// observedAt = T+10s-30s < T): the out-of-order guard must reject it.
	e.ObservePositionSample(1, 16.0, 102.0, 0, 30*time.Second, true)
	if e.run.lastPos[1] != newPos || !e.run.lastPosAt[1].Equal(newAt) {
		t.Fatalf("older out-of-order sample moved freshness backward: pos=%v at=%v",
			e.run.lastPos[1], e.run.lastPosAt[1])
	}

	clk.advance(10 * time.Second)
	e.ObservePositionSample(1, 17.0, 103.0, 0, 0, true) // genuinely fresh → updates
	if e.run.lastPos[1] == newPos {
		t.Fatal("a genuinely fresh later sample must update recorded freshness")
	}
}

func TestFailureInjectionRepeatedTerminalCyclesDoNotLeakActiveRun(t *testing.T) {
	e := NewEngine(nil)
	e.EnableAuthority()
	p := groupedPlan([]Waypoint{wpA}, 1)
	const cycles = 500
	for i := 0; i < cycles; i++ {
		id, err := e.StartOp(p, "")
		if err != nil {
			t.Fatalf("cycle %d start: %v", i, err)
		}
		if _, ok, err := e.ClaimAuthorityGoto(); err != nil || !ok {
			t.Fatalf("cycle %d initial claim: ok=%v err=%v", i, ok, err)
		}
		if i%2 == 0 {
			if err := e.Cancel(id); err != nil {
				t.Fatalf("cycle %d cancel: %v", i, err)
			}
		} else {
			e.Observe(1, wpA.Lat, wpA.Lon, 0)
		}
		s := e.Snapshot()
		if s.Active || !s.State.IsTerminal() || s.RunID != id {
			t.Fatalf("cycle %d left a live run: %+v", i, s)
		}
		if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
			t.Fatalf("cycle %d terminal run emitted command: ok=%v err=%v", i, ok, err)
		}
	}
	if got := e.Snapshot().RunID; got != cycles {
		t.Fatalf("final run id=%d want %d", got, cycles)
	}
}
