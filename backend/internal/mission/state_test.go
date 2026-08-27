package mission

import "testing"

func TestStateStrings(t *testing.T) {
	cases := map[State]string{
		StateIdle: "IDLE", StateValidating: "VALIDATING", StateReady: "READY",
		StateRunning: "RUNNING", StateWaiting: "WAITING", StateCancelling: "CANCELLING",
		StateCancelled: "CANCELLED", StateInterrupted: "INTERRUPTED", StateFailed: "FAILED",
		StateCompleted: "COMPLETED",
	}
	for s, want := range cases {
		if s.String() != want {
			t.Errorf("%d.String() = %q, want %q", int(s), s.String(), want)
		}
	}
}

func TestStateTerminal(t *testing.T) {
	terminal := []State{StateCancelled, StateInterrupted, StateFailed, StateCompleted}
	for _, s := range terminal {
		if !s.IsTerminal() {
			t.Errorf("%s should be terminal", s)
		}
		if s.IsActive() {
			t.Errorf("%s should not be active", s)
		}
	}
	nonTerminal := []State{StateIdle, StateValidating, StateReady, StateRunning, StateWaiting, StateCancelling}
	for _, s := range nonTerminal {
		if s.IsTerminal() {
			t.Errorf("%s should not be terminal", s)
		}
	}
	if !StateRunning.IsActive() || !StateWaiting.IsActive() {
		t.Error("RUNNING/WAITING should be active")
	}
}

func TestOwnerStrings(t *testing.T) {
	if OwnerFailsafe.String() != "failsafe" || OwnerOperator.String() != "operator" {
		t.Errorf("owner string mismatch: %s %s", OwnerFailsafe, OwnerOperator)
	}
}
