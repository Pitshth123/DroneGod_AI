package command

import (
	"context"
	"testing"
	"time"
)

// ─────────────────────────────────────────────────────────────
// Regression: ลำดับ takeoff (GUIDED→arm→takeoff) เคยส่ง context ของ
// "ทั้งก้อน" เข้าไปในแต่ละคำสั่งย่อย ทำให้ arm ครั้งแรกที่ไม่มี ACK
// บล็อกกินงบเวลาทั้งหมด → loop retry ไม่เคยได้ทำงาน และ client
// (timeout 30s) ตัดสายก่อน EKF/pre-arm พร้อม → takeoff ไม่ขึ้นเลย
//
// เทสต์นี้ล็อกพฤติกรรมที่ถูก: แต่ละ attempt ต้องมี timeout ของตัวเอง
// (cmdTimeout) และต้อง retry ได้จริงหลายครั้งภายในงบรวม
// ─────────────────────────────────────────────────────────────

// stepLike จำลอง helper `step` ใน Takeoff: ตัด timeout ย่อยจาก parent
func stepLike(parent context.Context, fn func(context.Context) error) error {
	sctx, cancel := context.WithTimeout(parent, cmdTimeout)
	defer cancel()
	return fn(sctx)
}

func TestTakeoffBudgetCoversArmRetries(t *testing.T) {
	// งบรวมต้องมากพอให้ retry ครบตามจำนวนที่ตั้งไว้
	// worst case ต่อรอบ = cmdTimeout (รอ ACK) + 2s (หน่วงก่อน retry)
	worst := time.Duration(armAttempts) * (cmdTimeout + 2*time.Second)
	if takeoffSeqTimeout < worst {
		t.Fatalf("งบเวลา takeoff (%v) น้อยกว่า worst-case ของ arm retry (%v) "+
			"— arm จะถูกตัดกลางคันก่อน EKF พร้อม", takeoffSeqTimeout, worst)
	}
}

func TestTakeoffBudgetLongerThanEKFWarmup(t *testing.T) {
	// โค้ด/คอมเมนต์ระบุว่า EKF/pre-arm ใช้เวลา ~30-40s หลัง boot
	// งบต้องมากกว่านั้นชัดเจน ไม่งั้น takeoff ไม่มีวันสำเร็จตอนเพิ่งเปิดเครื่อง
	const ekfWarmup = 40 * time.Second
	if takeoffSeqTimeout <= ekfWarmup {
		t.Fatalf("งบเวลา takeoff (%v) ต้องมากกว่าเวลารอ EKF (%v)",
			takeoffSeqTimeout, ekfWarmup)
	}
}

// หัวใจของบั๊ก: ถ้าคำสั่งย่อย "ไม่มี ACK" ต้องหมดเวลาเฉพาะรอบนั้น (~cmdTimeout)
// แล้วปล่อยให้รอบถัดไปทำงานต่อ — ไม่ใช่กินงบทั้งก้อนตั้งแต่รอบแรก
func TestArmAttemptTimesOutPerAttemptNotWholeBudget(t *testing.T) {
	parent, cancel := context.WithTimeout(context.Background(), takeoffSeqTimeout)
	defer cancel()

	blockUntilDeadline := func(c context.Context) error {
		<-c.Done() // จำลอง sendCmd ที่รอ ACK จนกว่า ctx จะหมด
		return c.Err()
	}

	start := time.Now()
	_ = stepLike(parent, blockUntilDeadline)
	elapsed := time.Since(start)

	if elapsed >= takeoffSeqTimeout {
		t.Fatalf("attempt เดียวกินงบทั้งก้อน (%v) — retry loop จะไม่ได้ทำงาน", elapsed)
	}
	// ควรจบราว ๆ cmdTimeout (เผื่อ jitter)
	if elapsed > cmdTimeout+2*time.Second {
		t.Fatalf("attempt ใช้เวลา %v นานเกิน cmdTimeout (%v)", elapsed, cmdTimeout)
	}
	// parent ต้องยังไม่หมดอายุ = ยัง retry ต่อได้
	if parent.Err() != nil {
		t.Fatal("parent context หมดอายุหลัง attempt เดียว — retry ต่อไม่ได้")
	}
}

// หลัง attempt ที่หมดเวลาไปหลายรอบ งบรวมต้องยังเหลือให้ทำต่อ
func TestMultipleAttemptsFitInBudget(t *testing.T) {
	parent, cancel := context.WithTimeout(context.Background(), takeoffSeqTimeout)
	defer cancel()

	instantFail := func(c context.Context) error { return context.DeadlineExceeded }
	for i := 0; i < armAttempts; i++ {
		_ = stepLike(parent, instantFail)
		if parent.Err() != nil {
			t.Fatalf("งบหมดตั้งแต่ attempt ที่ %d/%d", i+1, armAttempts)
		}
	}
}
