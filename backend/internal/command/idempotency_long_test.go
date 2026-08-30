package command

import (
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

func TestIdempotentInflightWaitsBeyondLegacyTenSecondWindow(t *testing.T) {
	s := newIdemStore(time.Minute)
	var calls atomic.Int32
	entered := make(chan struct{})
	release := make(chan struct{})
	fn := func() *pb.CommandResult {
		if calls.Add(1) == 1 {
			close(entered)
		}
		<-release
		return &pb.CommandResult{Ok: true, DroneId: 7, Command: "Takeoff", RequestId: "long-req"}
	}

	firstDone := make(chan struct{})
	go func() {
		defer close(firstDone)
		_, _ = s.do("long-req", 7, fn)
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("original idempotent execution never started")
	}

	type result struct {
		r      *pb.CommandResult
		replay bool
	}
	secondDone := make(chan result, 1)
	go func() {
		r, replay := s.do("long-req", 7, fn)
		secondDone <- result{r: r, replay: replay}
	}()

	// The old implementation started fn again after 10 seconds. Stay beyond that
	// legacy window and prove the retry is still waiting on the original owner.
	time.Sleep(10250 * time.Millisecond)
	if got := calls.Load(); got != 1 {
		t.Fatalf("same request_id executed %d times while original was still in flight; want 1", got)
	}
	select {
	case got := <-secondDone:
		t.Fatalf("retry returned before original completed: %+v", got)
	default:
	}

	close(release)
	select {
	case <-firstDone:
	case <-time.After(2 * time.Second):
		t.Fatal("original did not finish after release")
	}
	select {
	case got := <-secondDone:
		if got.r == nil || !got.r.Ok || !got.replay {
			t.Fatalf("retry must replay original completion: %+v", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("retry did not receive original result")
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("same request_id executed %d times; want exactly 1", got)
	}
}

func TestIdempotentReplayPreservesAggregateShape(t *testing.T) {
	s := newIdemStore(time.Minute)
	original := &pb.CommandResult{
		Ok: true, Command: "Takeoff", RequestId: "aggregate-req",
		PerDrone: []*pb.CommandResult{{Ok: true, DroneId: 1}, {Ok: true, DroneId: 2}},
	}
	_, _ = s.do("aggregate-req", 0, func() *pb.CommandResult { return original })
	replay, isReplay := s.do("aggregate-req", 0, func() *pb.CommandResult {
		t.Fatal("cached aggregate must not execute fn again")
		return nil
	})
	if !isReplay || replay == nil {
		t.Fatalf("expected replay, got replay=%v result=%+v", isReplay, replay)
	}
	if replay.RequestId != original.RequestId || len(replay.PerDrone) != 2 {
		t.Fatalf("aggregate replay lost request/per-drone shape: %+v", replay)
	}
}
