package fleet

import "testing"

// FailsafeActive ต้องรายงานสถานะ failsafe ที่ Core สั่ง RTL ไปแล้ว
// (battery critical หรือ link lost) — swarm formation loop ใช้กันไม่ให้สั่งทับ (§9)
func TestFailsafeActiveReportsBatteryAndLinkLost(t *testing.T) {
	m := newTestManager(t, newFakeReg())

	if m.FailsafeActive(1) {
		t.Fatal("ยังไม่มี failsafe — ต้องคืน false")
	}

	// battery failsafe
	m.fsMu.Lock()
	m.fsBatt[1] = true
	m.fsMu.Unlock()
	if !m.FailsafeActive(1) {
		t.Fatal("battery failsafe active — ต้องคืน true")
	}

	// battery หาย → false; link lost ของอีกลำ → true
	m.fsMu.Lock()
	m.fsBatt[1] = false
	m.fsLink[2] = 2 // lost
	m.fsMu.Unlock()
	if m.FailsafeActive(1) {
		t.Fatal("battery หายแล้ว — ต้องคืน false")
	}
	if !m.FailsafeActive(2) {
		t.Fatal("link lost — ต้องคืน true")
	}
}

// link "warn" (delayed, ยังไม่สั่ง RTL) ต้องไม่นับเป็น failsafe
func TestFailsafeActiveIgnoresLinkWarn(t *testing.T) {
	m := newTestManager(t, newFakeReg())
	m.fsMu.Lock()
	m.fsLink[3] = 1 // warn
	m.fsMu.Unlock()
	if m.FailsafeActive(3) {
		t.Fatal("link warn (1) ยังไม่ใช่ failsafe RTL — ต้องคืน false")
	}
}
