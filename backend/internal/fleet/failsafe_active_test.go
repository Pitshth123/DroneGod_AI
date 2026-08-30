package fleet

import (
	"sync/atomic"
	"testing"
	"time"
)

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

func TestDoIfFailsafeInactiveMakesLatchAndFinalWriteAtomic(t *testing.T) {
	m := newTestManager(t, newFakeReg())
	started := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	var writes atomic.Int32
	go func() {
		done <- m.DoIfFailsafeInactive(3, func() error {
			close(started)
			<-release
			writes.Add(1)
			return nil
		})
	}()
	<-started

	latched := make(chan struct{})
	go func() {
		m.fsMu.Lock()
		m.fsBatt[3] = true
		m.fsMu.Unlock()
		close(latched)
	}()
	select {
	case <-latched:
		t.Fatal("failsafe latch crossed a transport write already inside the atomic boundary")
	case <-time.After(20 * time.Millisecond):
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatalf("write that began before the latch should finish: %v", err)
	}
	select {
	case <-latched:
	case <-time.After(time.Second):
		t.Fatal("failsafe latch did not establish after the prior write finished")
	}

	called := false
	if err := m.DoIfFailsafeInactive(3, func() error {
		called = true
		return nil
	}); err == nil || called {
		t.Fatalf("write after failsafe latch escaped: err=%v called=%v", err, called)
	}
	if got := writes.Load(); got != 1 {
		t.Fatalf("transport writes=%d, want exactly the pre-latch write", got)
	}
}
