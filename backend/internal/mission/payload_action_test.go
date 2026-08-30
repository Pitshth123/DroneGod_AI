package mission

import (
	"errors"
	"testing"
	"time"
)

const chA = 9

func startAction(t *testing.T, a *ActionEngine, ids ...uint32) uint64 {
	t.Helper()
	if err := a.Start(ids, chA, 1900); err != nil {
		t.Fatal(err)
	}
	return a.Token()
}

func completeOneAction(t *testing.T, a *ActionEngine, clk *fakeClock, ids ...uint32) uint64 {
	t.Helper()
	tok := startAction(t, a, ids...)
	if !a.HoldResult(tok, true) {
		t.Fatal("hold")
	}
	for _, id := range ids {
		if !a.ServoSetResult(tok, id, true) {
			t.Fatal("set")
		}
	}
	clk.advance(3 * time.Second)
	if !a.Poll() {
		t.Fatal("dwell")
	}
	for _, id := range ids {
		if !a.ReleaseResult(tok, id) {
			t.Fatal("release")
		}
	}
	if !a.ShouldAdvance() {
		t.Fatal("completed action should advance")
	}
	return tok
}

func TestActionFullSequenceExactlyOnce(t *testing.T) {
	clk := newClock()
	a := NewActionEngine(clk.now)
	a.SetDwell(2 * time.Second)
	tok := startAction(t, a, 1, 2)
	if !a.HoldResult(tok, true) || !a.ServoSetResult(tok, 1, true) ||
		!a.ServoSetResult(tok, 2, true) {
		t.Fatal("valid callbacks rejected")
	}
	if a.ServoSetResult(tok, 1, true) {
		t.Fatal("duplicate SET accepted")
	}
	clk.advance(3 * time.Second)
	if !a.Poll() || !a.ReleaseResult(tok, 1) || !a.ReleaseResult(tok, 2) {
		t.Fatal("release sequence failed")
	}
	if a.ReleaseResult(tok, 1) {
		t.Fatal("duplicate RELEASE accepted")
	}
	if s := a.Snapshot(); s.Phase != ActionDone || s.SetCount != 2 || s.RelCount != 2 {
		t.Fatalf("bad final state: %+v", s)
	}
}

func TestActionRejectsDuplicateStartWhileActive(t *testing.T) {
	a := NewActionEngine(nil)
	startAction(t, a, 1)
	if err := a.Start([]uint32{2}, chA, 1900); !errors.Is(err, ErrActionActive) {
		t.Fatalf("active action replacement must be rejected: %v", err)
	}
	if s := a.Snapshot(); s.SetCount != 0 || s.Channel != chA {
		t.Fatalf("active action was overwritten: %+v", s)
	}
}

func TestActionRunTokensIsolateHoldServoAndRelease(t *testing.T) {
	clk := newClock()
	a := NewActionEngine(clk.now)
	old := completeOneAction(t, a, clk, 1)
	current := startAction(t, a, 2)
	if old == current {
		t.Fatal("action token reused")
	}
	if a.HoldResult(old, true) || a.ServoSetResult(old, 2, true) || a.ReleaseResult(old, 2) {
		t.Fatal("old HOLD/SET/RELEASE callback affected new action")
	}
	if !a.HoldResult(current, true) {
		t.Fatal("current action callback should work")
	}
}

func TestActionCancelThenNewRunRejectsOldCallback(t *testing.T) {
	a := NewActionEngine(nil)
	old := startAction(t, a, 1)
	a.Cancel()
	if a.ShouldAdvance() {
		t.Fatal("cancel must not advance")
	}
	current := startAction(t, a, 2)
	if old == current || a.HoldResult(old, true) {
		t.Fatal("old cancelled callback affected new action")
	}
}

func TestActionCancelAfterOneSetReleasesAllParticipants(t *testing.T) {
	a := NewActionEngine(nil)
	tok := startAction(t, a, 1, 2)
	a.HoldResult(tok, true)
	a.ServoSetResult(tok, 1, true)
	// D2 represents an uncertain/lost SET result: cancellation must still retain
	// its release obligation so a physical payload cannot remain engaged.
	a.Cancel()
	if s := a.Snapshot(); s.Phase != ActionRelease || !s.Cancelled ||
		len(s.ReleaseDrones) != 2 || s.ReleaseDrones[0] != 1 || s.ReleaseDrones[1] != 2 {
		t.Fatalf("cancel must preserve release-all cleanup: %+v", s)
	}
	if a.ServoSetResult(tok, 2, true) {
		t.Fatal("cancel must immediately block new SET")
	}
	if !a.ReleaseResult(tok, 1) || !a.ReleaseResult(tok, 2) || a.ReleaseResult(tok, 1) {
		t.Fatal("cleanup release must be allowed exactly once for every participant")
	}
	if a.ShouldAdvance() {
		t.Fatal("cancelled cleanup must never advance")
	}
}

func TestActionCancelAndFailsafeDuringDwellRequireCleanup(t *testing.T) {
	for _, failsafe := range []bool{false, true} {
		clk := newClock()
		a := NewActionEngine(clk.now)
		tok := startAction(t, a, 1, 2)
		a.HoldResult(tok, true)
		a.ServoSetResult(tok, 1, true)
		a.ServoSetResult(tok, 2, true)
		if failsafe {
			a.Interrupt("link lost")
		} else {
			a.Cancel()
		}
		if a.Poll() {
			t.Fatal("cleanup-only state must not dwell")
		}
		if !a.ReleaseResult(tok, 1) || !a.ReleaseResult(tok, 2) {
			t.Fatal("both affected drones require cleanup")
		}
		if a.ShouldAdvance() {
			t.Fatal("cancel/failsafe must suppress route progression")
		}
	}
}

func TestActionCancelDuringReleaseAndCleanupFailureObservable(t *testing.T) {
	clk := newClock()
	a := NewActionEngine(clk.now)
	tok := startAction(t, a, 1, 2)
	a.HoldResult(tok, true)
	a.ServoSetResult(tok, 1, true)
	a.ServoSetResult(tok, 2, true)
	clk.advance(3 * time.Second)
	a.Poll()
	a.ReleaseResult(tok, 1)
	a.Cancel()
	if !a.ReleaseResult(tok, 2, false) {
		t.Fatal("remaining cleanup must survive cancel")
	}
	s := a.Snapshot()
	if !s.CleanupFailed || s.CleanupFailure == "" || s.RelCount != 2 {
		t.Fatalf("cleanup failure must be observable: %+v", s)
	}
	if a.ShouldAdvance() {
		t.Fatal("cancelled action must not advance")
	}
}

func TestActionHoldFailureStillReleasesAllLikeLegacyFinish(t *testing.T) {
	a := NewActionEngine(nil)
	tok := startAction(t, a, 1, 2)
	if !a.HoldResult(tok, false) {
		t.Fatal("hold failure")
	}
	s := a.Snapshot()
	if s.Phase != ActionRelease || len(s.ReleaseDrones) != 2 || !s.Failed {
		t.Fatalf("Legacy finish(False) requires release-all cleanup: %+v", s)
	}
	if !a.ReleaseResult(tok, 1) || !a.ReleaseResult(tok, 2) || !a.ShouldAdvance() {
		t.Fatal("best-effort HOLD failure advances only after modeled cleanup completes")
	}
}

func TestActionServoFailureReleasesAllParticipantsThenAdvances(t *testing.T) {
	a := NewActionEngine(nil)
	tok := startAction(t, a, 1, 2)
	a.HoldResult(tok, true)
	a.ServoSetResult(tok, 1, false)
	if !a.ReleaseResult(tok, 1) || !a.ReleaseResult(tok, 2) || !a.ShouldAdvance() {
		t.Fatal("Legacy finish(False) releases every participant before best-effort progression")
	}
}
