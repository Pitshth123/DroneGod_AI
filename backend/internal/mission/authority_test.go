package mission

import (
	"errors"
	"testing"
)

func TestClaimAuthorityGotoExactlyOncePerWaypoint(t *testing.T) {
	e := NewEngine(nil)
	e.EnableAuthority()
	p := authorityPlan()
	p.ParticipantAltitudes = map[uint32]float64{1: 27}
	id := mustStart(t, e, p)

	first, ok, err := e.ClaimAuthorityGoto()
	if err != nil || !ok {
		t.Fatalf("first claim: ok=%v err=%v", ok, err)
	}
	if first.RunID != id || first.Index != 0 || first.DroneID != 1 || first.Alt != 27 {
		t.Fatalf("unexpected first intent: %+v", first)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("same waypoint must not claim twice: ok=%v err=%v", ok, err)
	}

	e.Observe(1, p.Routes[0].Points[0].Lat, p.Routes[0].Points[0].Lon, 27)
	second, ok, err := e.ClaimAuthorityGoto()
	if err != nil || !ok || second.Index != 1 {
		t.Fatalf("next waypoint claim: intent=%+v ok=%v err=%v", second, ok, err)
	}
}

func TestClaimAuthorityGotoRejectsUnsupportedPlan(t *testing.T) {
	e := NewEngine(nil)
	p := groupedPlan([]Waypoint{wpA}, 1, 2)
	mustStart(t, e, p) // valid shadow plan, deliberately not authority-eligible
	e.EnableAuthority()
	if _, ok, err := e.ClaimAuthorityGoto(); ok || !errors.Is(err, ErrAuthorityUnsupported) {
		t.Fatalf("want authority rejection: ok=%v err=%v", ok, err)
	}
}

func TestClaimAuthorityGotoStopsAfterCancelOrInterrupt(t *testing.T) {
	for _, tc := range []struct {
		name string
		stop func(*Engine, uint64)
	}{
		{"cancel", func(e *Engine, id uint64) { _ = e.Cancel(id) }},
		{"interrupt", func(e *Engine, _ uint64) { e.Interrupt(1, "battery", "critical") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			e.EnableAuthority()
			id := mustStart(t, e, authorityPlan())
			tc.stop(e, id)
			if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
				t.Fatalf("terminal run must not emit intent: ok=%v err=%v", ok, err)
			}
		})
	}
}

func TestFailIsStaleSafeAndTerminal(t *testing.T) {
	e := NewEngine(nil)
	id := mustStart(t, e, authorityPlan())
	e.Fail(id+99, "stale")
	if e.Snapshot().State != StateRunning {
		t.Fatal("stale failure must not touch active run")
	}
	e.Fail(id, "safety rejected GOTO")
	s := e.Snapshot()
	if s.State != StateFailed || s.Active || s.TerminalReason != "safety rejected GOTO" {
		t.Fatalf("matching failure should be terminal: %+v", s)
	}
	e.Observe(1, 14, 100, 20)
	if e.Snapshot().State != StateFailed {
		t.Fatal("FAILED mission must not resume from telemetry")
	}
}
