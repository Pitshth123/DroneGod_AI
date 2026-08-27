package fleet

import (
	"sync"
	"testing"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/common"
	"github.com/bluenviron/gomavlib/v3/pkg/message"
)

// rcRecorder เก็บข้อความที่ถูกส่งออกไว้ตรวจ
type rcRecorder struct {
	mu   sync.Mutex
	sent []message.Message
}

func (f *rcRecorder) Send(m message.Message) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sent = append(f.sent, m)
	return nil
}

func (f *rcRecorder) overrides() []*common.MessageRcChannelsOverride {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []*common.MessageRcChannelsOverride
	for _, m := range f.sent {
		if o, ok := m.(*common.MessageRcChannelsOverride); ok {
			out = append(out, o)
		}
	}
	return out
}

func (f *rcRecorder) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.sent)
}

func newRCTestDrone() (*Drone, *rcRecorder) {
	fs := &rcRecorder{}
	return newDrone(1, "T", "127.0.0.1", 5760, fs), fs
}

// ── ความปลอดภัย: ห้าม override คันบังคับหลัก ──

func TestSetRCOverrideRejectsFlightControlChannels(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	for ch := uint8(1); ch <= 4; ch++ {
		if err := d.SetRCOverride(ch, 1500); err == nil {
			t.Errorf("RC%d (คันบังคับ) ต้องถูกปฏิเสธ แต่ผ่านไปได้", ch)
		}
	}
	if len(fs.overrides()) != 0 {
		t.Error("ถูกปฏิเสธแล้วต้องไม่ส่งข้อความ override ออกไปเลย")
	}
}

func TestSetRCOverrideRejectsOutOfRange(t *testing.T) {
	d, _ := newRCTestDrone()
	defer d.ClearAllRCOverride()
	for _, ch := range []uint8{0, 19, 200} {
		if err := d.SetRCOverride(ch, 1500); err == nil {
			t.Errorf("RC%d อยู่นอกช่วง ต้องถูกปฏิเสธ", ch)
		}
	}
}

func TestOverrideNeverTouchesFlightControls(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanA, 1950); err != nil {
		t.Fatalf("ตั้ง override CH7 ไม่ได้: %v", err)
	}
	ovs := fs.overrides()
	if len(ovs) == 0 {
		t.Fatal("ไม่มีข้อความ override ถูกส่ง")
	}
	o := ovs[0]
	// 0 = "ปล่อยให้รีโมทจริงคุม" → คันบังคับต้องเป็น 0 เสมอ
	if o.Chan1Raw != 0 || o.Chan2Raw != 0 || o.Chan3Raw != 0 || o.Chan4Raw != 0 {
		t.Errorf("คันบังคับต้องเป็น 0 (ปล่อยให้รีโมท) แต่ได้ %d/%d/%d/%d",
			o.Chan1Raw, o.Chan2Raw, o.Chan3Raw, o.Chan4Raw)
	}
}

// ── พฤติกรรมพื้นฐาน ──

func TestSetRCOverrideSendsRequestedPWM(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	if err := d.SetRCOverride(ServoChanA, 1950); err != nil {
		t.Fatal(err)
	}
	o := fs.overrides()[0]
	if o.Chan7Raw != 1950 {
		t.Errorf("CH7 = %d, want 1950", o.Chan7Raw)
	}
	if o.Chan8Raw != 0 {
		t.Errorf("CH8 ยังไม่ได้สั่ง ต้องเป็น 0 (ปล่อยให้รีโมท) แต่ได้ %d", o.Chan8Raw)
	}
}

func TestBothChannelsCanBeOverridden(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	d.SetRCOverride(ServoChanA, 1950)
	d.SetRCOverride(ServoChanB, 2100)
	ovs := fs.overrides()
	last := ovs[len(ovs)-1]
	if last.Chan7Raw != 1950 || last.Chan8Raw != 2100 {
		t.Errorf("ต้อง override ได้ทั้งคู่: CH7=%d CH8=%d", last.Chan7Raw, last.Chan8Raw)
	}
	act := d.RCOverrideActive()
	if len(act) != 2 {
		t.Errorf("RCOverrideActive ควรมี 2 ช่อง ได้ %d", len(act))
	}
}

func TestClearRCOverrideReleasesOnlyThatChannel(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	d.SetRCOverride(ServoChanA, 1950)
	d.SetRCOverride(ServoChanB, 2100)
	if err := d.ClearRCOverride(ServoChanA); err != nil {
		t.Fatal(err)
	}
	ovs := fs.overrides()
	last := ovs[len(ovs)-1]
	if last.Chan7Raw != 0 {
		t.Errorf("CH7 ถูกปล่อยแล้วต้องเป็น 0 (คืนให้รีโมท) ได้ %d", last.Chan7Raw)
	}
	if last.Chan8Raw != 2100 {
		t.Errorf("CH8 ยังต้อง override อยู่ ได้ %d", last.Chan8Raw)
	}
	if _, still := d.RCOverrideActive()[ServoChanA]; still {
		t.Error("CH7 ไม่ควรอยู่ในรายการ override แล้ว")
	}
}

func TestClearAllReleasesEverything(t *testing.T) {
	d, fs := newRCTestDrone()
	d.SetRCOverride(ServoChanA, 1950)
	d.SetRCOverride(ServoChanB, 2100)
	if err := d.ClearAllRCOverride(); err != nil {
		t.Fatal(err)
	}
	last := fs.overrides()
	o := last[len(last)-1]
	if o.Chan7Raw != 0 || o.Chan8Raw != 0 {
		t.Errorf("ปล่อยหมดแล้วทุกช่องต้องเป็น 0 ได้ CH7=%d CH8=%d", o.Chan7Raw, o.Chan8Raw)
	}
	if len(d.RCOverrideActive()) != 0 {
		t.Error("ไม่ควรเหลือ override ค้างอยู่")
	}
}

// ── loop ส่งซ้ำ (override หมดอายุที่ FC ราว 3 วิ) ──

func TestOverrideIsResentPeriodically(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	d.SetRCOverride(ServoChanA, 1950)
	first := fs.count()
	// รอให้ ticker ยิงอย่างน้อย 2 รอบ (rcOverrideResend = 500ms)
	time.Sleep(1300 * time.Millisecond)
	if got := fs.count(); got <= first {
		t.Errorf("ต้องส่งซ้ำเรื่อย ๆ กัน override หมดอายุ — ส่งไป %d ครั้ง (เริ่มที่ %d)",
			got, first)
	}
}

func TestResendStopsAfterClear(t *testing.T) {
	d, fs := newRCTestDrone()
	d.SetRCOverride(ServoChanA, 1950)
	time.Sleep(700 * time.Millisecond)
	d.ClearAllRCOverride()
	afterClear := fs.count()
	time.Sleep(1200 * time.Millisecond)
	if got := fs.count(); got != afterClear {
		t.Errorf("ปล่อย override แล้วต้องหยุดส่งซ้ำ — ยังส่งเพิ่มอีก %d ครั้ง",
			got-afterClear)
	}
}

func TestReSetDoesNotStartSecondLoop(t *testing.T) {
	d, fs := newRCTestDrone()
	defer d.ClearAllRCOverride()
	d.SetRCOverride(ServoChanA, 1950)
	d.SetRCOverride(ServoChanA, 1050) // สั่งซ้ำช่องเดิม
	d.SetRCOverride(ServoChanB, 2100)
	base := fs.count()
	time.Sleep(1100 * time.Millisecond)
	// 500ms ticker → ~2 รอบใน 1.1 วิ ถ้ามี loop ซ้อนจะได้มากกว่านี้มาก
	if grew := fs.count() - base; grew > 5 {
		t.Errorf("น่าจะมี loop ส่งซ้ำซ้อนกัน — เพิ่มขึ้น %d ข้อความใน 1.1 วิ", grew)
	}
}
