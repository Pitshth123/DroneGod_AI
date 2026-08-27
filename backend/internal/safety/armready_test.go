package safety

import (
	"testing"

	"github.com/swarmgod/backend/internal/config"
)

// เคสจริงจาก mission log 13:19:35 — สั่ง takeoff พร้อมกัน 5 ลำ
// D4/D5 ยัง sat=0/fix=0 (GPS ยังไม่มา) แต่อีก 3 วินาทีต่อมาได้ sat=10
// GPS ที่ยังไม่พร้อมต้องเป็น Transient (รอได้) ไม่ใช่ปฏิเสธทิ้งถาวร
func envForTest() *Envelope {
	return New(config.Config{
		MinArmBatteryPct: 25,
		MinSatCount:      6,
		MaxAltM:          120,
	})
}

func TestNoGpsFixIsTransient(t *testing.T) {
	e := envForTest()
	d := e.CheckArmPrecondition(DroneState{ID: 4, BatteryPct: 100, GpsFix: 0, SatCount: 0})
	if d.Allow {
		t.Fatal("ต้องไม่อนุญาตให้ arm ตอนไม่มี GPS fix")
	}
	if !d.Transient {
		t.Fatal("GPS ยังไม่ fix ต้องเป็น Transient (รอได้) — ไม่งั้นลำที่ GPS มาช้าถูกทิ้งคำสั่ง")
	}
}

func TestLowSatCountIsTransient(t *testing.T) {
	e := envForTest()
	d := e.CheckArmPrecondition(DroneState{ID: 5, BatteryPct: 100, GpsFix: 3, SatCount: 2})
	if d.Allow {
		t.Fatal("ต้องไม่อนุญาตให้ arm ตอน sat ไม่พอ")
	}
	if !d.Transient {
		t.Fatal("sat ไม่พอ ต้องเป็น Transient (รอได้)")
	}
}

// แบตต่ำ = รอไปก็ไม่ดีขึ้น ต้องปฏิเสธทันที ห้ามค้างรอ
func TestLowBatteryIsNotTransient(t *testing.T) {
	e := envForTest()
	d := e.CheckArmPrecondition(DroneState{ID: 1, BatteryPct: 10, GpsFix: 3, SatCount: 10})
	if d.Allow {
		t.Fatal("แบตต่ำต้องไม่ให้ arm")
	}
	if d.Transient {
		t.Fatal("แบตต่ำต้องไม่ใช่ Transient — ไม่ควรค้างรอจนหมดเวลา")
	}
}

// ความปลอดภัยต้องไม่ถูกลดทอน: พร้อมจริงเท่านั้นจึงอนุญาต
func TestReadyStateAllowed(t *testing.T) {
	e := envForTest()
	d := e.CheckArmPrecondition(DroneState{ID: 1, BatteryPct: 100, GpsFix: 3, SatCount: 10})
	if !d.Allow {
		t.Fatalf("สถานะพร้อมแล้วต้องอนุญาต แต่ถูกปฏิเสธ: %s", d.Reason)
	}
}
