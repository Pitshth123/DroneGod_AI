package command

import (
	"sync"
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// IDEM-001: request_id เดียวกัน (ลำเดียวกัน) → fn ยิงครั้งเดียว, ครั้งถัดไปคืน cache
func TestIdempotentDedup(t *testing.T) {
	s := newIdemStore(time.Minute)
	var calls atomic.Int32
	fn := func() *pb.CommandResult {
		calls.Add(1)
		return &pb.CommandResult{Ok: true, DroneId: 1, Command: "Arm", Message: "ACCEPTED",
			Outcome: pb.CommandOutcome_OUTCOME_ACCEPTED}
	}

	r1, _ := s.do("req-A", 1, fn)
	r2, replay2 := s.do("req-A", 1, fn) // ซ้ำ → ต้องไม่ยิง fn อีก
	r3, _ := s.do("req-A", 1, fn)
	if !replay2 {
		t.Fatal("ครั้งที่ 2 ควรเป็น replay=true")
	}
	if r2.Outcome != pb.CommandOutcome_OUTCOME_ACCEPTED {
		t.Fatalf("replay ควรคง Outcome เดิม, ได้ %v", r2.Outcome)
	}

	if calls.Load() != 1 {
		t.Fatalf("fn ถูกเรียก %d ครั้ง คาดว่า 1 (idempotent)", calls.Load())
	}
	if !r1.Ok || !r2.Ok || !r3.Ok {
		t.Fatal("ผลควร Ok ทุกครั้ง")
	}
	if r2.Message == r1.Message {
		t.Fatalf("replay ควรมีป้าย idempotent: r1=%q r2=%q", r1.Message, r2.Message)
	}
}

// คนละ drone (parent request เดียวกัน) = คนละ child key → ยิงแยกกัน (group partial)
func TestIdempotentPerDrone(t *testing.T) {
	s := newIdemStore(time.Minute)
	var calls atomic.Int32
	fn := func() *pb.CommandResult {
		calls.Add(1)
		return &pb.CommandResult{Ok: true}
	}
	s.do("group-1", 1, fn)
	s.do("group-1", 2, fn) // drone ต่างกัน → ยิงจริง
	s.do("group-1", 1, fn) // ซ้ำ drone 1 → ไม่ยิง
	if calls.Load() != 2 {
		t.Fatalf("คาดว่ายิง 2 (ต่อ drone), ได้ %d", calls.Load())
	}
}

// request_id ว่าง = ไม่ dedup (backward compatible)
func TestIdempotentEmptyID(t *testing.T) {
	s := newIdemStore(time.Minute)
	var calls atomic.Int32
	fn := func() *pb.CommandResult { calls.Add(1); return &pb.CommandResult{} }
	s.do("", 1, fn)
	s.do("", 1, fn)
	if calls.Load() != 2 {
		t.Fatalf("empty id ไม่ควร dedup: calls=%d", calls.Load())
	}
}

// retention หมดอายุ → ยิงใหม่ได้
func TestIdempotentExpiry(t *testing.T) {
	s := newIdemStore(50 * time.Millisecond)
	var calls atomic.Int32
	fn := func() *pb.CommandResult { calls.Add(1); return &pb.CommandResult{Ok: true} }
	s.do("req-X", 1, fn)
	time.Sleep(120 * time.Millisecond) // เกิน retention
	s.do("req-X", 1, fn)
	if calls.Load() != 2 {
		t.Fatalf("หลังหมดอายุควรยิงใหม่: calls=%d", calls.Load())
	}
}

// concurrent: ยิง request_id เดียวกันพร้อมกัน N ตัว → fn ทำงานครั้งเดียว (in-flight guard)
func TestIdempotentConcurrent(t *testing.T) {
	s := newIdemStore(time.Minute)
	var calls atomic.Int32
	fn := func() *pb.CommandResult {
		calls.Add(1)
		time.Sleep(30 * time.Millisecond) // จำลองงานที่ใช้เวลา
		return &pb.CommandResult{Ok: true, Command: "Takeoff"}
	}

	const N = 20
	var wg sync.WaitGroup
	oks := make([]*pb.CommandResult, N)
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			oks[idx], _ = s.do("same-req", 7, fn)
		}(i)
	}
	wg.Wait()

	if calls.Load() != 1 {
		t.Fatalf("concurrent same request_id ควรยิง fn ครั้งเดียว ได้ %d", calls.Load())
	}
	for i, r := range oks {
		if r == nil || !r.Ok {
			t.Fatalf("goroutine %d ได้ผลไม่ครบ: %v", i, r)
		}
	}
}
