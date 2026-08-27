package command

import (
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/fleet"
)

// ─────────────────────────────────────────────────────────────
// Regression: "กด Cancel Nav ระหว่างบิน Waypoint แล้วโดรนตก"
//
// ต้นตอ: Hold เดิมสั่ง LOITER — ใน ArduCopter โหมด LOITER / ALT_HOLD / POSHOLD
// เอา climb rate จาก "สติ๊กคันเร่งของนักบิน" ไม่ใช่จากตัวคุมอัตโนมัติ
// ระหว่างบินอัตโนมัติสติ๊กคันเร่งอยู่ต่ำสุดเสมอ (หรือไม่มี RC ต่ออยู่เลยอย่างใน SITL)
// FC จึงอ่านว่า "นักบินสั่งลง" แล้วร่วงลงด้วยอัตราสูงสุดทันทีที่สลับโหมด
//
// เทสต์ชุดนี้ล็อกไว้ไม่ให้ใครเผลอเปลี่ยน holdMode กลับไปเป็นโหมดกลุ่มนั้นอีก
// ─────────────────────────────────────────────────────────────

// โหมดที่ climb rate มาจากสติ๊กคันเร่ง = ห้ามใช้เป็นโหมด hold
var pilotThrottleModes = map[pb.FlightMode]string{
	pb.FlightMode_FLIGHT_MODE_LOITER:    "LOITER",
	pb.FlightMode_FLIGHT_MODE_ALT_HOLD:  "ALT_HOLD",
	pb.FlightMode_FLIGHT_MODE_POSHOLD:   "POSHOLD",
	pb.FlightMode_FLIGHT_MODE_STABILIZE: "STABILIZE",
	pb.FlightMode_FLIGHT_MODE_ACRO:      "ACRO",
}

func TestHoldModeIsNotPilotThrottleControlled(t *testing.T) {
	if name, bad := pilotThrottleModes[holdMode]; bad {
		t.Fatalf("holdMode = %s — โหมดนี้เอา climb rate จากสติ๊กคันเร่ง "+
			"โดรนจะร่วงลงทันทีที่ Hold กลางอากาศ", name)
	}
}

func TestHoldModeIsGuidedSoNextCommandWorks(t *testing.T) {
	// GUIDED สำคัญเป็นพิเศษ: คำสั่งอื่นทั้งหมด (Goto/Waypoint/Swarm) ส่ง
	// SET_POSITION_TARGET ซึ่ง FC จะเมินถ้าไม่ได้อยู่ GUIDED
	// ถ้าเปลี่ยนไปใช้ BRAKE ต้องเพิ่มขั้นตอนกลับ GUIDED ให้ทุกคำสั่งถัดไปด้วย
	if holdMode != pb.FlightMode_FLIGHT_MODE_GUIDED {
		t.Fatalf("holdMode = %v — ถ้าไม่ใช่ GUIDED ต้องแก้คำสั่งที่ตามมาให้สลับโหมดเองด้วย",
			holdMode)
	}
}

// ─────────────────────────────────────────────────────────────
// Regression รอบสอง: "กด Cancel Nav แล้วโดรนลง" ที่ยังเหลืออยู่หลังแก้ LOITER
//
// ต้นตอที่สอง: Hold ส่ง Goto(lat, lon, AltRel) โดยไม่ตรวจ AltRel เลย
// แต่ alt ในเฟรม GLOBAL_RELATIVE_ALT คือ "ความสูงเป้าหมาย" ไม่ใช่ "คงไว้เท่าเดิม"
// ถ้า AltRel อ่านได้ 0 (telemetry ยังไม่มา/ค้าง) Hold = สั่งบินลงไปที่พื้น
// ─────────────────────────────────────────────────────────────

func TestHoldAltFloorIsAboveGround(t *testing.T) {
	// ต้องมากกว่า 0 จริง ๆ ไม่งั้นด่านนี้ไม่ได้กันอะไรเลย:
	// AltRel = 0 (ค่า zero-value ตอน telemetry ยังไม่มา) ต้องไม่ผ่านเป็นเป้าหมาย
	if minHoldAltM <= 0 {
		t.Fatalf("minHoldAltM = %.2f — ต้อง > 0 ไม่งั้น AltRel=0 จะถูกส่งเป็นเป้า = สั่งลงพื้น",
			minHoldAltM)
	}
}

func TestHoldAltFloorRejectsZeroTelemetry(t *testing.T) {
	// จำลองเงื่อนไขจริงที่ Hold ใช้ตัดสินใจ: altRel ที่อ่านไม่ได้/ยังไม่มา
	// ต้องตกเข้าทาง zero-velocity hold ไม่ใช่ทาง Goto
	for _, alt := range []float64{0, 0.0, 0.5} {
		if alt >= minHoldAltM {
			t.Fatalf("alt %.2f ควรถือว่าไม่น่าเชื่อถือ (< minHoldAltM %.2f) "+
				"แต่กลับผ่านไปเป็นเป้าหมายของ Goto = เสี่ยงสั่งลดระดับ", alt, minHoldAltM)
		}
	}
	// ความสูงบินปกติต้องยังใช้ position hold ได้ (ไม่ตกไปทาง zero-velocity หมด)
	if 20.0 < minHoldAltM {
		t.Fatalf("minHoldAltM = %.2f สูงเกินไป — ความสูงบินปกติ 20m ยังไม่ได้ตรึงพิกัด",
			minHoldAltM)
	}
}

func TestHoldModeNameMatchesModeNumber(t *testing.T) {
	// holdModeName ใช้เทียบกับ SafetyState.Mode (สตริงจาก HEARTBEAT)
	// ถ้าสองค่านี้หลุดจากกัน Hold จะสั่งเปลี่ยนโหมดซ้ำทุกครั้งโดยไม่จำเป็น
	num, ok := fleet.ModeNumber(holdMode)
	if !ok {
		t.Fatalf("holdMode %v ไม่มีเลข custom_mode ใน fleet.ModeNumber", holdMode)
	}
	if got := fleet.ModeName(num); got != holdModeName {
		t.Fatalf("holdModeName = %q แต่ custom_mode %d คือ %q", holdModeName, num, got)
	}
}
