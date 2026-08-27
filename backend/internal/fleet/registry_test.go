package fleet

import (
	"sync"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/safety"
	"github.com/swarmgod/backend/internal/telemetry"
)

// fakeReg บันทึกทุกครั้งที่ UpsertVehicle ถูกเรียก
type fakeReg struct {
	mu    sync.Mutex
	calls map[string]upsertCall // key = uuid
}
type upsertCall struct {
	sysID  int
	name   string
	serial string
}

func newFakeReg() *fakeReg { return &fakeReg{calls: map[string]upsertCall{}} }

func (f *fakeReg) UpsertVehicle(uuid string, systemID, componentID int, displayName, serial string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls[uuid] = upsertCall{sysID: systemID, name: displayName, serial: serial}
	return nil
}

// mkDrone สร้าง Drone แบบไม่ต้องมี connection จริง (conn=nil) สำหรับ white-box test
func mkDrone(id uint32, name string, sysID byte, serial uint64, verified bool) *Drone {
	d := newDrone(id, name, "host", 5760, nil)
	d.verified = verified
	d.targetSystem = sysID
	d.serial = serial
	return d
}

func newTestManager(t *testing.T, reg VehicleRegistry) *Manager {
	t.Helper()
	cfg := config.Default()
	cfg.MAVLinkSignKey = "" // กัน os.ReadFile ไปโหลด key จริง
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatalf("audit.New: %v", err)
	}
	t.Cleanup(aud.Close)
	return NewManager(cfg, telemetry.New(), aud, events.New(), safety.New(cfg), reg)
}

// SAFE-DUPID-001: ตรวจ duplicate System ID เฉพาะลำที่ verified
func TestDuplicateSystemID(t *testing.T) {
	m := newTestManager(t, newFakeReg())
	m.drones[1] = mkDrone(1, "UAV_1", 1, 0, true)      // sysid 1
	m.drones[2] = mkDrone(2, "UAV_2", 1, 0, true)      // sysid 1 ← ชนกับ 1
	m.drones[3] = mkDrone(3, "UAV_3", 2, 0x1234, true) // sysid 2 unique
	m.drones[4] = mkDrone(4, "UAV_4", 1, 0, false)     // sysid 1 แต่ยังไม่ verified → ไม่นับ

	if other, dup := m.DuplicateSystemID(1); !dup || other != 2 {
		t.Fatalf("drone 1 should collide with 2: got other=%d dup=%v", other, dup)
	}
	if _, dup := m.DuplicateSystemID(3); dup {
		t.Fatalf("drone 3 (unique sysid) should not be duplicate")
	}
	if _, dup := m.DuplicateSystemID(4); dup {
		t.Fatalf("unverified drone 4 must not count as duplicate")
	}
	if _, dup := m.DuplicateSystemID(99); dup {
		t.Fatalf("unknown drone must not be duplicate")
	}
}

// VEH-REG-001: reconcile upsert เฉพาะลำ verified + uuid ถูกต้อง (hw- / name-)
func TestReconcileRegistry(t *testing.T) {
	reg := newFakeReg()
	m := newTestManager(t, reg)
	m.drones[1] = mkDrone(1, "UAV_1", 5, 0, true)      // ไม่มี serial → name-UAV_1
	m.drones[2] = mkDrone(2, "UAV_2", 6, 0x1234, true) // มี serial → hw-...1234
	m.drones[3] = mkDrone(3, "UAV_3", 7, 0, false)     // ยังไม่ verified → ไม่ upsert

	m.reconcileRegistry()

	reg.mu.Lock()
	defer reg.mu.Unlock()
	if len(reg.calls) != 2 {
		t.Fatalf("expected 2 upserts (verified only), got %d: %+v", len(reg.calls), reg.calls)
	}
	if c, ok := reg.calls["name-UAV_1"]; !ok || c.sysID != 5 || c.serial != "" {
		t.Fatalf("name-based uuid wrong: %+v ok=%v", c, ok)
	}
	if c, ok := reg.calls["hw-0000000000001234"]; !ok || c.sysID != 6 || c.serial != "0000000000001234" {
		t.Fatalf("hw-based uuid wrong: %+v ok=%v", c, ok)
	}
	if _, ok := reg.calls["name-UAV_3"]; ok {
		t.Fatalf("unverified drone 3 must not be upserted")
	}
}

// duplicate System ID ต้อง publish WARN event (ครั้งเดียว)
func TestReconcilePublishesDuplicateWarning(t *testing.T) {
	m := newTestManager(t, newFakeReg())
	subID, ch := m.events.Subscribe()
	defer m.events.Unsubscribe(subID)

	m.drones[1] = mkDrone(1, "UAV_1", 1, 0, true)
	m.drones[2] = mkDrone(2, "UAV_2", 1, 0, true) // dup sysid

	m.reconcileRegistry()

	select {
	case ev := <-ch:
		if ev.Level != pb.EventLevel_EVENT_LEVEL_WARN || ev.Category != "identity" {
			t.Fatalf("wrong event: level=%v cat=%q", ev.Level, ev.Category)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("expected duplicate-System-ID WARN event, got none")
	}

	// เรียกซ้ำไม่ควรเตือนอีก (dupWarned sticky)
	m.reconcileRegistry()
	select {
	case ev := <-ch:
		t.Fatalf("should not warn twice, got %q", ev.Category)
	case <-time.After(200 * time.Millisecond):
		// ok — ไม่มี event ซ้ำ
	}
}
