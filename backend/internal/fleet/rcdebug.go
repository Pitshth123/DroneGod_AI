package fleet

import (
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
)

// ── RC / SERVO diagnostic ────────────────────────────────────────────
//
// เปิดด้วย env `SWARMGOD_RC_DEBUG=1` (ดู DIAG_RC.bat)
//
// ทำไมต้องมี: cockpit ติดตามแค่ ch7/ch8 ตามที่ออกแบบไว้ ถ้ากลไกปล่อยของจริง
// ไปผูกอยู่กับช่องอื่น หรือกับ aux function (`RCn_OPTION`) แทนที่จะเป็น servo
// passthrough เราจะมองไม่เห็นเลยจาก log ปกติ — เห็นแต่ว่า "สั่งไปแล้ว ค่าถูกต้อง"
//
// เปิดโหมดนี้แล้ว core จะพิมพ์ **ทุกช่อง RC (1-16)** และ **ทุกขาเซอร์โว (1-16)**
// ทุกครั้งที่มีค่าไหนเปลี่ยน ทำให้เทียบได้ตรง ๆ ว่า:
//   โยกสวิตช์ที่รีโมท  → ช่อง/ขาไหนขยับบ้าง
//   กดปุ่มที่ cockpit  → ช่อง/ขาไหนขยับบ้าง
// ถ้าสองอย่างนี้ขยับไม่เหมือนกัน แปลว่ากลไกไม่ได้อยู่บนช่องที่ UI สั่งอยู่

var rcDebugOn = strings.TrimSpace(os.Getenv("SWARMGOD_RC_DEBUG")) == "1"

// RCDebugEnabled บอกว่าโหมด diagnostic เปิดอยู่ไหม (ใช้ log ตอน core boot)
func RCDebugEnabled() bool { return rcDebugOn }

// เปลี่ยนน้อยกว่านี้ถือว่าเป็น noise ของสัญญาณ ไม่ต้องพิมพ์ซ้ำ
const rcDebugDeadband = 20

type rcDebugState struct {
	mu    sync.Mutex
	rc    [16]uint32
	servo [16]uint32
	rcOK  bool
	svOK  bool
	ovr   string // ชุด override ล่าสุดที่พิมพ์ไปแล้ว (กันพิมพ์ซ้ำทุก 500 ms)
	ovrOK bool

	stuckSent bool // แจ้งเตือน "FC ไม่รับ override" ไปแล้วสำหรับเหตุการณ์นี้
	firstDone bool // รายงานเวลาที่ FC รับคำสั่ง "แรก" ของการเชื่อมต่อนี้ไปแล้ว
}

func changedBeyondDeadband(prev, cur *[16]uint32) bool {
	for i := range cur {
		d := int(cur[i]) - int(prev[i])
		if d < 0 {
			d = -d
		}
		if d >= rcDebugDeadband {
			return true
		}
	}
	return false
}

func formatChannels(label string, v *[16]uint32) string {
	var b strings.Builder
	b.WriteString(label)
	for i, x := range v {
		if x == 0 {
			continue // ช่องที่ไม่ได้ใช้ — ตัดออกให้อ่านง่าย
		}
		fmt.Fprintf(&b, " %d=%d", i+1, x)
	}
	return b.String()
}

// logOverride พิมพ์ตอน core "ส่ง" override ออกไป — คู่กับ logRC/logServo ที่เป็นค่าที่
// FC "รายงานกลับ" เทียบสองอย่างนี้แล้วรู้ทันทีว่าถ้าช้า ช้าที่ core หรือที่เครื่องบิน
//
// `sendRCOverride` ถูกเรียกซ้ำทุก 500 ms โดย resend loop (กัน FC ปล่อย override ทิ้ง)
// จึงพิมพ์เฉพาะตอน "ชุดช่องที่ override เปลี่ยน" ไม่ใช่ทุกครั้งที่ส่ง
func (d *Drone) logOverride(ch map[uint8]uint16) {
	if !rcDebugOn {
		return
	}
	var b strings.Builder
	for c := uint8(1); c <= 16; c++ {
		if v, ok := ch[c]; ok {
			fmt.Fprintf(&b, " %d=%d", c, v)
		}
	}
	s := b.String()
	if s == "" {
		s = " (ไม่เหลือช่องไหน override อยู่)"
	}

	d.rcDbg.mu.Lock()
	same := d.rcDbg.ovrOK && d.rcDbg.ovr == s
	if !same {
		d.rcDbg.ovr = s
		d.rcDbg.ovrOK = true
	}
	d.rcDbg.mu.Unlock()
	if !same {
		log.Printf("[rcdbg] D%d SEND override:%s", d.ID, s)
	}
}

// ── ตรวจจับ "ส่ง override แล้วเครื่องบินไม่รับ" ───────────────────────────
//
// อาการที่เจอจริง: core ส่ง RC_CHANNELS_OVERRIDE ออกไปถูกต้อง (resend ทุก 500 ms)
// แต่ FC เพิกเฉยอยู่หลายวินาที แล้วจู่ ๆ ก็เริ่มรับ จากนั้นเร็วตลอด
//
// ยังหาต้นเหตุที่แท้จริงไม่เจอ — สิ่งที่ **ตัดออกไปแล้วด้วยการวัด**:
//
//	MAVLink signing   ทดสอบด้วย SWARMGOD_MAVLINK_SIGNING=off → ดีเลย์ยังอยู่ 7 วิ
//	                  (และยังสั่งได้ = FC ไม่ได้บังคับ signing ด้วย)
//	นาฬิกา GCS        sync แล้วดีเลย์ลดลงจริง แต่ไม่หาย (24 → 6/7/11 วิ)
//	ฝั่ง cockpit       วัดได้ ~2 ms ทั้งเส้น
//	ฝั่ง gRPC          ~11 ms ถึง core
//	ค่า PWM            ตรงกับที่รีโมทส่งเป๊ะรายช่องแล้ว
//	ช่อง/พารามิเตอร์   SERVO8 เดินตาม RC8 ทุกครั้ง = ตั้งถูกแล้ว
//
// เหลือคือ "FC เมิน RC_CHANNELS_OVERRIDE ชุดแรกหลังเชื่อมต่อ ~15-17 วินาที"
// ยังไม่รู้ว่า gate อยู่ตรงไหนใน ArduPilot — ระหว่างนี้ทำ 2 อย่าง:
//  1. บรรเทาด้วย warm-up (ดูด้านล่าง)
//  2. ตรวจแล้วบอกผู้ใช้ตรง ๆ ไม่ปล่อยให้เดาว่า "ปุ่มเสีย"

// overrideStuckAfter = ส่ง override ไปแล้วนานเท่านี้ยังไม่เห็นผลที่ FC = ผิดปกติ
//
// เดิมตั้งไว้ 2 วิ ตอนที่ดีเลย์ปกติอยู่ที่ 6-24 วิ · หลังใส่ RC warm-up (§11.32)
// ดีเลย์ปกติเหลือ 2-3 วิ เกณฑ์เดิมจึงเด้งเตือน **แทบทุกเที่ยวบิน** กลายเป็น noise
// ที่ฝึกให้คนมองข้ามคำเตือน — ขยับเป็น 5 วิ ให้เตือนเฉพาะตอนผิดปกติจริง
const overrideStuckAfter = 5 * time.Second

// ── warm-up: อุ่นช่องทาง RC_CHANNELS_OVERRIDE ตั้งแต่เพิ่งเชื่อมต่อ ──
//
// วัดจากโดรนจริงหลายรอบ: คำสั่ง RC override **ครั้งแรก** หลังเชื่อมต่อถูก FC เมิน
// อยู่ราว 6-24 วินาที (เวลาจากเชื่อมต่อถึงคำสั่งแรกสำเร็จค่อนข้างคงที่ ~15-17 วิ)
// จากนั้นทุกคำสั่งถัดไปลงภายในวินาทีเดียว รวมถึงตอนบินจริง
//
// ตัดออกไปแล้ว: MAVLink signing (ทดสอบด้วย SWARMGOD_MAVLINK_SIGNING=off — ดีเลย์ยังอยู่)
//
//	ฝั่ง cockpit (วัดได้ ~2 ms) · ฝั่ง gRPC (~11 ms) · ค่า PWM (ตรงรีโมทแล้ว)
//
// วิธีบรรเทา: ส่ง RC_CHANNELS_OVERRIDE แบบ "ปล่อยทุกช่อง" (ทุก field = 0) ตั้งแต่
// เพิ่งเชื่อมต่อ เป็นเวลาสั้น ๆ — ค่า 0 ในสเปก MAVLink แปลว่า "คืนช่องให้ RC จริง"
// จึงไม่แตะการควบคุมใด ๆ เลย แต่ทำให้ FC ได้เห็นข้อความชนิดนี้จากเราไหลมาก่อน
// ผู้ใช้จะกดปุ่ม — ถ้า gate อยู่ที่ "ยังไม่เคยเห็น stream นี้" ก็จะผ่านไปก่อนแล้ว
//
// จำกัดเวลาไว้ ไม่ให้กลายเป็นการยิง override ตลอดอายุการบิน (เปลี่ยนพฤติกรรมมากเกินไป)
const (
	rcWarmupFor    = 25 * time.Second
	rcWarmupPeriod = 500 * time.Millisecond
)

// OverrideStuck คืนรายละเอียดถ้ากำลัง override ช่อง 7/8 อยู่ แต่ค่าที่ FC รายงานกลับ
// ยังไม่ตรงกับที่สั่งเกิน overrideStuckAfter — คืน ok=false ถ้าทุกอย่างปกติ
//
// รายงานครั้งเดียวต่อ 1 เหตุการณ์ (รีเซ็ตเมื่อ FC รับคำสั่งแล้ว) กัน log/event ท่วม
func (d *Drone) OverrideStuck() (ch uint8, want, got uint32, since time.Duration, ok bool) {
	d.ovrMu.Lock()
	pending := make(map[uint8]uint16, len(d.ovrCh))
	for k, v := range d.ovrCh {
		pending[k] = v
	}
	at := d.ovrSince
	d.ovrMu.Unlock()

	if len(pending) == 0 || at.IsZero() {
		d.rcDbg.mu.Lock()
		d.rcDbg.stuckSent = false
		d.rcDbg.mu.Unlock()
		return 0, 0, 0, 0, false
	}
	elapsed := time.Since(at)
	if elapsed < overrideStuckAfter {
		return 0, 0, 0, 0, false
	}

	d.mu.RLock()
	rc := map[uint8]uint32{ServoChanA: d.rcCh7, ServoChanB: d.rcCh8}
	seen := d.rcValid
	d.mu.RUnlock()
	if !seen {
		return 0, 0, 0, 0, false // ไม่มีข้อมูล RC เทียบ — สรุปไม่ได้
	}

	for c, wantPWM := range pending {
		gotPWM, tracked := rc[c]
		if !tracked || gotPWM == uint32(wantPWM) {
			continue
		}
		d.rcDbg.mu.Lock()
		already := d.rcDbg.stuckSent
		d.rcDbg.stuckSent = true
		d.rcDbg.mu.Unlock()
		if already {
			return 0, 0, 0, 0, false
		}
		return c, uint32(wantPWM), gotPWM, elapsed, true
	}

	// ตรงกันครบแล้ว = FC รับคำสั่งแล้ว รีเซ็ตไว้รอเหตุการณ์ถัดไป
	d.rcDbg.mu.Lock()
	d.rcDbg.stuckSent = false
	d.rcDbg.mu.Unlock()
	return 0, 0, 0, 0, false
}

// OverrideRecovered คืน true ครั้งเดียว ตอน FC กลับมารับคำสั่งหลังเคยเตือนว่าค้าง
//
// ต้องมีคู่กับ OverrideStuck: เตือนแล้วเงียบหายไปเลย ผู้ใช้ไม่รู้ว่าตกลงใช้ได้เมื่อไหร่
// ต้องกดใหม่ไหม — บอกให้ชัดว่า "รับแล้ว ใช้เวลา N วินาที"
func (d *Drone) OverrideRecovered() (since time.Duration, ok bool) {
	d.rcDbg.mu.Lock()
	warned := d.rcDbg.stuckSent
	d.rcDbg.mu.Unlock()
	if !warned {
		return 0, false
	}

	d.ovrMu.Lock()
	pending := make(map[uint8]uint16, len(d.ovrCh))
	for k, v := range d.ovrCh {
		pending[k] = v
	}
	at := d.ovrSince
	d.ovrMu.Unlock()

	// ผู้ใช้กดยกเลิกก่อนที่ FC จะรับ — ต้องรายงานด้วย ไม่ใช่เงียบหาย
	// (เดิมคืน false เฉย ๆ ทำให้ log มีแต่คำเตือน ไม่มีตอนจบ ตามผลไม่ได้เลยว่า
	//  ตกลง FC รับหรือผู้ใช้ยอมแพ้ไปก่อน — เจอตอนอ่าน log 5 รอบของผู้ใช้)
	if len(pending) == 0 {
		d.rcDbg.mu.Lock()
		d.rcDbg.stuckSent = false
		d.rcDbg.mu.Unlock()
		return 0, false
	}

	d.mu.RLock()
	rc := map[uint8]uint32{ServoChanA: d.rcCh7, ServoChanB: d.rcCh8}
	seen := d.rcValid
	d.mu.RUnlock()
	if !seen {
		return 0, false
	}
	for c, wantPWM := range pending {
		if got, tracked := rc[c]; tracked && got != uint32(wantPWM) {
			return 0, false // ยังไม่รับครบทุกช่อง
		}
	}

	d.rcDbg.mu.Lock()
	d.rcDbg.stuckSent = false
	d.rcDbg.mu.Unlock()
	return time.Since(at), true
}

// OverrideLatency คืนเวลาที่ FC ใช้ "รับคำสั่งแรก" หลังเชื่อมต่อ — รายงานครั้งเดียวต่อ
// การเชื่อมต่อหนึ่งครั้ง แม้จะเร็วกว่าเกณฑ์เตือนก็ตาม
//
// มีไว้เพื่อ **วัดผลได้ทุกเที่ยวบินโดยไม่ต้องเปิด SWARMGOD_RC_DEBUG** — ก่อนหน้านี้
// ถ้าดีเลย์ต่ำกว่าเกณฑ์เตือน log จะไม่มีตัวเลขอะไรเลย เทียบผลการปรับแต่งไม่ได้
func (d *Drone) OverrideLatency() (since time.Duration, ok bool) {
	d.rcDbg.mu.Lock()
	done := d.rcDbg.firstDone
	d.rcDbg.mu.Unlock()
	if done {
		return 0, false
	}

	d.ovrMu.Lock()
	pending := make(map[uint8]uint16, len(d.ovrCh))
	for k, v := range d.ovrCh {
		pending[k] = v
	}
	at := d.ovrSince
	d.ovrMu.Unlock()
	if len(pending) == 0 || at.IsZero() {
		return 0, false
	}

	d.mu.RLock()
	rc := map[uint8]uint32{ServoChanA: d.rcCh7, ServoChanB: d.rcCh8}
	seen := d.rcValid
	d.mu.RUnlock()
	if !seen {
		return 0, false
	}
	for c, wantPWM := range pending {
		if got, tracked := rc[c]; tracked && got != uint32(wantPWM) {
			return 0, false // FC ยังไม่รับ
		}
	}

	d.rcDbg.mu.Lock()
	d.rcDbg.firstDone = true
	d.rcDbg.mu.Unlock()
	return time.Since(at), true
}

// logRC พิมพ์ RC ทุกช่องเมื่อมีการเปลี่ยนแปลง
func (d *Drone) logRC(m *ardupilotmega.MessageRcChannels) {
	if !rcDebugOn {
		return
	}
	cur := [16]uint32{
		uint32(m.Chan1Raw), uint32(m.Chan2Raw), uint32(m.Chan3Raw), uint32(m.Chan4Raw),
		uint32(m.Chan5Raw), uint32(m.Chan6Raw), uint32(m.Chan7Raw), uint32(m.Chan8Raw),
		uint32(m.Chan9Raw), uint32(m.Chan10Raw), uint32(m.Chan11Raw), uint32(m.Chan12Raw),
		uint32(m.Chan13Raw), uint32(m.Chan14Raw), uint32(m.Chan15Raw), uint32(m.Chan16Raw),
	}
	d.rcDbg.mu.Lock()
	first := !d.rcDbg.rcOK
	show := first || changedBeyondDeadband(&d.rcDbg.rc, &cur)
	if show {
		d.rcDbg.rc = cur
		d.rcDbg.rcOK = true
	}
	d.rcDbg.mu.Unlock()
	if show {
		log.Printf("[rcdbg] D%d %s", d.ID, formatChannels("RC  ", &cur))
	}
}

// logServo พิมพ์ขาเซอร์โวทุกขาเมื่อมีการเปลี่ยนแปลง
func (d *Drone) logServo(m *ardupilotmega.MessageServoOutputRaw) {
	if !rcDebugOn {
		return
	}
	cur := [16]uint32{
		uint32(m.Servo1Raw), uint32(m.Servo2Raw), uint32(m.Servo3Raw), uint32(m.Servo4Raw),
		uint32(m.Servo5Raw), uint32(m.Servo6Raw), uint32(m.Servo7Raw), uint32(m.Servo8Raw),
		uint32(m.Servo9Raw), uint32(m.Servo10Raw), uint32(m.Servo11Raw), uint32(m.Servo12Raw),
		uint32(m.Servo13Raw), uint32(m.Servo14Raw), uint32(m.Servo15Raw), uint32(m.Servo16Raw),
	}
	d.rcDbg.mu.Lock()
	first := !d.rcDbg.svOK
	show := first || changedBeyondDeadband(&d.rcDbg.servo, &cur)
	if show {
		d.rcDbg.servo = cur
		d.rcDbg.svOK = true
	}
	d.rcDbg.mu.Unlock()
	if show {
		log.Printf("[rcdbg] D%d %s", d.ID, formatChannels("SERVO", &cur))
	}
}
