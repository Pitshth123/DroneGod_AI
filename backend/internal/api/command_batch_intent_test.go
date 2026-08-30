package api

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// The lease guard closes the last check-to-send race.  If an old transport
// write has already entered its tiny commit section, acceptance of the newer
// takeover waits for that write to finish.  Once the newer takeover is accepted,
// no write using the stale lease can begin, even after the newer lease releases.
func TestTakeoverAcceptanceIsAtomicWithOldFinalTransportWrite(t *testing.T) {
	s := newReservationServer()
	old := s.prepareNormalBatch(context.Background(), "Takeoff", "", []uint32{1})
	defer old.release(s)
	claim := old.claims[1]

	writeEntered := make(chan struct{})
	releaseWrite := make(chan struct{})
	writeDone := make(chan error, 1)
	go func() {
		writeDone <- claim.lease.guard.DoSend(func() error {
			close(writeEntered)
			<-releaseWrite
			return nil
		})
	}()
	<-writeEntered

	takeoverDone := make(chan *cmdBatch, 1)
	go func() {
		takeoverDone <- s.prepareTakeoverBatch(
			context.Background(), "StopAll", "", []uint32{1}, takeoverPriorityStopAll)
	}()
	select {
	case <-takeoverDone:
		t.Fatal("newer takeover was published while an older transport write was still committing")
	case <-time.After(25 * time.Millisecond):
	}

	close(releaseWrite)
	if err := <-writeDone; err != nil {
		t.Fatalf("write committed before takeover should complete normally: %v", err)
	}
	newer := <-takeoverDone
	newer.release(s)

	var staleWrites atomic.Int32
	err := claim.lease.guard.DoSend(func() error {
		staleWrites.Add(1)
		return nil
	})
	if err == nil || staleWrites.Load() != 0 {
		t.Fatalf("stale write began after takeover acceptance: err=%v writes=%d", err, staleWrites.Load())
	}
}

// A newer stronger takeover on a later target must permanently invalidate the
// older batch's future send. Releasing the newer lease must never make the stale
// older claim current again.
func TestOldTakeoverBatchCannotResumeLaterTargetAfterNewerStopCompletes(t *testing.T) {
	s := newReservationServer()
	old := s.prepareTakeoverBatch(context.Background(), "Land", "old-land", []uint32{1, 2}, takeoverPriorityLand)
	defer old.release(s)
	if !s.claimCurrent(old.claims[1]) || !s.claimCurrent(old.claims[2]) {
		t.Fatal("old batch must claim both targets before its first send")
	}

	newer := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{2}, takeoverPriorityStopAll)
	if newer.rejected[2] != nil || !s.claimCurrent(newer.claims[2]) {
		t.Fatalf("newer StopAll must own D2: rejected=%+v", newer.rejected[2])
	}
	if !s.claimSuperseded(old.claims[2]) {
		t.Fatal("newer StopAll must permanently supersede old LAND claim on D2")
	}
	newer.release(s)
	if !s.claimSuperseded(old.claims[2]) {
		t.Fatal("releasing newer StopAll must not resurrect the stale old LAND claim")
	}

	var sends atomic.Int32
	r := s.runClaimed("Land", "old-land", old.claims[2], func(context.Context, uint32) *pb.CommandResult {
		sends.Add(1)
		return &pb.CommandResult{Ok: true}
	})
	if sends.Load() != 0 {
		t.Fatal("stale old LAND reached its send after newer StopAll completed")
	}
	if r == nil || r.Ok {
		t.Fatalf("stale claim must be rejected as preempted: %+v", r)
	}
}

// The same stale-later-target bug also applies to normal batched commands such
// as Takeoff: a future target is claimed at request start, so a newer takeover
// can replace it and the old batch can never command it afterwards.
func TestOldNormalBatchCannotResumeLaterTargetAfterTakeoverCompletes(t *testing.T) {
	s := newReservationServer()
	old := s.prepareNormalBatch(context.Background(), "Takeoff", "old-takeoff", []uint32{1, 2})
	defer old.release(s)
	if len(old.rejected) != 0 {
		t.Fatalf("unexpected normal batch rejection: %+v", old.rejected)
	}

	newer := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{2}, takeoverPriorityStopAll)
	if newer.rejected[2] != nil {
		t.Fatalf("newer takeover unexpectedly rejected: %+v", newer.rejected[2])
	}
	newer.release(s)

	var sends atomic.Int32
	r := s.runClaimed("Takeoff", "old-takeoff", old.claims[2], func(context.Context, uint32) *pb.CommandResult {
		sends.Add(1)
		return &pb.CommandResult{Ok: true}
	})
	if sends.Load() != 0 || r == nil || r.Ok {
		t.Fatalf("stale Takeoff later-target send must stay invalid: sends=%d result=%+v", sends.Load(), r)
	}
}

// Rollback is subordinate to the original Takeoff claim. Once a newer operator
// command owns that drone, rollback LAND cannot be emitted even after the newer
// command has completed and released its lease.
func TestTakeoffRollbackClaimCannotResumeAfterNewerTakeover(t *testing.T) {
	s := newReservationServer()
	old := s.prepareNormalBatch(context.Background(), "Takeoff", "old-takeoff", []uint32{1})
	defer old.release(s)
	claim := old.claims[1]

	newer := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{1}, takeoverPriorityStopAll)
	newer.release(s)
	if !s.claimSuperseded(claim) {
		t.Fatal("old Takeoff claim must remain superseded after newer takeover releases")
	}

	var rollbackSends atomic.Int32
	r := s.runClaimed("TakeoffRollbackLand", "", claim, func(context.Context, uint32) *pb.CommandResult {
		rollbackSends.Add(1)
		return &pb.CommandResult{Ok: true}
	})
	if rollbackSends.Load() != 0 || r == nil || r.Ok {
		t.Fatalf("stale rollback must not send: sends=%d result=%+v", rollbackSends.Load(), r)
	}
}

// A weaker new command that is rejected by an active stronger takeover must not
// disturb the stronger claim.
func TestRejectedWeakerBatchDoesNotInvalidateStrongerClaim(t *testing.T) {
	s := newReservationServer()
	strong := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{5}, takeoverPriorityStopAll)
	defer strong.release(s)
	if !s.claimCurrent(strong.claims[5]) {
		t.Fatal("strong takeover claim missing")
	}

	weak := s.prepareTakeoverBatch(context.Background(), "Hold", "weak-hold", []uint32{5}, takeoverPriorityNavigation)
	defer weak.release(s)
	if weak.rejected[5] == nil {
		t.Fatal("weaker takeover must be rejected while stronger takeover is active")
	}
	if !s.claimCurrent(strong.claims[5]) {
		t.Fatal("rejected weaker takeover invalidated the stronger claim")
	}
}

// Exact reviewer reproduction: an older multi-drone LAND is blocked on D1. A
// newer StopAll for D2 fully executes and releases before LAND reaches D2. The
// old LAND must still be stale and must never call its D2 send.
func TestSequentialOldLandCannotSendD2AfterStopAllCompleted(t *testing.T) {
	s := newReservationServer()
	landEnteredD1 := make(chan struct{})
	releaseD1 := make(chan struct{})
	landDone := make(chan *pb.CommandResult, 1)
	var landD2Sends atomic.Int32

	go func() {
		landDone <- s.runTakeoverBatch(context.Background(), "Land", "",
			[]uint32{1, 2}, takeoverPriorityLand,
			func(_ context.Context, id uint32) *pb.CommandResult {
				if id == 1 {
					close(landEnteredD1)
					<-releaseD1
				} else if id == 2 {
					landD2Sends.Add(1)
				}
				return &pb.CommandResult{Ok: true, DroneId: id, Command: "Land"}
			})
	}()
	<-landEnteredD1

	stop := s.runTakeoverBatch(context.Background(), "StopAll", "",
		[]uint32{2}, takeoverPriorityStopAll,
		func(_ context.Context, id uint32) *pb.CommandResult {
			return &pb.CommandResult{Ok: true, DroneId: id, Command: "StopAll"}
		})
	if stop == nil || !stop.Ok {
		t.Fatalf("newer StopAll must complete successfully: %+v", stop)
	}

	close(releaseD1)
	result := <-landDone
	if landD2Sends.Load() != 0 {
		t.Fatalf("stale LAND sent D2 after newer StopAll completed: sends=%d", landD2Sends.Load())
	}
	if result == nil || result.Ok {
		t.Fatalf("older LAND batch must report the superseded D2 instead of success: %+v", result)
	}
}

func TestEveryOverlappingOlderBatchStaysInvalidAfterNewerStopCompletes(t *testing.T) {
	cases := []struct {
		command  string
		priority takeoverPriority
	}{
		{"RTL", takeoverPriorityRTL},
		{"Hold", takeoverPriorityNavigation},
		{"Disarm", takeoverPriorityDisarm},
		{"ChangeAlt", takeoverPriorityNavigation},
		{"EqualizeAlt", takeoverPriorityNavigation},
	}
	for _, tc := range cases {
		t.Run(tc.command, func(t *testing.T) {
			s := newReservationServer()
			enteredD1 := make(chan struct{})
			releaseD1 := make(chan struct{})
			done := make(chan *pb.CommandResult, 1)
			var staleD2Sends atomic.Int32
			go func() {
				done <- s.runTakeoverBatch(
					context.Background(), tc.command, "", []uint32{1, 2}, tc.priority,
					func(_ context.Context, id uint32) *pb.CommandResult {
						if id == 1 {
							close(enteredD1)
							<-releaseD1
						} else {
							staleD2Sends.Add(1)
						}
						return &pb.CommandResult{Ok: true, DroneId: id, Command: tc.command}
					})
			}()
			<-enteredD1
			stop := s.runTakeoverBatch(
				context.Background(), "StopAll", "", []uint32{2}, takeoverPriorityStopAll,
				func(_ context.Context, id uint32) *pb.CommandResult {
					return &pb.CommandResult{Ok: true, DroneId: id, Command: "StopAll"}
				})
			if stop == nil || !stop.Ok {
				t.Fatalf("new StopAll failed: %+v", stop)
			}
			close(releaseD1)
			result := <-done
			if staleD2Sends.Load() != 0 || result == nil || result.Ok {
				t.Fatalf("stale %s resumed D2: sends=%d result=%+v", tc.command, staleD2Sends.Load(), result)
			}
		})
	}
}

func TestEqualPriorityNewerBatchWinsOnlyOverlappingTargets(t *testing.T) {
	s := newReservationServer()
	old := s.prepareTakeoverBatch(
		context.Background(), "Hold", "", []uint32{1, 2, 3}, takeoverPriorityNavigation)
	defer old.release(s)
	newer := s.prepareTakeoverBatch(
		context.Background(), "ChangeAlt", "", []uint32{2, 3, 4}, takeoverPriorityNavigation)
	defer newer.release(s)

	if !s.claimCurrent(old.claims[1]) {
		t.Fatal("non-overlapping old target D1 lost ownership")
	}
	for _, id := range []uint32{2, 3} {
		if !s.claimSuperseded(old.claims[id]) || !s.claimCurrent(newer.claims[id]) {
			t.Fatalf("equal-priority newer intent did not replace overlap D%d", id)
		}
	}
	if !s.claimCurrent(newer.claims[4]) {
		t.Fatal("new non-overlapping target D4 was not claimed")
	}
}

func TestTargetIDsCanonicalizesDuplicateTargetsBeforeReservation(t *testing.T) {
	ids := targetIDs(&pb.Target{DroneIds: []uint32{2, 1, 2, 3, 1}})
	want := []uint32{2, 1, 3}
	if len(ids) != len(want) {
		t.Fatalf("canonical targets=%v want=%v", ids, want)
	}
	for i := range want {
		if ids[i] != want[i] {
			t.Fatalf("canonical targets=%v want=%v", ids, want)
		}
	}
}

func TestTakeoverBatchSendsDuplicateTargetOnlyOnce(t *testing.T) {
	s := newReservationServer()
	ids := targetIDs(&pb.Target{DroneIds: []uint32{7, 7}})
	var sends atomic.Int32
	r := s.runTakeoverBatch(
		context.Background(), "Hold", "", ids, takeoverPriorityNavigation,
		func(_ context.Context, id uint32) *pb.CommandResult {
			sends.Add(1)
			return &pb.CommandResult{Ok: true, DroneId: id, Command: "Hold"}
		})
	if r == nil || !r.Ok || sends.Load() != 1 {
		t.Fatalf("duplicate target must send once: sends=%d result=%+v", sends.Load(), r)
	}
}

// Preemption classification is based on the lease token, not result-message
// wording. Even a cancellation that surfaces as an ordinary-looking FC error
// remains typed as superseded and therefore cannot authorize rollback.
func TestPreemptionDoesNotDependOnFailureMessage(t *testing.T) {
	s := newReservationServer()
	old := s.prepareNormalBatch(context.Background(), "Takeoff", "", []uint32{1})
	claim := old.claims[1]
	defer old.release(s)

	started := make(chan struct{})
	finished := make(chan *pb.CommandResult, 1)
	go func() {
		finished <- s.runClaimed("Takeoff", "", claim, func(ctx context.Context, id uint32) *pb.CommandResult {
			close(started)
			<-ctx.Done()
			return &pb.CommandResult{Ok: false, DroneId: id, Command: "Takeoff", Message: "set GUIDED failed"}
		})
	}()
	<-started
	newer := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{1}, takeoverPriorityStopAll)
	newer.release(s)
	got := <-finished
	if got == nil || got.Ok {
		t.Fatalf("preempted Takeoff must fail: %+v", got)
	}
	if !s.claimSuperseded(claim) {
		t.Fatal("typed claim must remain superseded even though result text lacks 'preempted'")
	}
	var rollbackSends atomic.Int32
	if s.claimCurrent(claim) {
		rollbackSends.Add(1)
	}
	if rollbackSends.Load() != 0 {
		t.Fatal("superseded Takeoff claim would have authorized stale rollback")
	}
}
