package fleet

import (
	"testing"
	"time"
)

// ─────────────────────────────────────────────────────────────
// Regression: "กด B แล้วเครื่องบินไม่ขยับหลายวินาที แล้วจู่ ๆ ก็ทำงาน"
//
// core ส่ง RC_CHANNELS_OVERRIDE ออกไปถูกต้องทุกครั้ง (ยืนยันจาก log `SEND override`)
// แต่ ArduPilot ทิ้งเฟรมเงียบ ๆ เพราะ MAVLink signing timestamp ยังไม่ผ่าน
// (FC ตั้งเวลาล่วงหน้า 60 วิหลังบูตกัน replay attack)
//
// เราแก้ฝั่ง FC ไม่ได้ แต่ต้อง "จับได้และบอกผู้ใช้" ไม่ใช่ปล่อยให้เดาว่าปุ่มเสีย
// ─────────────────────────────────────────────────────────────

func TestOverrideStuckQuietWhenNoOverride(t *testing.T) {
	d, _ := newRCTestDrone()
	if _, _, _, _, stuck := d.OverrideStuck(); stuck {
		t.Fatal("ไม่ได้ override อะไรอยู่ ต้องไม่รายงานว่าค้าง")
	}
}

func TestOverrideStuckQuietBeforeGracePeriod(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	// เพิ่งสั่งไปเดี๋ยวนี้ — ยังไม่ถึงเวลาที่ถือว่าผิดปกติ
	if _, _, _, _, stuck := d.OverrideStuck(); stuck {
		t.Fatal("ยังไม่พ้น grace period ต้องไม่รายงาน")
	}
}

func TestOverrideStuckReportedWhenFCIgnoresIt(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	// FC รายงานกลับมาว่า CH8 ยังเป็น 900 (ไม่รับคำสั่ง)
	d.mu.Lock()
	d.rcCh8, d.rcValid = 900, true
	d.mu.Unlock()
	// ย้อนเวลาที่ตั้ง override ให้พ้น grace period
	d.ovrMu.Lock()
	d.ovrSince = time.Now().Add(-(overrideStuckAfter + time.Second))
	d.ovrMu.Unlock()

	ch, want, got, since, stuck := d.OverrideStuck()
	if !stuck {
		t.Fatal("FC ไม่รับคำสั่งเกิน 2 วิ ต้องรายงาน")
	}
	if ch != ServoChanB || want != 2100 || got != 900 {
		t.Fatalf("รายละเอียดผิด: ch=%d want=%d got=%d", ch, want, got)
	}
	if since < overrideStuckAfter {
		t.Fatalf("ระยะเวลาที่รายงานสั้นเกินจริง: %v", since)
	}

	// รายงานครั้งเดียวต่อเหตุการณ์ — ไม่ถล่ม log/event ทุก 1 วิ
	if _, _, _, _, again := d.OverrideStuck(); again {
		t.Fatal("ต้องรายงานครั้งเดียวต่อเหตุการณ์")
	}
}

func TestOverrideStuckClearsOnceFCAccepts(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	d.mu.Lock()
	d.rcCh8, d.rcValid = 900, true
	d.mu.Unlock()
	d.ovrMu.Lock()
	d.ovrSince = time.Now().Add(-(overrideStuckAfter + time.Second))
	d.ovrMu.Unlock()
	if _, _, _, _, stuck := d.OverrideStuck(); !stuck {
		t.Fatal("ควรรายงานรอบแรก")
	}

	// FC ยอมรับแล้ว — สถานะต้องถูกล้างเพื่อรอเหตุการณ์ถัดไป
	d.mu.Lock()
	d.rcCh8 = 2100
	d.mu.Unlock()
	if _, _, _, _, stuck := d.OverrideStuck(); stuck {
		t.Fatal("FC รับแล้วต้องไม่รายงานว่าค้าง")
	}

	// เกิดใหม่อีกครั้ง ต้องรายงานได้อีก (ไม่ใช่เงียบตลอดกาล)
	d.mu.Lock()
	d.rcCh8 = 900
	d.mu.Unlock()
	if _, _, _, _, stuck := d.OverrideStuck(); !stuck {
		t.Fatal("เหตุการณ์ใหม่ต้องรายงานได้อีก")
	}
}

// เตือนว่าค้างแล้วต้องบอกตอน "หาย" ด้วย ไม่งั้นผู้ใช้ไม่รู้ว่าตกลงใช้ได้เมื่อไหร่
func TestOverrideRecoveredReportedOnceAfterWarning(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	d.mu.Lock()
	d.rcCh8, d.rcValid = 900, true
	d.mu.Unlock()
	d.ovrMu.Lock()
	d.ovrSince = time.Now().Add(-(overrideStuckAfter + time.Second))
	d.ovrMu.Unlock()

	// ยังไม่เคยเตือน → ยังไม่ต้องรายงานว่าหาย
	if _, ok := d.OverrideRecovered(); ok {
		t.Fatal("ยังไม่เคยเตือน ไม่ควรรายงานว่าหาย")
	}
	if _, _, _, _, stuck := d.OverrideStuck(); !stuck {
		t.Fatal("ควรเตือนว่าค้าง")
	}
	// ยังไม่รับ → ยังไม่หาย
	if _, ok := d.OverrideRecovered(); ok {
		t.Fatal("FC ยังไม่รับ ไม่ควรรายงานว่าหาย")
	}

	d.mu.Lock()
	d.rcCh8 = 2100
	d.mu.Unlock()
	since, ok := d.OverrideRecovered()
	if !ok {
		t.Fatal("FC รับแล้วต้องรายงานว่าหาย")
	}
	if since < overrideStuckAfter {
		t.Fatalf("ระยะเวลาที่รายงานผิด: %v", since)
	}
	// รายงานครั้งเดียว
	if _, again := d.OverrideRecovered(); again {
		t.Fatal("ต้องรายงานครั้งเดียว")
	}
}

func TestOverrideStuckQuietWithoutRCData(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	d.ovrMu.Lock()
	d.ovrSince = time.Now().Add(-(overrideStuckAfter + time.Second))
	d.ovrMu.Unlock()
	// ไม่มี RC จาก FC เลย = เทียบไม่ได้ ต้องไม่เดาว่าค้าง
	if _, _, _, _, stuck := d.OverrideStuck(); stuck {
		t.Fatal("ไม่มีข้อมูล RC ต้องไม่สรุปว่าค้าง")
	}
}

// วัดเวลา "คำสั่งแรกหลังเชื่อมต่อ" ต้องได้ตัวเลขทุกเที่ยวบิน แม้จะเร็วกว่าเกณฑ์เตือน
// (ถ้าไม่มี พอปรับจนเร็วขึ้นแล้วจะไม่มีตัวเลขให้เทียบผลเลย)
func TestOverrideLatencyReportedOncePerConnection(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if _, ok := d.OverrideLatency(); ok {
		t.Fatal("ยังไม่ได้สั่งอะไร ไม่ควรมีตัวเลข")
	}
	if err := d.SetRCOverride(ServoChanB, 2100); err != nil {
		t.Fatalf("SetRCOverride: %v", err)
	}
	d.mu.Lock()
	d.rcCh8, d.rcValid = 900, true // FC ยังไม่รับ
	d.mu.Unlock()
	if _, ok := d.OverrideLatency(); ok {
		t.Fatal("FC ยังไม่รับ ไม่ควรรายงาน")
	}

	d.mu.Lock()
	d.rcCh8 = 2100 // FC รับแล้ว
	d.mu.Unlock()
	if _, ok := d.OverrideLatency(); !ok {
		t.Fatal("FC รับแล้วต้องรายงานเวลา")
	}
	// ครั้งเดียวต่อการเชื่อมต่อ — ไม่ใช่ทุกครั้งที่กด
	if _, again := d.OverrideLatency(); again {
		t.Fatal("ต้องรายงานครั้งเดียวต่อการเชื่อมต่อ")
	}
}
