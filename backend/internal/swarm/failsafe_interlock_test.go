package swarm

import (
	"context"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/events"
)

// follower ที่ Core กำลัง failsafe RTL ต้องถูกตัดออกจากเป้าหมาย formation (§9)
// โดยลำที่เหลือต้องคง slot เดิม (ลำที่ข้ามไม่ทำให้ลำอื่นเลื่อนตำแหน่ง)
func TestPlanFormationTargetsSkipsFailsafeFollower(t *testing.T) {
	online := []uint32{1, 2, 3, 4}
	const leader = uint32(1)
	noFail := func(uint32) bool { return false }

	all := planFormationTargets(online, leader, 14.0, 100.0, 20.0, 0, 10.0,
		pb.Formation_FORMATION_LINE, pb.HeadingMode(0), noFail)
	if len(all) != 3 {
		t.Fatalf("ไม่มี failsafe ต้องได้ 3 follower, ได้ %d", len(all))
	}

	fs := func(id uint32) bool { return id == 3 }
	got := planFormationTargets(online, leader, 14.0, 100.0, 20.0, 0, 10.0,
		pb.Formation_FORMATION_LINE, pb.HeadingMode(0), fs)
	byID := map[uint32]followerCmd{}
	for _, c := range got {
		byID[c.id] = c
	}
	if _, skipped := byID[3]; skipped {
		t.Fatal("follower 3 กำลัง failsafe RTL — ต้องไม่ถูกสั่ง target ใหม่")
	}
	if len(got) != 2 {
		t.Fatalf("ควรเหลือ 2 ลำที่สั่งได้, ได้ %d", len(got))
	}

	// slot stability: ตำแหน่งของ follower 4 ต้องไม่ขยับเมื่อ 3 failsafe
	var all4 followerCmd
	for _, c := range all {
		if c.id == 4 {
			all4 = c
		}
	}
	if byID[4].lat != all4.lat || byID[4].lon != all4.lon || byID[4].alt != all4.alt {
		t.Fatalf("follower 4 เลื่อน slot เมื่อ 3 failsafe: %v vs %v", byID[4], all4)
	}
}

func TestPlanFormationTargetsNilPredicate(t *testing.T) {
	got := planFormationTargets([]uint32{1, 2}, 1, 14.0, 100.0, 20.0, 0, 10.0,
		pb.Formation_FORMATION_LINE, pb.HeadingMode(0), nil)
	if len(got) != 1 || got[0].id != 2 {
		t.Fatalf("nil predicate ต้องสั่งทุก follower: %v", got)
	}
}

// ตัวแม่ failsafe → halt แบบ fail-closed + ยิง ALARM ครั้งเดียว (idempotent)
func TestHaltFormationLatchesAndAlarms(t *testing.T) {
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer aud.Close()
	bus := events.New()
	_, ch := bus.Subscribe()
	m := &Manager{audit: aud, events: bus, leaderID: 2}

	m.haltFormation("leader Drone 2 failsafe")
	if !m.halted {
		t.Fatal("halt ต้อง latch (halted=true)")
	}
	select {
	case ev := <-ch:
		if ev.Level != pb.EventLevel_EVENT_LEVEL_ALARM {
			t.Fatalf("ต้องเป็น ALARM, ได้ %v", ev.Level)
		}
	case <-time.After(time.Second):
		t.Fatal("ไม่ได้รับ ALARM event")
	}

	// idempotent — เรียกซ้ำไม่ยิง ALARM ซ้ำ
	m.haltFormation("again")
	select {
	case ev := <-ch:
		t.Fatalf("halt ซ้ำไม่ควรยิง event เพิ่ม: %v", ev)
	case <-time.After(50 * time.Millisecond):
	}
}

// halted latch → tick คืนทันที ไม่แตะ fleet และไม่ auto-resume
func TestHaltedTickReturnsEarlyNoAutoResume(t *testing.T) {
	m := &Manager{halted: true} // fleet=nil: ถ้าไม่ return ก่อนจะ panic
	for i := 0; i < 3; i++ {
		m.tick() // ต้องไม่ panic และไม่ทำงานต่อ
	}
	if !m.halted {
		t.Fatal("halted ต้องคงอยู่ — mission เก่าห้าม auto-resume")
	}
}

// Stop ต้องล้าง latch (ผู้ใช้เริ่มใหม่ได้ แต่ต้อง Start ใหม่เท่านั้น)
func TestStopClearsHaltedLatch(t *testing.T) {
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer aud.Close()
	_, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	close(done)
	m := &Manager{audit: aud, active: true, halted: true, cancel: cancel, done: done}
	m.Stop()
	if m.halted {
		t.Fatal("Stop ต้องล้าง halted latch")
	}
}
