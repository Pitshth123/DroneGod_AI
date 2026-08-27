package swarm

import "testing"

// ─────────────────────────────────────────────────────────────
// Regression: SetLeader เดิม "ไม่ได้ถูก implement เลย" (มีแค่ใน proto)
// gRPC จึงตอบ Unimplemented → cockpit เปลี่ยน Head ไม่ติด และ swarm poll
// ก็ดึงกลับไปเป็น online[0] (ลำ 1) ทุกครั้ง
//
// เทสต์นี้ล็อกกติกา pickLeader: ตัวที่ผู้ใช้ปักหมุด (pinnedID) ต้องชนะ
// การเลือกอัตโนมัติเสมอ ตราบใดที่ยัง online
// ─────────────────────────────────────────────────────────────

func TestPickLeaderDefaultsToFirstOnline(t *testing.T) {
	m := &Manager{}
	if got := m.pickLeader([]uint32{2, 3, 5}); got != 2 {
		t.Fatalf("ไม่ได้ปักหมุด ต้องได้ตัวแรก online (2) แต่ได้ %d", got)
	}
}

func TestPinnedLeaderWinsOverAutoPick(t *testing.T) {
	m := &Manager{pinnedID: 3}
	if got := m.pickLeader([]uint32{1, 2, 3, 4}); got != 3 {
		t.Fatalf("ตัวที่ผู้ใช้เลือก (3) ต้องชนะ auto-pick แต่ได้ %d", got)
	}
}

func TestPinnedLeaderIgnoredWhenOffline(t *testing.T) {
	// ลำที่ปักหมุดหลุด → ต้อง failover ไปตัวถัดไป ไม่ค้างรอ
	m := &Manager{pinnedID: 9}
	if got := m.pickLeader([]uint32{1, 2, 3}); got != 1 {
		t.Fatalf("ลำที่ปักหมุดหลุดแล้ว ต้อง failover เป็น 1 แต่ได้ %d", got)
	}
}

func TestUnpinReturnsToAutoPick(t *testing.T) {
	m := &Manager{pinnedID: 4}
	if got := m.pickLeader([]uint32{1, 4}); got != 4 {
		t.Fatalf("want pinned 4, got %d", got)
	}
	m.pinnedID = 0 // ปลดหมุด (Auto)
	if got := m.pickLeader([]uint32{1, 4}); got != 1 {
		t.Fatalf("ปลดหมุดแล้วต้องกลับไป auto (1) แต่ได้ %d", got)
	}
}

func TestPinnedLeaderSurvivesRepeatedPicks(t *testing.T) {
	// จำลอง tick ซ้ำ ๆ — ต้องไม่ค่อย ๆ ไหลกลับไปเป็น online[0]
	m := &Manager{pinnedID: 5}
	online := []uint32{1, 2, 5}
	for i := 0; i < 20; i++ {
		if got := m.pickLeader(online); got != 5 {
			t.Fatalf("tick ที่ %d: leader ไหลกลับเป็น %d (ต้องคงเป็น 5)", i, got)
		}
	}
}
