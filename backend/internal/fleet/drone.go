package fleet

import (
	"context"
	"fmt"
	"log"
	"math"
	"sync"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/common"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/minimal"
	"github.com/bluenviron/gomavlib/v3/pkg/message"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/safety"
)

// sender = ความสามารถขั้นต่ำที่ Drone ต้องใช้จาก connection (ส่ง MAVLink message)
// แยกเป็น interface เพื่อ inject fake ใน test ได้ (*mavlink.Conn satisfy อยู่แล้ว)
type sender interface {
	Send(message.Message) error
}

// SendGuard serializes the final FC write against a higher-priority cancellation.
// Mission authority attaches one to its context so cancellation and the actual
// conn.Send() share the same tiny critical section: either the write commits
// first, or cancellation wins first and the stale write is refused.  The guard
// covers only the transport write itself, never an ACK wait.
type SendGuard interface {
	DoSend(func() error) error
}

type sendGuardContextKey struct{}

// WithSendGuard attaches a final-write guard to ctx.  Normal/manual callers do
// not need one; it is used by cancellable Core mission sends to close the
// ctx.Err() -> conn.Send() TOCTOU window without holding API ownership locks
// across long ACK waits.
func WithSendGuard(ctx context.Context, guard SendGuard) context.Context {
	if ctx == nil {
		ctx = context.Background()
	}
	if guard == nil {
		return ctx
	}
	return context.WithValue(ctx, sendGuardContextKey{}, guard)
}

type chainedSendGuard struct {
	outer SendGuard
	inner SendGuard
}

func (g *chainedSendGuard) DoSend(send func() error) error {
	if g == nil || g.outer == nil || g.inner == nil {
		return context.Canceled
	}
	return g.outer.DoSend(func() error { return g.inner.DoSend(send) })
}

// WithAdditionalSendGuard composes a second final-write guard without replacing
// an existing mission/operator guard already attached to ctx. The existing guard
// stays outermost, so ownership cancellation is checked before the additional
// boundary. Both guards cover only the short transport write performed by
// guardedSend; COMMAND_ACK waits remain outside their critical sections.
func WithAdditionalSendGuard(ctx context.Context, guard SendGuard) context.Context {
	if ctx == nil {
		ctx = context.Background()
	}
	if guard == nil {
		return ctx
	}
	if existing, ok := ctx.Value(sendGuardContextKey{}).(SendGuard); ok && existing != nil {
		guard = &chainedSendGuard{outer: existing, inner: guard}
	}
	return context.WithValue(ctx, sendGuardContextKey{}, guard)
}

type sendGuardFunc func(func() error) error

func (f sendGuardFunc) DoSend(send func() error) error { return f(send) }

func guardedSend(ctx context.Context, send func() error) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if guard, ok := ctx.Value(sendGuardContextKey{}).(SendGuard); ok && guard != nil {
		return guard.DoSend(func() error {
			if err := ctx.Err(); err != nil {
				return err
			}
			return send()
		})
	}
	return send()
}

// Drone = สถานะ 1 ลำ (mutex-guarded) + ช่องส่งคำสั่ง (conn)
type Drone struct {
	ID   uint32
	Name string
	Host string
	Port uint32

	conn sender

	mu             sync.RWMutex
	targetSystem   byte
	lat, lon       float64
	altRel, altAbs float64
	// launchLat/launchLon คือจุดปล่อยจริงของลำนี้ใน sortie ปัจจุบัน.
	// ห้ามใช้ GCS home ร่วมกันแทน เพราะหลายลำจะบรรจบที่จุดเดียวตอน RETURN.
	launchLat, launchLon float64
	launchSet            bool
	vx, vy, vz           float64
	groundSpeed          float64
	heading              float64
	roll, pitch          float64
	battPct              float64
	voltage              float64
	current              float64
	gpsFix               int
	sats                 int
	mode                 string
	modeEnum             pb.FlightMode
	armed                bool

	// ── datalink (spec: "Datalink ความแรง ไม่มีข้อมูลส่งมาจริง") ──
	// rssi มาจากวิทยุ SiK ผ่าน RADIO_STATUS เท่านั้น — SITL/USB/UDP ตรงจะไม่มี
	// จึงต้องมี rssiValid กำกับ ไม่งั้น UI จะเข้าใจผิดว่า 0 dBm = สัญญาณเต็ม
	rssi         int32
	rssiValid    bool
	rssiAt       time.Time // เวลาที่ได้ RADIO_STATUS ล่าสุด (ใช้หมดอายุเมื่อวิทยุเงียบ)
	dropRate     uint32    // ‰ จาก SYS_STATUS.drop_rate_comm
	hbLast       time.Time // heartbeat ล่าสุด — ใช้คำนวณ link quality แบบใช้ได้ทุก transport
	hbIntervalMs float64   // ระยะห่าง heartbeat เฉลี่ย (EMA)

	// ── servo output จริงจาก FC (ผลลัพธ์ที่ขาเซอร์โว) ──
	servoCh7   uint32
	servoCh8   uint32
	servoValid bool
	servoAt    time.Time

	// ── RC input จากรีโมท (สวิตช์ A=RC7, B=RC8) ──
	// แยกจาก servo output เพราะเป็นคนละชั้น: RC = คนโยกสวิตช์, servo = FC ขยับขาจริง
	rcCh7   uint32
	rcCh8   uint32
	rcValid bool
	rcAt    time.Time

	// ── diagnostic: RC/servo ครบทุกช่อง (เปิดด้วย SWARMGOD_RC_DEBUG=1) ──
	rcDbg rcDebugState

	// ── RC override ที่ core ถืออยู่ (ให้ cockpit สั่งได้ทั้งที่ยังใช้ RC passthrough) ──
	// ArduPilot ปล่อย override ทิ้งเองราว 3 วิถ้าไม่มีข้อความใหม่ จึงต้องส่งซ้ำเรื่อย ๆ
	ovrMu    sync.Mutex
	ovrCh    map[uint8]uint16 // channel -> PWM ที่ override อยู่ (ว่าง = ไม่ override อะไรเลย)
	ovrStop  chan struct{}    // ปิดเพื่อหยุด loop ส่งซ้ำ
	ovrSince time.Time        // เวลาที่ชุด override ปัจจุบันถูกตั้ง (ใช้จับ "FC ไม่รับ")

	connected      bool
	verified       bool
	serial         uint64 // hardware UID จาก AUTOPILOT_VERSION (0 = ยังไม่รู้/SITL) — spec §6.1
	connectStart   time.Time
	firstTelemetry time.Time
	lastMsg        time.Time
	positionAt     time.Time // GLOBAL_POSITION_INT sample time; must not be refreshed by heartbeat/other MAVLink
	gpsAt          time.Time // GPS_RAW_INT fix sample time; navigation validity requires a current fix sample

	// COMMAND_ACK waiting: MAV_CMD -> channel รับ result code
	ackMu       sync.Mutex
	pendingAcks map[common.MAV_CMD]chan int32

	// sendMu serialize COMMAND_LONG+ACK ต่อโดรน (spec §8.2)
	// กัน pendingAcks[cmd] ชนกันเมื่อ user command กับ failsafe RTL ยิงพร้อมกัน
	sendMu sync.Mutex
}

func newDrone(id uint32, name, host string, port uint32, conn sender) *Drone {
	return &Drone{
		ID: id, Name: name, Host: host, Port: port, conn: conn,
		mode: "IDLE", modeEnum: pb.FlightMode_FLIGHT_MODE_UNKNOWN,
		connectStart: time.Now(), connected: true, targetSystem: 1,
		pendingAcks: make(map[common.MAV_CMD]chan int32),
	}
}

// HandleFrame อัปเดต state จาก MAVLink (เรียกจาก goroutine ของ conn)
func (d *Drone) HandleFrame(sysID byte, msg message.Message) {
	d.mu.Lock()
	d.lastMsg = time.Now()
	if sysID != 0 {
		d.targetSystem = sysID
	}
	if d.firstTelemetry.IsZero() {
		d.firstTelemetry = time.Now()
		d.verified = true
	}

	switch m := msg.(type) {
	case *ardupilotmega.MessageHeartbeat:
		d.armed = m.BaseMode&minimal.MAV_MODE_FLAG_SAFETY_ARMED != 0
		d.mode, d.modeEnum = decodeMode(m.CustomMode)
		// วัดจังหวะ heartbeat จริง → ใช้เป็น link quality ที่ใช้ได้ทุก transport
		// (SITL ไม่มีวิทยุ จึงไม่มี RSSI แต่ยังวัด "ลิงก์ดีแค่ไหน" จาก heartbeat ได้)
		if now := time.Now(); !d.hbLast.IsZero() {
			gap := now.Sub(d.hbLast).Seconds() * 1000.0
			if gap > 0 && gap < 10000 { // ทิ้ง outlier ตอนเพิ่งต่อ/หลุดยาว
				if d.hbIntervalMs == 0 {
					d.hbIntervalMs = gap
				} else {
					d.hbIntervalMs = d.hbIntervalMs*0.8 + gap*0.2 // EMA กันค่ากระโดด
				}
			}
			d.hbLast = now
		} else {
			d.hbLast = time.Now()
		}
	case *ardupilotmega.MessageRcChannels:
		// สวิตช์บนรีโมท — ปุ่ม A อยู่ RC7, ปุ่ม B อยู่ RC8
		d.rcCh7 = uint32(m.Chan7Raw)
		d.rcCh8 = uint32(m.Chan8Raw)
		d.rcValid = true
		d.rcAt = time.Now()
		d.logRC(m) // diagnostic: ทุกช่อง (เปิดด้วย SWARMGOD_RC_DEBUG=1)
	case *ardupilotmega.MessageRcChannelsRaw:
		// FC รุ่นเก่า/สตรีมอีกแบบส่งข้อความนี้แทน RC_CHANNELS — รับทั้งคู่ไว้
		d.rcCh7 = uint32(m.Chan7Raw)
		d.rcCh8 = uint32(m.Chan8Raw)
		d.rcValid = true
		d.rcAt = time.Now()
	case *ardupilotmega.MessageServoOutputRaw:
		// ค่า PWM ที่ FC ส่งออกจริง — ไม่สนว่าใครสั่ง (cockpit หรือรีโมท)
		d.servoCh7 = uint32(m.Servo7Raw)
		d.servoCh8 = uint32(m.Servo8Raw)
		d.servoValid = true
		d.servoAt = time.Now()
		d.logServo(m) // diagnostic: ทุกขา (เปิดด้วย SWARMGOD_RC_DEBUG=1)
	case *ardupilotmega.MessageRadioStatus:
		// มีเฉพาะเมื่อต่อผ่านวิทยุ SiK (telemetry radio) — ค่าเป็นสเกล 0..254
		// แปลงเป็น dBm ตามสูตรของ SiK: dBm = rssi/1.9 - 127
		d.rssi = int32(float64(m.Rssi)/1.9 - 127.0)
		d.rssiValid = true
		d.rssiAt = time.Now()
	case *ardupilotmega.MessageGlobalPositionInt:
		d.lat = float64(m.Lat) / 1e7
		d.lon = float64(m.Lon) / 1e7
		d.altAbs = float64(m.Alt) / 1000.0
		d.altRel = float64(m.RelativeAlt) / 1000.0
		d.positionAt = time.Now()
		// อัปเดตจุดปล่อยขณะอยู่บนพื้นเสมอ รองรับทั้ง TAKEOFF จาก Cockpit
		// และการขึ้นบินด้วยรีโมท.  หลังพ้นพื้นแล้วค่าจะ freeze ตลอด sortie.
		d.captureLaunchPositionLocked(false)
		d.vx = float64(m.Vx) / 100.0
		d.vy = float64(m.Vy) / 100.0
		d.vz = float64(m.Vz) / 100.0
		if m.Hdg != 65535 {
			d.heading = float64(m.Hdg) / 100.0
		}
	case *ardupilotmega.MessageSysStatus:
		// อย่า clobber ด้วย 0 (บาง FC ส่ง voltage=0 ใน SYS_STATUS แต่รายงานจริงใน BATTERY_STATUS)
		if m.VoltageBattery != 65535 && m.VoltageBattery != 0 {
			d.voltage = float64(m.VoltageBattery) / 1000.0
		}
		if m.CurrentBattery != -1 {
			d.current = float64(m.CurrentBattery) / 100.0
		}
		if m.BatteryRemaining != -1 {
			d.battPct = float64(m.BatteryRemaining)
		}
		d.dropRate = uint32(m.DropRateComm) // ‰ แพ็กเก็ตที่หายระหว่างทาง
	case *ardupilotmega.MessageBatteryStatus:
		// บาง FC (BATT_MONITOR analog/CAN) รายงานแบตทาง BATTERY_STATUS ไม่ใช่ SYS_STATUS
		// รวมแรงดันทุกเซลล์ที่วัดได้ (0xFFFF = ไม่ใช้). cell0 อาจถือแรงดันรวมถ้าไม่มีค่าแยกเซลล์
		var mv int
		for _, cv := range m.Voltages {
			if cv != 65535 {
				mv += int(cv)
			}
		}
		if mv > 0 {
			d.voltage = float64(mv) / 1000.0
		}
		if m.CurrentBattery != -1 {
			d.current = float64(m.CurrentBattery) / 100.0
		}
		if m.BatteryRemaining != -1 {
			d.battPct = float64(m.BatteryRemaining)
		}
	case *ardupilotmega.MessageGpsRawInt:
		d.gpsFix = int(m.FixType)
		d.sats = int(m.SatellitesVisible)
		d.gpsAt = time.Now()
	case *ardupilotmega.MessageAttitude:
		d.roll = float64(m.Roll) * 180.0 / math.Pi
		d.pitch = float64(m.Pitch) * 180.0 / math.Pi
	case *ardupilotmega.MessageVfrHud:
		d.groundSpeed = float64(m.Groundspeed)
	case *ardupilotmega.MessageAutopilotVersion:
		// hardware UID = ตัวตนถาวรของ FC (ส่งมาเมื่อ FC ตอบ REQUEST_MESSAGE; SITL มักเป็น 0)
		if m.Uid != 0 {
			d.serial = m.Uid
		}
	}
	d.mu.Unlock()

	// COMMAND_ACK → ส่งเข้า channel ที่รออยู่ (นอก state lock)
	if ack, ok := msg.(*ardupilotmega.MessageCommandAck); ok {
		d.deliverAck(ack.Command, int32(ack.Result))
	}
	// STATUSTEXT จาก FC (เช่น "PreArm: ...") → log (Phase 6 จะ forward เป็น Event)
	if st, ok := msg.(*ardupilotmega.MessageStatustext); ok {
		log.Printf("[UAV_%d FC] %s", d.ID, st.Text)
	}
}

func (d *Drone) deliverAck(cmd common.MAV_CMD, result int32) {
	d.ackMu.Lock()
	ch := d.pendingAcks[cmd]
	d.ackMu.Unlock()
	if ch != nil {
		select {
		case ch <- result:
		default:
		}
	}
}

// ════════════════ COMMANDS (ส่ง MAVLink) ════════════════

func (d *Drone) targetSys() byte {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.targetSystem == 0 {
		return 1
	}
	return d.targetSystem
}

// sendCmd ส่ง COMMAND_LONG แล้วรอ COMMAND_ACK (result 0 = ACCEPTED)
// serialize ต่อโดรน (sendMu): มีคำสั่งค้าง ACK ได้ทีละ 1 → pendingAcks[cmd] ไม่ชนกัน (spec §8.2)
func (d *Drone) sendCmd(ctx context.Context, cmd common.MAV_CMD, p [7]float32) (int32, error) {
	d.sendMu.Lock()
	defer d.sendMu.Unlock()

	ch := make(chan int32, 1)
	d.ackMu.Lock()
	d.pendingAcks[cmd] = ch
	d.ackMu.Unlock()
	defer func() {
		d.ackMu.Lock()
		delete(d.pendingAcks, cmd)
		d.ackMu.Unlock()
	}()

	msg := &common.MessageCommandLong{
		TargetSystem: d.targetSys(), TargetComponent: 1,
		Command: cmd, Confirmation: 0,
		Param1: p[0], Param2: p[1], Param3: p[2], Param4: p[3],
		Param5: p[4], Param6: p[5], Param7: p[6],
	}
	if err := guardedSend(ctx, func() error { return d.conn.Send(msg) }); err != nil {
		return -1, err
	}
	select {
	case <-ctx.Done():
		return -1, fmt.Errorf("no ACK (timeout)")
	case r := <-ch:
		return r, nil
	}
}

func (d *Drone) Arm(ctx context.Context, force bool) (int32, error) {
	p2 := float32(0)
	if force {
		p2 = 21196 // magic = force arm
	}
	return d.sendCmd(ctx, common.MAV_CMD_COMPONENT_ARM_DISARM, [7]float32{1, p2})
}

func (d *Drone) Disarm(ctx context.Context) (int32, error) {
	return d.sendCmd(ctx, common.MAV_CMD_COMPONENT_ARM_DISARM, [7]float32{0})
}

// Kill = force disarm (motor cut) ของ ArduPilot; ใช้ได้เฉพาะหลัง core ตรวจ
// explicit confirmation แล้วเท่านั้น
func (d *Drone) Kill(ctx context.Context) (int32, error) {
	return d.sendCmd(ctx, common.MAV_CMD_COMPONENT_ARM_DISARM, [7]float32{0, 21196})
}

func (d *Drone) SetMode(ctx context.Context, modeNum uint32) (int32, error) {
	// DO_SET_MODE: param1=base_mode (custom enabled=1), param2=custom_mode
	return d.sendCmd(ctx, common.MAV_CMD_DO_SET_MODE,
		[7]float32{float32(minimal.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float32(modeNum)})
}

func (d *Drone) Takeoff(ctx context.Context, alt float64) (int32, error) {
	// Takeoff composite arm มาก่อนถึงเมธอดนี้ จึง capture ซ้ำตรง boundary
	// คำสั่งโดยไม่อิง armed flag (แต่ยังบังคับว่าต้องอยู่ใกล้พื้น).
	d.mu.Lock()
	d.captureLaunchPositionLocked(true)
	d.mu.Unlock()
	return d.sendCmd(ctx, common.MAV_CMD_NAV_TAKEOFF,
		[7]float32{0, 0, 0, 0, 0, 0, float32(alt)})
}

const launchCaptureMaxAltM = 1.5

func validLaunchCoordinate(lat, lon float64) bool {
	return !math.IsNaN(lat) && !math.IsNaN(lon) &&
		!math.IsInf(lat, 0) && !math.IsInf(lon, 0) &&
		(lat != 0 || lon != 0) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180
}

// captureLaunchPositionLocked ต้องเรียกขณะถือ d.mu.
func (d *Drone) captureLaunchPositionLocked(force bool) {
	if (!force && d.armed) || d.altRel > launchCaptureMaxAltM ||
		!validLaunchCoordinate(d.lat, d.lon) {
		return
	}
	// GlobalPosition อาจมาก่อน heartbeat รอบล่าสุด จึงยอม capture ที่ระดับพื้น;
	// เงื่อนไขความสูงป้องกันไม่ให้ตำแหน่งระหว่างบินมาทับ launch point.
	d.launchLat, d.launchLon, d.launchSet = d.lat, d.lon, true
}

// LaunchPosition คืนจุดปล่อยจริงล่าสุดที่ยืนยันตอนอยู่บนพื้น.
func (d *Drone) LaunchPosition() (lat, lon float64, ok bool) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.launchLat, d.launchLon, d.launchSet
}

func (d *Drone) ReturnHome(ctx context.Context) (int32, error) {
	return d.sendCmd(ctx, common.MAV_CMD_NAV_RETURN_TO_LAUNCH, [7]float32{})
}

func (d *Drone) LandNow(ctx context.Context) (int32, error) {
	return d.sendCmd(ctx, common.MAV_CMD_NAV_LAND, [7]float32{})
}

// ServoPWM ขอบเขต PWM ที่ยอมรับ (ตาม servo.md §5 — กว้างกว่าช่วง UI 1000–2000
// เพื่อรองรับ RELEASE 2100 / RELOAD 900)
const (
	ServoPWMMin     = 900
	ServoPWMMax     = 2100
	ServoPWMNeutral = 1500
	ServoChanA      = 7 // ปุ่ม A
	ServoChanB      = 8 // ปุ่ม B (ช่องเดิมของ GCS_1 — bomb drop)
)

// ClampServoPWM บีบค่าให้อยู่ในขอบเขตที่ FC ยอมรับ (แยกไว้ให้เทสต์ได้ตรง ๆ)
func ClampServoPWM(pwm uint32) uint32 {
	if pwm < ServoPWMMin {
		return ServoPWMMin
	}
	if pwm > ServoPWMMax {
		return ServoPWMMax
	}
	return pwm
}

// SetServo สั่ง servo output ช่องที่ระบุ ผ่าน DO_SET_SERVO (MAV_CMD 183)
// param1 = ช่อง servo, param2 = PWM (μs) — ตาม servo.md §7
//
// ต่างจาก GCS_1 เดิมตรงที่ "รอ ACK" (sendCmd) แทน fire-and-forget เพราะการปล่อยของ
// เป็นคำสั่งที่ต้องรู้ผลจริง (servo.md §9 ข้อ 3 ระบุไว้เองว่าควรแก้)
func (d *Drone) SetServo(ctx context.Context, channel, pwm uint32) (int32, error) {
	if channel == 0 {
		channel = ServoChanB // เข้ากันได้ย้อนหลังกับ request เก่าที่ไม่ได้ระบุช่อง
	}
	return d.sendCmd(ctx, common.MAV_CMD_DO_SET_SERVO,
		[7]float32{float32(channel), float32(ClampServoPWM(pwm))})
}

// ── RC_CHANNELS_OVERRIDE — ให้ cockpit สั่งได้ทั้งที่ FC ยังใช้ RC passthrough ──
//
// ทำไมไม่ใช้ DO_SET_SERVO: ถ้า SERVOn_FUNCTION = RCINn (passthrough) FC จะเขียนค่า
// จาก RC ลงขาเซอร์โว "ทุก loop" (50–400 Hz) คำสั่ง one-shot อย่าง DO_SET_SERVO
// จึงถูกทับทันที · override ทำให้ FC มองว่าค่านั้น "มาจากรีโมท" จึงผ่าน passthrough ได้
//
// ⚠️ ความปลอดภัย: ช่องที่ไม่ได้ตั้งค่าใน RC_CHANNELS_OVERRIDE จะเป็น 0 = "ปล่อยให้
// รีโมทจริงคุม" ซึ่งเป็นค่า zero-value ของ Go พอดี — คันบังคับ ch1-4 (roll/pitch/
// throttle/yaw) จึงไม่มีทางถูก override โดยบังเอิญ
const (
	rcOverrideResend = 500 * time.Millisecond // ส่งซ้ำถี่กว่า timeout ของ FC (~3 วิ) มาก
)

// SetRCOverride ตั้ง override ช่องเดียว แล้วเริ่ม/ต่ออายุ loop ส่งซ้ำ
func (d *Drone) SetRCOverride(channel uint8, pwm uint16) error {
	if channel == 0 || channel > 18 {
		return fmt.Errorf("RC channel %d อยู่นอกช่วง 1-18", channel)
	}
	if channel <= 4 {
		// กันพลาดระดับโค้ด: ห้าม override คันบังคับหลักเด็ดขาด
		return fmt.Errorf("ห้าม override RC%d (คันบังคับหลัก)", channel)
	}
	d.ovrMu.Lock()
	if d.ovrCh == nil {
		d.ovrCh = make(map[uint8]uint16)
	}
	d.ovrCh[channel] = pwm
	d.ovrSince = time.Now() // เริ่มจับเวลาว่า FC ยอมรับคำสั่งนี้เมื่อไหร่
	needStart := d.ovrStop == nil
	if needStart {
		d.ovrStop = make(chan struct{})
	}
	stop := d.ovrStop
	d.ovrMu.Unlock()

	if needStart {
		go d.rcOverrideLoop(stop)
	}
	return d.sendRCOverride()
}

// ClearRCOverride เลิก override ช่องนั้น (คืนช่องให้รีโมทจริง)
// ถ้าไม่เหลือช่องไหน override อยู่เลย จะหยุด loop ส่งซ้ำด้วย
func (d *Drone) ClearRCOverride(channel uint8) error {
	d.ovrMu.Lock()
	delete(d.ovrCh, channel)
	d.ovrSince = time.Now()
	empty := len(d.ovrCh) == 0
	if empty && d.ovrStop != nil {
		close(d.ovrStop)
		d.ovrStop = nil
	}
	d.ovrMu.Unlock()
	// ส่งอีกครั้งพร้อมค่า 0 ของช่องนั้น = บอก FC ให้คืนช่องให้รีโมท
	return d.sendRCOverride()
}

// ClearAllRCOverride ปล่อยทุกช่อง (ตรงกับ servo_reset() ของ GCS_1 — servo.md §3.3)
func (d *Drone) ClearAllRCOverride() error {
	d.ovrMu.Lock()
	d.ovrCh = nil
	if d.ovrStop != nil {
		close(d.ovrStop)
		d.ovrStop = nil
	}
	d.ovrMu.Unlock()
	return d.conn.Send(&common.MessageRcChannelsOverride{
		TargetSystem: d.targetSys(), TargetComponent: 1,
	})
}

// RCOverrideActive คืนช่องที่กำลัง override อยู่ (ใช้ในเทสต์/ตรวจสถานะ)
func (d *Drone) RCOverrideActive() map[uint8]uint16 {
	d.ovrMu.Lock()
	defer d.ovrMu.Unlock()
	out := make(map[uint8]uint16, len(d.ovrCh))
	for k, v := range d.ovrCh {
		out[k] = v
	}
	return out
}

// RCWarmup ส่ง "ปล่อยทุกช่อง" ตั้งแต่เพิ่งเชื่อมต่อ เพื่อให้ FC เห็น stream นี้ล่วงหน้า
// ก่อนผู้ใช้กดปุ่มจริง (เหตุผลเต็ม + สิ่งที่ตัดออกไปแล้ว ดู rcdebug.go)
//
// ค่า 0 ทุก field = "คืนช่องให้ RC จริง" ตามสเปก MAVLink → ไม่แตะการควบคุมใด ๆ
// หยุดทันทีถ้ามี override จริงเกิดขึ้น (loop ปกติรับช่วงต่อ) หรือหมดเวลา/ปิด connection
func (d *Drone) RCWarmup(ctx context.Context) {
	deadline := time.After(rcWarmupFor)
	t := time.NewTicker(rcWarmupPeriod)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-deadline:
			return
		case <-t.C:
			d.ovrMu.Lock()
			busy := len(d.ovrCh) > 0
			d.ovrMu.Unlock()
			if busy {
				return // มีคำสั่งจริงแล้ว ปล่อยให้ rcOverrideLoop ทำงานแทน
			}
			if err := d.sendRCOverride(); err != nil {
				return // ส่งไม่ได้ = ลิงก์มีปัญหา ไม่ต้องรบกวนต่อ
			}
		}
	}
}

func (d *Drone) rcOverrideLoop(stop chan struct{}) {
	t := time.NewTicker(rcOverrideResend)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			if err := d.sendRCOverride(); err != nil {
				log.Printf("[UAV_%d] RC override resend failed: %v", d.ID, err)
			}
		}
	}
}

// sendRCOverride ประกอบข้อความจาก map ปัจจุบัน — ช่องที่ไม่ได้ override จะเป็น 0
// (= คืนให้รีโมทจริง) ตามสเปก MAVLink
func (d *Drone) sendRCOverride() error {
	d.ovrMu.Lock()
	ch := make(map[uint8]uint16, len(d.ovrCh))
	for k, v := range d.ovrCh {
		ch[k] = v
	}
	d.ovrMu.Unlock()
	d.logOverride(ch) // diagnostic (SWARMGOD_RC_DEBUG=1)

	m := &common.MessageRcChannelsOverride{
		TargetSystem: d.targetSys(), TargetComponent: 1,
	}
	// เซ็ตเฉพาะช่องที่รองรับการปล่อยของ (5-8) — ช่องอื่นคงเป็น 0 เสมอ
	m.Chan5Raw = ch[5]
	m.Chan6Raw = ch[6]
	m.Chan7Raw = ch[7]
	m.Chan8Raw = ch[8]
	return d.conn.Send(m)
}

// Goto ส่ง SET_POSITION_TARGET_GLOBAL_INT (position-only mask) — ไม่มี ACK
func (d *Drone) GotoContext(ctx context.Context, lat, lon, alt float64) error {
	const posOnly = 3576 // ignore vel+accel+yaw+yawrate
	msg := &common.MessageSetPositionTargetGlobalInt{
		TargetSystem: d.targetSys(), TargetComponent: 1,
		CoordinateFrame: common.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
		TypeMask:        common.POSITION_TARGET_TYPEMASK(posOnly),
		LatInt:          int32(lat * 1e7),
		LonInt:          int32(lon * 1e7),
		Alt:             float32(alt),
	}
	return guardedSend(ctx, func() error { return d.conn.Send(msg) })
}

func (d *Drone) Goto(lat, lon, alt float64) error {
	return d.GotoContext(context.Background(), lat, lon, alt)
}

// MoveVelocity สั่งความเร็ว body-frame (vx=หน้า, vy=ขวา, vz=ลง m/s; yawRate rad/s)
// สำหรับบังคับด้วยรีโมท/ปุ่มทิศทาง (ต้องส่งซ้ำ ~5Hz; หยุดส่ง=หยุด)
func (d *Drone) MoveVelocityContext(ctx context.Context, vx, vy, vz, yawRate float64) error {
	const velMask = 1479 // ignore pos+accel+yaw ; keep vel+yawrate
	msg := &common.MessageSetPositionTargetLocalNed{
		TargetSystem: d.targetSys(), TargetComponent: 1,
		CoordinateFrame: common.MAV_FRAME_BODY_OFFSET_NED,
		TypeMask:        common.POSITION_TARGET_TYPEMASK(velMask),
		Vx:              float32(vx),
		Vy:              float32(vy),
		Vz:              float32(vz),
		YawRate:         float32(yawRate),
	}
	return guardedSend(ctx, func() error { return d.conn.Send(msg) })
}

func (d *Drone) MoveVelocity(vx, vy, vz, yawRate float64) error {
	return d.MoveVelocityContext(context.Background(), vx, vy, vz, yawRate)
}

// SetParam ตั้งค่าพารามิเตอร์ของ FC (PARAM_SET)
//
// param_id ใน MAVLink ยาวได้ไม่เกิน 16 ตัวอักษร — ยาวเกินถือว่าผิด
// ไม่รอ PARAM_VALUE ตอบกลับ: ArduPilot ตอบเป็น broadcast ไม่ใช่ ACK ต่อคำสั่ง
// ผู้เรียกที่ต้องการยืนยันค่าให้ไปอ่านจาก telemetry/ParamGet เอา
func (d *Drone) SetParam(id string, value float64) error {
	if id == "" || len(id) > 16 {
		return fmt.Errorf("param id ต้องยาว 1-16 ตัวอักษร (ได้ %q)", id)
	}
	msg := &common.MessageParamSet{
		TargetSystem: d.targetSys(), TargetComponent: 1,
		ParamId: id,
		// ArduPilot ส่ง/รับค่าพารามิเตอร์เป็น float32 เสมอ ไม่ว่าชนิดจริงจะเป็นอะไร
		ParamValue: float32(value),
		ParamType:  common.MAV_PARAM_TYPE_REAL32,
	}
	return d.conn.Send(msg)
}

// GotoYawContext is the cancellable/final-write-guarded yaw variant used by
// swarm form-up. A higher-priority takeover can therefore close the same tiny
// transport boundary used by other guarded navigation writes.
func (d *Drone) GotoYawContext(ctx context.Context, lat, lon, alt, yawDeg float64) error {
	const posYaw = 2552 // ignore vel+accel+yawrate (ไม่ ignore yaw)
	msg := &common.MessageSetPositionTargetGlobalInt{
		TargetSystem: d.targetSys(), TargetComponent: 1,
		CoordinateFrame: common.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
		TypeMask:        common.POSITION_TARGET_TYPEMASK(posYaw),
		LatInt:          int32(lat * 1e7),
		LonInt:          int32(lon * 1e7),
		Alt:             float32(alt),
		Yaw:             float32(yawDeg * math.Pi / 180.0),
	}
	return guardedSend(ctx, func() error { return d.conn.Send(msg) })
}

// GotoYaw เหมือน Goto แต่บังคับ yaw ด้วย (deg, 0=เหนือ) — ให้ลูกหันหน้าตามแม่
func (d *Drone) GotoYaw(lat, lon, alt, yawDeg float64) error {
	return d.GotoYawContext(context.Background(), lat, lon, alt, yawDeg)
}

// ════════════════ STATE / SNAPSHOT ════════════════

func (d *Drone) status() pb.LinkStatus {
	if !d.connected {
		return pb.LinkStatus_LINK_STATUS_OFFLINE
	}
	if !d.verified {
		return pb.LinkStatus_LINK_STATUS_CONNECTING
	}
	if !d.armed {
		return pb.LinkStatus_LINK_STATUS_READY
	}
	switch d.mode {
	case "RTL":
		return pb.LinkStatus_LINK_STATUS_RTL
	case "LAND":
		return pb.LinkStatus_LINK_STATUS_LANDING
	case "LOITER", "POSHOLD", "BRAKE":
		return pb.LinkStatus_LINK_STATUS_HOLD
	default:
		if d.altRel > 1.0 {
			return pb.LinkStatus_LINK_STATUS_FLYING
		}
		return pb.LinkStatus_LINK_STATUS_ARMED
	}
}

func (d *Drone) Snapshot() *pb.Telemetry {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return &pb.Telemetry{
		DroneId:           d.ID,
		Name:              d.Name,
		Status:            d.status(),
		Mode:              d.modeEnum,
		Armed:             d.armed,
		Position:          &pb.GeoPoint{Lat: d.lat, Lon: d.lon, AltRel: d.altRel, AltAbs: d.altAbs},
		Velocity:          &pb.Vector3{X: d.vx, Y: d.vy, Z: d.vz},
		GroundSpeed:       d.groundSpeed,
		Heading:           d.heading,
		Roll:              d.roll,
		Pitch:             d.pitch,
		BatteryPct:        d.battPct,
		Voltage:           d.voltage,
		Current:           d.current,
		GpsFix:            pb.GpsFix(d.gpsFix),
		SatCount:          uint32(d.sats),
		ConnectElapsed:    time.Since(d.connectStart).Seconds(),
		TelemetryVerified: d.verified,
		Host:              d.Host,
		Port:              d.Port,
		TimestampMs:       time.Now().UnixMilli(),
		Rssi:              d.rssi,
		RssiValid:         d.rssiFresh(),
		LinkQuality:       d.linkQuality(),
		DropRate:          d.dropRate,
		ServoCh7Pwm:       d.servoCh7,
		ServoCh8Pwm:       d.servoCh8,
		ServoValid:        d.servoFresh(),
		RcCh7Raw:          d.rcCh7,
		RcCh8Raw:          d.rcCh8,
		RcValid:           d.rcFresh(),
		OvrCh7:            d.isOverriding(ServoChanA),
		OvrCh8:            d.isOverriding(ServoChanB),
	}
}

// isOverriding — core กำลังถือ override ช่องนี้อยู่ไหม
// ใช้แยกว่า "ค่า RC ที่เห็น" มาจาก cockpit หรือจากคนโยกสวิตช์จริง
func (d *Drone) isOverriding(ch uint8) bool {
	d.ovrMu.Lock()
	defer d.ovrMu.Unlock()
	_, ok := d.ovrCh[ch]
	return ok
}

// rcFresh — ค่า RC ใช้ได้เฉพาะเมื่อรีโมทยังส่งอยู่ (ภายใน 5 วิ)
// รีโมทดับ/ขาดสัญญาณแล้วต้องไม่ค้างค่าเดิมหลอกว่ายังกดสวิตช์ค้างอยู่
func (d *Drone) rcFresh() bool {
	return d.rcValid && time.Since(d.rcAt) < 5*time.Second
}

// servoFresh — ค่า servo ใช้ได้เฉพาะเมื่อ FC ยังรายงาน SERVO_OUTPUT_RAW อยู่ (ภายใน 5 วิ)
// ถ้า FC เงียบไปแล้วต้องไม่ค้างสถานะเก่าไว้หลอกผู้ใช้ว่ายังปล่อยของอยู่
func (d *Drone) servoFresh() bool {
	return d.servoValid && time.Since(d.servoAt) < 5*time.Second
}

// rssiFresh — RSSI ถือว่าใช้ได้เฉพาะเมื่อวิทยุยังรายงานอยู่ (ภายใน 5 วิ)
// ถ้าวิทยุถูกถอด/เงียบ ต้องกลับไปเป็น "ไม่มีข้อมูล" ไม่ใช่ค้างค่าเดิมไว้ตลอด
func (d *Drone) rssiFresh() bool {
	return d.rssiValid && time.Since(d.rssiAt) < 5*time.Second
}

// linkQuality คืน 0..100 จากข้อมูลจริง (ไม่ใช่ค่าคงที่)
//
//	มีวิทยุ SiK  → อิง RSSI (-120 dBm = 0%, -50 dBm = 100%)
//	ไม่มีวิทยุ   → อิงจังหวะ heartbeat จริง (ควรมาทุก ~1 วิ; ยิ่งห่าง = ยิ่งแย่)
//	              แล้วหักด้วย drop_rate จาก SYS_STATUS ถ้ามี
func (d *Drone) linkQuality() uint32 {
	if !d.connected {
		return 0
	}
	var q float64
	switch {
	case d.rssiFresh():
		q = (float64(d.rssi) + 120.0) / 70.0 * 100.0
	case d.hbIntervalMs > 0:
		// heartbeat ปกติ 1000 ms → 100%; ห่าง 3000 ms ขึ้นไป → 0%
		q = (3000.0 - d.hbIntervalMs) / 2000.0 * 100.0
	default:
		return 0 // ยังไม่มีข้อมูลพอจะบอกคุณภาพ — อย่าเดา
	}
	if d.dropRate > 0 {
		q -= float64(d.dropRate) / 10.0 // ‰ → %
	}
	if q < 0 {
		q = 0
	} else if q > 100 {
		q = 100
	}
	return uint32(q)
}

func (d *Drone) SafetyState() safety.DroneState {
	d.mu.RLock()
	defer d.mu.RUnlock()
	age := -1.0
	if !d.lastMsg.IsZero() {
		age = time.Since(d.lastMsg).Seconds()
	}
	positionAge := -1.0
	if !d.positionAt.IsZero() {
		positionAge = time.Since(d.positionAt).Seconds()
	}
	gpsAge := -1.0
	if !d.gpsAt.IsZero() {
		gpsAge = time.Since(d.gpsAt).Seconds()
	}
	return safety.DroneState{
		ID: d.ID, Lat: d.lat, Lon: d.lon, AltRel: d.altRel,
		BatteryPct: d.battPct, SatCount: d.sats, GpsFix: d.gpsFix,
		Armed: d.armed, Mode: d.mode, Heading: d.heading,
		TelemetryAgeSec: age, PositionAgeSec: positionAge, GpsAgeSec: gpsAge,
	}
}

func (d *Drone) IsArmed() bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.armed
}

// SystemID คืน MAVLink System ID ที่ค้นพบจาก heartbeat (spec §6.1)
func (d *Drone) SystemID() byte {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.targetSystem
}

// Serial คืน hardware UID (0 = ยังไม่รู้)
func (d *Drone) Serial() uint64 {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.serial
}

// Verified = ได้ telemetry frame แรกแล้ว (System ID เชื่อถือได้)
func (d *Drone) Verified() bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.verified
}

// Online: verified + ได้ telemetry ล่าสุดภายใน within วินาที (สำหรับ swarm failover)
func (d *Drone) Online(within float64) bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if !d.verified || d.lastMsg.IsZero() {
		return false
	}
	return time.Since(d.lastMsg).Seconds() < within
}

// Nav คืน lat, lon, altRel, heading (สำหรับคำนวณ formation)
func (d *Drone) Nav() (lat, lon, altRel, heading float64) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.lat, d.lon, d.altRel, d.heading
}

// SecondsSinceLastMsg คืนเวลาตั้งแต่ได้ telemetry ล่าสุด (-1 ถ้ายังไม่เคยได้) — สำหรับ failsafe
func (d *Drone) SecondsSinceLastMsg() float64 {
	d.mu.RLock()
	defer d.mu.RUnlock()
	if d.lastMsg.IsZero() {
		return -1
	}
	return time.Since(d.lastMsg).Seconds()
}

func (d *Drone) Mode() string {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.mode
}
