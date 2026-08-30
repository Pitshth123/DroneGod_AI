package swarm

import (
	"fmt"
	"sync"
	"testing"
)

func TestReturnCompletionExposesCurrentOrAlreadyClosedBoundary(t *testing.T) {
	m := &Manager{}
	select {
	case <-m.ReturnCompletion():
	default:
		t.Fatal("inactive Return completion must already be closed")
	}
	done := make(chan struct{})
	m.returnMu.Lock()
	m.returnDone = done
	m.returnMu.Unlock()
	if got := m.ReturnCompletion(); got != done {
		t.Fatal("did not expose current Return completion boundary")
	}
}

func TestReturnResultsDistinguishSuccessCancellationAndFailure(t *testing.T) {
	for _, outcome := range []ReturnOutcome{
		ReturnOutcomeSucceeded, ReturnOutcomeCancelled, ReturnOutcomeFailed,
	} {
		ch := make(chan ReturnResult, 1)
		ch <- ReturnResult{Outcome: outcome, Reason: fmt.Sprint(outcome)}
		m := &Manager{returnResult: ch}
		got := <-m.ReturnResults()
		if got.Outcome != outcome {
			t.Fatalf("got outcome %d, want %d", got.Outcome, outcome)
		}
	}
}

type fakeReturnFailsafeOwner struct {
	mu     sync.Mutex
	active map[uint32]bool
}

func (o *fakeReturnFailsafeOwner) DoIfFailsafeInactive(id uint32, send func() error) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.active[id] {
		return fmt.Errorf("failsafe active for Drone %d", id)
	}
	return send()
}

func (o *fakeReturnFailsafeOwner) latch(id uint32) {
	o.mu.Lock()
	if o.active == nil {
		o.active = make(map[uint32]bool)
	}
	o.active[id] = true
	o.mu.Unlock()
}

func TestReturnFinalWriteGuardBlocksGotoLandAndRTLAfterFailsafe(t *testing.T) {
	owner := &fakeReturnFailsafeOwner{active: make(map[uint32]bool)}
	navigation := &navigationSendGuard{}
	guard := &returnParticipantSendGuard{navigation: navigation, owner: owner, id: 3}
	owner.latch(3) // failsafe begins after Return registration, before first write

	for _, command := range []string{"GOTO", "LAND", "RTL"} {
		t.Run(command, func(t *testing.T) {
			called := false
			if err := guard.DoSend(func() error {
				called = true
				return nil
			}); err == nil || called {
				t.Fatalf("failsafe-owned D3 received %s: err=%v called=%v", command, err, called)
			}
		})
	}
}

func TestReturnFinalWriteGuardSuppressesStaleNextWriteBetweenParticipants(t *testing.T) {
	owner := &fakeReturnFailsafeOwner{active: make(map[uint32]bool)}
	navigation := &navigationSendGuard{}
	guard := &returnParticipantSendGuard{navigation: navigation, owner: owner, id: 3}
	writes := 0
	if err := guard.DoSend(func() error { writes++; return nil }); err != nil {
		t.Fatalf("healthy first write failed: %v", err)
	}
	owner.latch(3) // failsafe wins between Return writes
	if err := guard.DoSend(func() error { writes++; return nil }); err == nil {
		t.Fatal("stale next Return write was not suppressed")
	}
	if writes != 1 {
		t.Fatalf("writes=%d, want only the pre-failsafe write", writes)
	}
}
