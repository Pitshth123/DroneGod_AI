// Package command — Dispatcher: ทุกคำสั่งวิ่งผ่านที่นี่
//
//	validate → safety.Envelope.Check → audit.Log → ส่ง MAVLink → รอ ACK
//
// (ดู ARCHITECTURE.md §4.2 — "กฎเหล็ก: ทุก command ผ่าน safety ก่อนเสมอ")
package command

import (
	"context"
	"fmt"
	"math"
	"strings"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/safety"
	"github.com/swarmgod/backend/pkg/geo"
)

const cmdTimeout = 5 * time.Second

// งบเวลาลำดับ takeoff (GUIDED→arm→takeoff) — ต้องคลุมช่วงรอ EKF/pre-arm พร้อม
// armAttempts × (cmdTimeout + 2s หน่วง) ต้องไม่เกิน takeoffSeqTimeout
const (
	takeoffSeqTimeout = 90 * time.Second
	armAttempts       = 10
	// รอ GPS/sat พร้อมก่อน arm (transient — เพิ่งต่อมักใช้เวลาไม่กี่วินาที)
	armReadyWait = 25 * time.Second
	armReadyPoll = 500 * time.Millisecond
)

type Service struct {
	mgr   *fleet.Manager
	env   *safety.Envelope
	audit *audit.Logger
	idem  *idemStore
}

func NewService(mgr *fleet.Manager, env *safety.Envelope, aud *audit.Logger) *Service {
	return &Service{mgr: mgr, env: env, audit: aud, idem: newIdemStore(60 * time.Second)}
}

// Idempotent รัน fn ครั้งเดียวต่อ (requestID, droneID) ภายใน retention window (spec §8.2)
// requestID ว่าง = ไม่ dedup. ใช้ห่อคำสั่ง mutating ที่ api layer
// บันทึก request-level audit พร้อม request_id (spec §16.1) — ไม่แตะ command critical path
func (s *Service) Idempotent(requestID string, droneID uint32, fn func() *pb.CommandResult) *pb.CommandResult {
	res, replay := s.idem.do(requestID, droneID, fn)
	if requestID != "" && res != nil {
		s.audit.Request(requestID, droneID, res.Command, res.Outcome.String(), res.Ok, replay)
	}
	return res
}

// RecordCorrelation writes the frontend CommandGateway identity plus the final
// request/result linkage at the Core RPC boundary. It is observability-only and
// cannot authorize, suppress, preempt, or otherwise influence command execution.
func (s *Service) RecordCorrelation(operationID, commandID, attemptID, rpcMethod,
	requestID, command, outcome string, ok bool, handlerError string) {
	if s == nil || s.audit == nil {
		return
	}
	s.audit.Correlation(operationID, commandID, attemptID, rpcMethod,
		requestID, command, outcome, ok, handlerError)
}

// ── helpers ────────────────────────────────────────────────
func res(id uint32, cmd string, ok bool, code int32, msg string) *pb.CommandResult {
	return &pb.CommandResult{Ok: ok, DroneId: id, Command: cmd, ResultCode: code, Message: msg}
}

func (s *Service) reject(id uint32, cmd, reason string) *pb.CommandResult {
	s.audit.Command(id, cmd, false, reason, -1)
	r := res(id, cmd, false, -1, reason)
	r.Outcome = pb.CommandOutcome_OUTCOME_SAFETY_REJECTED
	if strings.Contains(reason, "not connected") {
		r.Outcome = pb.CommandOutcome_OUTCOME_NOT_CONNECTED
	}
	return r
}

func (s *Service) drone(id uint32, cmd string) (*fleet.Drone, *pb.CommandResult) {
	d := s.mgr.Drone(id)
	if d == nil {
		return nil, s.reject(id, cmd, "drone not connected")
	}
	return d, nil
}

// checkDuplicateSystemID ปฏิเสธ arm/takeoff ถ้า System ID ชนกับลำอื่น (spec §6.1)
// เหตุผล: ถ้า 2 ลำใช้ System ID เดียวกัน คำสั่ง/ACK อาจถูก route ผิดลำ = อันตราย
func (s *Service) checkDuplicateSystemID(id uint32, cmd string) *pb.CommandResult {
	if other, dup := s.mgr.DuplicateSystemID(id); dup {
		return s.reject(id, cmd,
			fmt.Sprintf("duplicate MAVLink System ID with drone %d — refuse (spec §6.1)", other))
	}
	return nil
}

// ackResult แปลง (code, err) จากการส่งคำสั่ง → CommandResult + audit
func (s *Service) ackResult(id uint32, cmd string, code int32, err error) *pb.CommandResult {
	if err != nil {
		s.audit.Command(id, cmd, true, "sent, "+err.Error(), -1)
		r := res(id, cmd, false, -1, err.Error())
		r.Outcome = pb.CommandOutcome_OUTCOME_TIMEOUT
		return r
	}
	ok := code == 0 // MAV_RESULT_ACCEPTED
	msg := "ACCEPTED"
	if !ok {
		msg = "rejected by FC (MAV_RESULT=" + itoa(code) + ")"
	}
	s.audit.Command(id, cmd, true, msg, code)
	r := res(id, cmd, ok, code, msg)
	r.Outcome = pb.CommandOutcome_OUTCOME_ACK_REJECTED
	if ok {
		r.Outcome = pb.CommandOutcome_OUTCOME_ACCEPTED
	}
	return r
}

// ── commands ───────────────────────────────────────────────

func (s *Service) Arm(ctx context.Context, id uint32, force bool) *pb.CommandResult {
	d, rej := s.drone(id, "Arm")
	if rej != nil {
		return rej
	}
	if rej := s.checkDuplicateSystemID(id, "Arm"); rej != nil {
		return rej
	}
	if dec := s.env.CheckArmPrecondition(d.SafetyState()); !dec.Allow {
		return s.reject(id, "Arm", dec.Reason)
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.Arm(cctx, force)
	return s.ackResult(id, "Arm", code, err)
}

func (s *Service) Disarm(ctx context.Context, id uint32, confirmed bool) *pb.CommandResult {
	d, rej := s.drone(id, "Disarm")
	if rej != nil {
		return rej
	}
	// disarm กลางอากาศต้อง confirm
	if d.SafetyState().AltRel > 1.0 {
		if dec := s.env.CheckDangerous("Disarm-in-air", confirmed); !dec.Allow {
			return s.reject(id, "Disarm", dec.Reason)
		}
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.Disarm(cctx)
	return s.ackResult(id, "Disarm", code, err)
}

// Kill ตัดมอเตอร์ทันทีด้วย force-disarm magic ของ ArduPilot
func (s *Service) Kill(ctx context.Context, id uint32, confirmed bool) *pb.CommandResult {
	d, rej := s.drone(id, "Kill")
	if rej != nil {
		return rej
	}
	if dec := s.env.CheckDangerous("Kill", confirmed); !dec.Allow {
		return s.reject(id, "Kill", dec.Reason)
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.Kill(cctx)
	return s.ackResult(id, "Kill", code, err)
}

func (s *Service) SetMode(ctx context.Context, id uint32, mode pb.FlightMode) *pb.CommandResult {
	d, rej := s.drone(id, "SetMode")
	if rej != nil {
		return rej
	}
	num, ok := fleet.ModeNumber(mode)
	if !ok {
		return s.reject(id, "SetMode", "unknown mode")
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.SetMode(cctx, num)
	return s.ackResult(id, "SetMode", code, err)
}

// waitArmReady รอจนผ่าน arm precondition (GPS fix/sat) หรือหมดเวลา
// ไม่ลดมาตรฐานความปลอดภัย: ยังไม่ยอม arm ถ้า GPS ไม่พร้อม แค่ "ให้เวลา" มันพร้อม
// เงื่อนไขถาวร (แบตต่ำ) เจอเมื่อไหร่เลิกรอทันที
func (s *Service) waitArmReady(ctx context.Context, d *fleet.Drone) safety.Decision {
	dec := s.env.CheckArmPrecondition(d.SafetyState())
	if dec.Allow || !dec.Transient {
		return dec
	}
	tick := time.NewTicker(armReadyPoll)
	defer tick.Stop()
	deadline := time.Now().Add(armReadyWait)
	for {
		select {
		case <-ctx.Done():
			return dec // คืนเหตุผลล่าสุดที่ยังไม่พร้อม
		case <-tick.C:
			dec = s.env.CheckArmPrecondition(d.SafetyState())
			if dec.Allow || !dec.Transient {
				return dec
			}
			if time.Now().After(deadline) {
				return dec
			}
		}
	}
}

// sleepCtx waits for d, or returns early false if ctx is cancelled first.  Used
// so an operator emergency/takeover that cancels the Takeoff exec context aborts
// the GUIDED→arm→takeoff sequence promptly instead of sleeping out the delays.
func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

// Takeoff = composite: GUIDED → arm → takeoff (safety: alt + arm precondition)
// TakeoffPrecheck ตรวจเฉพาะด่านที่ไม่เปลี่ยนสถานะ เพื่อให้คำสั่งหลายลำตรวจครบ
// ทุกลำก่อน ARM ลำแรก. เงื่อนไข transient ยังปล่อยให้ Takeoff รอตามปกติ.
func (s *Service) TakeoffPrecheck(id uint32, alt float64, confirmed bool) *pb.CommandResult {
	d, rej := s.drone(id, "Takeoff")
	if rej != nil {
		return rej
	}
	if rej := s.checkDuplicateSystemID(id, "Takeoff"); rej != nil {
		return rej
	}
	if dec := s.env.CheckDangerous("Takeoff", confirmed); !dec.Allow {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	if dec := s.env.CheckAltitude(alt); !dec.Allow {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	if dec := s.env.CheckArmPrecondition(d.SafetyState()); !dec.Allow && !dec.Transient {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	return res(id, "TakeoffPrecheck", true, 0, "READY")
}

func (s *Service) Takeoff(ctx context.Context, id uint32, alt float64, confirmed bool) *pb.CommandResult {
	d, rej := s.drone(id, "Takeoff")
	if rej != nil {
		return rej
	}
	if rej := s.checkDuplicateSystemID(id, "Takeoff"); rej != nil {
		return rej
	}
	if dec := s.env.CheckDangerous("Takeoff", confirmed); !dec.Allow {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	if dec := s.env.CheckAltitude(alt); !dec.Allow {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	// เงื่อนไขถาวร (เช่น แบตต่ำ) → ปฏิเสธทันที รอไปก็ไม่ดีขึ้น
	if dec := s.env.CheckArmPrecondition(d.SafetyState()); !dec.Allow && !dec.Transient {
		return s.reject(id, "Takeoff", dec.Reason)
	}
	s.audit.Command(id, "Takeoff", true, "sequence GUIDED→arm→takeoff", 0)

	// งบเวลาทั้งลำดับ: ต้องมากพอให้ EKF/pre-arm พร้อม (~30-40s หลัง boot)
	// ไม่งั้น deadline หมดก่อน arm สำเร็จ = takeoff ไม่ขึ้นตลอด
	cctx, cancel := context.WithTimeout(ctx, takeoffSeqTimeout)
	defer cancel()

	// รอ GPS/sat ให้พร้อมภายในงบเวลา แทนที่จะทิ้งคำสั่งทันที
	// (เพิ่งต่อโดรน GPS มักยังไม่ fix อีก 2-3 วินาทีก็พร้อม — เดิมสั่งพร้อมกันหลายลำ
	//  ลำที่ GPS มาช้ากว่าเพื่อนจะถูกปฏิเสธทิ้งทั้งที่ใกล้พร้อมแล้ว)
	if dec := s.waitArmReady(cctx, d); !dec.Allow {
		return s.reject(id, "Takeoff", dec.Reason)
	}

	// สำคัญ: แต่ละคำสั่งย่อยต้องมี timeout ของตัวเอง — ถ้าส่ง cctx (ยาว) เข้าไปตรง ๆ
	// sendCmd จะบล็อกรอ ACK จนหมดงบทั้งก้อนตั้งแต่ครั้งแรก ทำให้ loop retry ไม่เคยได้ทำงาน
	// และ sendMu ของลำนั้นถูกยึดยาว บล็อกคำสั่งอื่นไปด้วย
	step := func(fn func(context.Context) (int32, error)) (int32, error) {
		sctx, scancel := context.WithTimeout(cctx, cmdTimeout)
		defer scancel()
		return fn(sctx)
	}

	guided, _ := fleet.ModeNumber(pb.FlightMode_FLIGHT_MODE_GUIDED)
	if code, err := step(func(c context.Context) (int32, error) {
		return d.SetMode(c, guided)
	}); err != nil || code != 0 {
		return res(id, "Takeoff", false, code, "set GUIDED failed")
	}
	if !sleepCtx(cctx, 400*time.Millisecond) {
		return res(id, "Takeoff", false, 0, "takeoff preempted by operator/takeover before arm")
	}

	// retry arm — EKF/pre-arm ("Need Position Estimate") อาจใช้เวลา ~30-40s หลัง boot
	var armCode int32 = -1
	var armErr error
	for attempt := 1; attempt <= armAttempts; attempt++ {
		armCode, armErr = step(func(c context.Context) (int32, error) {
			return d.Arm(c, false)
		})
		if armErr == nil && armCode == 0 {
			break
		}
		if cctx.Err() != nil {
			break // หมดงบเวลาแล้ว/ถูก preempt — เลิก retry
		}
		if !sleepCtx(cctx, 2*time.Second) {
			break // operator emergency/takeover preempted the sequence
		}
	}
	if armErr != nil || armCode != 0 {
		reason := fmt.Sprintf("arm rejected by FC after %d attempts (pre-arm not ready)", armAttempts)
		if armErr != nil {
			reason = "arm failed: " + armErr.Error()
		}
		return res(id, "Takeoff", false, armCode, reason)
	}
	if !sleepCtx(cctx, 500*time.Millisecond) {
		return res(id, "Takeoff", false, 0, "takeoff preempted by operator/takeover after arm")
	}
	code, err := step(func(c context.Context) (int32, error) {
		return d.Takeoff(c, alt)
	})
	return s.ackResult(id, "Takeoff", code, err)
}

func (s *Service) Land(ctx context.Context, id uint32) *pb.CommandResult {
	d, rej := s.drone(id, "Land")
	if rej != nil {
		return rej
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.LandNow(cctx)
	return s.ackResult(id, "Land", code, err)
}

// Servo สั่งกลไกปล่อยของ A=ch7 / B=ch8 ผ่าน RC_CHANNELS_OVERRIDE
//
// ใช้ override แทน DO_SET_SERVO เพื่อให้ทำงานร่วมกับ SERVOn_FUNCTION = RCINn
// (RC passthrough) ได้ — สั่งได้ทั้งจาก cockpit และรีโมท (ดู docs/SERVO_DATALINK.md §2)
// core จะส่งซ้ำให้เองเรื่อย ๆ เพราะ FC ปล่อย override ทิ้งราว 3 วิ
func (s *Service) Servo(ctx context.Context, id, channel, pwm uint32) *pb.CommandResult {
	d, rej := s.drone(id, "Servo")
	if rej != nil {
		return rej
	}
	if channel == 0 {
		channel = fleet.ServoChanB
	}
	err := d.SetRCOverride(uint8(channel), uint16(fleet.ClampServoPWM(pwm)))
	return s.ackResult(id, "Servo", 0, err)
}

// ServoOff เลิก override ช่องนั้น = คืนช่องให้สวิตช์จริงบนรีโมทคุมต่อ
func (s *Service) ServoOff(id, channel uint32) *pb.CommandResult {
	d, rej := s.drone(id, "ServoOff")
	if rej != nil {
		return rej
	}
	if channel == 0 {
		channel = fleet.ServoChanB
	}
	err := d.ClearRCOverride(uint8(channel))
	return s.ackResult(id, "ServoOff", 0, err)
}

// ServoRelease ยกเลิก override ทุกช่อง (คืนการควบคุมให้รีโมททั้งหมด)
func (s *Service) ServoRelease(id uint32) *pb.CommandResult {
	d, rej := s.drone(id, "ServoRelease")
	if rej != nil {
		return rej
	}
	return s.ackResult(id, "ServoRelease", 0, d.ClearAllRCOverride())
}

func (s *Service) RTL(ctx context.Context, id uint32) *pb.CommandResult {
	d, rej := s.drone(id, "RTL")
	if rej != nil {
		return rej
	}
	cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
	defer cancel()
	code, err := d.ReturnHome(cctx)
	return s.ackResult(id, "RTL", code, err)
}

// MissionReturnRTL is the normal post-mission Return command. Unlike an
// explicit operator RTL, it must yield when fleet/FC failsafe already owns the
// safety flight action, preventing two automatic authorities from competing.
func (s *Service) MissionReturnRTL(ctx context.Context, id uint32) *pb.CommandResult {
	if s.mgr.FailsafeActive(id) {
		return s.reject(id, "MissionReturnRTL", "failsafe active — fleet/FC owns Return action")
	}
	// The early precheck rejects the common case, but a failsafe can still latch
	// between it and conn.Send. Compose the fleet failsafe final-write boundary with
	// the Return lease/cancellation guard already on ctx, so an automatic Return RTL
	// that lost the race can never reach the FC after fleet/FC acquired failsafe
	// ownership. fleet.sendCmd keeps the COMMAND_ACK wait outside fsMu. This is the
	// automatic-Return policy only: explicit operator RTL (s.RTL called directly)
	// stays unguarded here by design.
	ctx = s.mgr.WithFailsafeSendGuard(ctx, id)
	return s.RTL(ctx, id)
}

const maxManualYawRateDeg = 90.0
const manualProjectionSec = 2.0

// RcMove เป็นเส้นทาง manual control ที่ยังต้องผ่าน armed/mode/speed gate และ audit
func (s *Service) RcMove(ctx context.Context, id uint32, dir pb.RcDirection, speed, yawRateDeg float64) *pb.CommandResult {
	d, rej := s.drone(id, "RcMove")
	if rej != nil {
		return rej
	}
	// STOP ต้องผ่านได้แม้ telemetry เก่า/โหมดเปลี่ยน/ไม่ได้ armed เพื่อให้ปล่อยปุ่ม
	// manual แล้วส่ง zero velocity + hold ได้เสมอ.
	if dir == pb.RcDirection_RC_DIR_STOP {
		return s.Stop(ctx, id)
	}
	st := d.SafetyState()
	if !st.Armed {
		return s.reject(id, "RcMove", "not armed")
	}
	if st.Mode != "GUIDED" {
		return s.reject(id, "RcMove", "manual movement requires GUIDED mode")
	}
	if dec := s.env.CheckManualState(st); !dec.Allow {
		_ = d.MoveVelocityContext(ctx, 0, 0, 0, 0)
		return s.reject(id, "RcMove", dec.Reason)
	}

	var vx, vy, vz, yr float64
	switch dir {
	case pb.RcDirection_RC_DIR_FWD, pb.RcDirection_RC_DIR_BWD,
		pb.RcDirection_RC_DIR_LEFT, pb.RcDirection_RC_DIR_RIGHT,
		pb.RcDirection_RC_DIR_UP, pb.RcDirection_RC_DIR_DOWN:
		if dec := s.env.CheckSpeed(speed); !dec.Allow {
			return s.reject(id, "RcMove", dec.Reason)
		}
		switch dir {
		case pb.RcDirection_RC_DIR_FWD:
			vx = speed
		case pb.RcDirection_RC_DIR_BWD:
			vx = -speed
		case pb.RcDirection_RC_DIR_RIGHT:
			vy = speed
		case pb.RcDirection_RC_DIR_LEFT:
			vy = -speed
		case pb.RcDirection_RC_DIR_UP:
			vz = -speed
		case pb.RcDirection_RC_DIR_DOWN:
			vz = speed
		}
	case pb.RcDirection_RC_DIR_YAW_L, pb.RcDirection_RC_DIR_YAW_R:
		if math.IsNaN(yawRateDeg) || math.IsInf(yawRateDeg, 0) || yawRateDeg <= 0 || yawRateDeg > maxManualYawRateDeg {
			return s.reject(id, "RcMove", fmt.Sprintf("yaw rate must be > 0 and <= %.0f deg/s", maxManualYawRateDeg))
		}
		yr = yawRateDeg * math.Pi / 180
		if dir == pb.RcDirection_RC_DIR_YAW_L {
			yr = -yr
		}
	default:
		return s.reject(id, "RcMove", "unknown direction")
	}

	// ฉายตำแหน่งไปข้างหน้าในกรอบโลกก่อนส่ง velocity จริง แล้วใช้ด่านเดียวกับ Goto
	// (radius, geofence, altitude และ separation) เพื่อไม่ให้ปุ่ม manual ข้าม envelope.
	if vx != 0 || vy != 0 || vz != 0 {
		h := st.Heading * math.Pi / 180
		north := (vx*math.Cos(h) - vy*math.Sin(h)) * manualProjectionSec
		east := (vx*math.Sin(h) + vy*math.Cos(h)) * manualProjectionSec
		tLat, tLon := geo.OffsetM(st.Lat, st.Lon, north, east)
		tAlt := st.AltRel - vz*manualProjectionSec
		if dec := s.env.CheckGoto(st, tLat, tLon, tAlt, s.mgr.OtherSafetyStates(id)); !dec.Allow {
			_ = d.MoveVelocity(0, 0, 0, 0)
			return s.reject(id, "RcMove", "projected movement unsafe: "+dec.Reason)
		}
	}

	if err := d.MoveVelocityContext(ctx, vx, vy, vz, yr); err != nil {
		s.audit.Command(id, "RcMove", true, "send error: "+err.Error(), -1)
		return res(id, "RcMove", false, -1, err.Error())
	}
	s.audit.Command(id, "RcMove", true, "sent", 0)
	r := res(id, "RcMove", true, 0, "sent")
	r.Outcome = pb.CommandOutcome_OUTCOME_SENT
	return r
}

// Stop หยุดทันทีด้วย zero velocity แล้วเปลี่ยนเข้า GUIDED hold เพื่อให้คำสั่ง
// อัตโนมัติเดิมไม่ลากโดรนเคลื่อนต่อ
func (s *Service) Stop(ctx context.Context, id uint32) *pb.CommandResult {
	d, rej := s.drone(id, "StopAll")
	if rej != nil {
		return rej
	}
	if err := d.MoveVelocityContext(ctx, 0, 0, 0, 0); err != nil {
		s.audit.Command(id, "StopAll", true, "zero velocity send error: "+err.Error(), -1)
		return res(id, "StopAll", false, -1, err.Error())
	}
	r := s.Hold(ctx, id)
	if r == nil {
		return res(id, "StopAll", false, -1, "hold returned no result")
	}
	r.Command = "StopAll"
	s.audit.Command(id, "StopAll", true, "zero velocity + "+r.Message, r.ResultCode)
	return r
}

// holdMode = โหมดที่ Hold พาโดรนเข้าไปอยู่
//
// ห้ามเปลี่ยนเป็น LOITER / ALT_HOLD / POSHOLD เด็ดขาด — พวกนั้นเอา climb rate
// จากสติ๊กคันเร่งของรีโมท จะทำให้โดรน "ตก" ทันทีที่สลับโหมดกลางอากาศ
// (ดูคอมเมนต์เต็มที่ Hold และ pilotThrottleModes ใน hold_test.go)
var (
	holdMode     = pb.FlightMode_FLIGHT_MODE_GUIDED
	holdModeName = "GUIDED"
)

// minHoldAltM = ความสูงต่ำสุดที่ยอมเอา AltRel ไปตั้งเป็น "เป้าหมาย" ของ Hold
//
// Goto ส่ง SET_POSITION_TARGET_GLOBAL_INT เฟรม GLOBAL_RELATIVE_ALT — ค่า alt
// ที่ส่งไปคือ "ความสูงเป้าหมายที่ให้บินไปหา" ไม่ใช่ "คงไว้เท่าเดิม"
// ดังนั้นถ้า AltRel อ่านได้ 0 หรือใกล้ 0 (GLOBAL_POSITION_INT ยังไม่มา,
// telemetry ค้าง, หรือเพิ่งต่อเข้ามา) Hold จะกลายเป็นคำสั่ง "ลงไปที่พื้น"
// = โดรนร่วงลงทั้งที่ผู้ใช้แค่กด Cancel Nav
//
// ต่ำกว่านี้ = ความสูงไม่น่าเชื่อถือพอจะเอาไปเป็นเป้า → ใช้ zero-velocity hold
// แทน ซึ่งใน GUIDED ตัวคุมคงความสูงปัจจุบันให้เองโดยไม่ต้องระบุตัวเลข
const minHoldAltM = 1.0

// Hold = หยุดลอยอยู่กับที่ (ใช้จาก Cancel Nav / ยกเลิก waypoint / Quick Action HOLD)
//
// เดิมสั่ง LOITER แล้ว "โดรนตก" ทันที — เพราะ LOITER (รวมถึง ALT_HOLD / POSHOLD)
// เอา climb rate จาก "สติ๊กคันเร่งของรีโมท" ไม่ใช่จากตัวคุมอัตโนมัติ
// ระหว่างบินอัตโนมัติสติ๊กคันเร่งอยู่ต่ำสุดเสมอ (หรือไม่มี RC เลยอย่างใน SITL)
// FC จึงอ่านว่า "นักบินสั่งลง" แล้วร่วงลงด้วยอัตราสูงสุดทันทีที่สลับโหมด
//
// ตอนนี้: อยู่ GUIDED แล้วตรึงพิกัดปัจจุบันแทน
//   - GUIDED ไม่สนสติ๊กคันเร่ง → ค้างความสูงเดิมจริง
//   - เป็นโหมดเดียวกับที่คำสั่งอื่นทั้งหมดใช้ → สั่งบินต่อได้ทันที ไม่ต้องสลับโหมดอีก
//     (ถ้าใช้ BRAKE จะค้างได้เหมือนกัน แต่ Goto รอบถัดไปจะถูกเมินจนกว่าจะกลับ GUIDED)
func (s *Service) Hold(ctx context.Context, id uint32) *pb.CommandResult {
	d, rej := s.drone(id, "Hold")
	if rej != nil {
		return rej
	}
	// Battery-critical / link-lost failsafe already owns the aircraft (RTL).
	// WAIT/Cancel/Quick-HOLD callers must never switch mode or send a position
	// hold after the failsafe latch has won the race.
	if s.mgr.FailsafeActive(id) {
		return s.reject(id, "Hold", "failsafe active — refuse HOLD")
	}
	// Preemption guard: a Core mission HOLD preempted by an operator takeover must
	// not switch mode / write a position hold after the takeover has won.
	if ctx != nil && ctx.Err() != nil {
		return s.reject(id, "Hold", "preempted — command cancelled before send")
	}
	// Compose the fleet failsafe latch with any existing mission/operator
	// final-write guard. Every SetMode/velocity/position transport write below
	// re-enters this boundary independently, while SetMode's ACK wait remains
	// outside fsMu inside fleet.sendCmd.
	ctx = s.mgr.WithFailsafeSendGuard(ctx, id)
	st := d.SafetyState()
	if !st.Armed {
		// อยู่บนพื้น/ยังไม่ armed — ไม่มีอะไรให้ค้าง และห้ามไปยุ่งกับโหมด
		s.audit.Command(id, "Hold", true, "not armed — no-op", 0)
		return res(id, "Hold", true, 0, "not armed")
	}

	// 1) ตัดโหมดอัตโนมัติอื่น (RTL / AUTO / LOITER …) ออกก่อน ด้วยการเข้า holdMode
	if st.Mode != holdModeName {
		mode, _ := fleet.ModeNumber(holdMode)
		cctx, cancel := context.WithTimeout(ctx, cmdTimeout)
		code, err := d.SetMode(cctx, mode)
		cancel()
		if err != nil || code != 0 {
			return s.ackResult(id, "Hold", code, err)
		}
	}

	// 2) ความเร็ว 0 = ค้างอยู่กับที่โดยไม่ต้องระบุความสูง ใช้เมื่อ:
	//      - ไม่มีพิกัด → ตรึงตำแหน่งไม่ได้ อย่างน้อยกันไหลต่อ
	//      - ความสูงต่ำ/ไม่น่าเชื่อถือ → ตรึงแล้วจะกลายเป็นสั่งลดระดับ (ดู minHoldAltM)
	noFix := st.Lat == 0 && st.Lon == 0
	if noFix || st.AltRel < minHoldAltM {
		if err := d.MoveVelocityContext(ctx, 0, 0, 0, 0); err != nil {
			s.audit.Command(id, "Hold", true, "send error: "+err.Error(), -1)
			return res(id, "Hold", false, -1, err.Error())
		}
		reason := "no GPS — zero velocity hold"
		msg := "hold (no GPS: zero velocity)"
		if !noFix {
			reason = fmt.Sprintf("alt %.1fm < %.1fm — zero velocity hold (ไม่สั่งลดระดับ)",
				st.AltRel, minHoldAltM)
			msg = "holding position (zero velocity)"
		}
		s.audit.Command(id, "Hold", true, reason, 0)
		r := res(id, "Hold", true, 0, msg)
		r.Outcome = pb.CommandOutcome_OUTCOME_SENT
		return r
	}

	// 3) ตรึงพิกัด+ความสูงปัจจุบัน
	if err := d.GotoContext(ctx, st.Lat, st.Lon, st.AltRel); err != nil {
		s.audit.Command(id, "Hold", true, "send error: "+err.Error(), -1)
		return res(id, "Hold", false, -1, err.Error())
	}
	s.audit.Command(id, "Hold", true, "holding current position (GUIDED)", 0)
	r := res(id, "Hold", true, 0, "holding position")
	r.Outcome = pb.CommandOutcome_OUTCOME_SENT
	return r
}

// Goto: safety.CheckGoto (armed + alt + radius + geofence + separation) → SET_POSITION_TARGET
func (s *Service) Goto(ctx context.Context, id uint32, lat, lon, alt float64) *pb.CommandResult {
	d, rej := s.drone(id, "Goto")
	if rej != nil {
		return rej
	}
	// Battery-critical / link-lost failsafe already owns the aircraft (RTL).
	// Reject every later GOTO source centrally so mission/manual callers cannot
	// race the event observer and overwrite that higher-priority safety action.
	if s.mgr.FailsafeActive(id) {
		return s.reject(id, "Goto", "failsafe active — refuse GOTO")
	}
	others := s.mgr.OtherSafetyStates(id)
	if dec := s.env.CheckGoto(d.SafetyState(), lat, lon, alt, others); !dec.Allow {
		return s.reject(id, "Goto", dec.Reason)
	}
	// Preemption guard: if an operator emergency/takeover cancelled this send's
	// context (e.g. a Core mission GOTO preempted by a takeover), refuse before
	// writing to the FC so a stale/superseded GOTO can never reach the aircraft.
	if ctx != nil && ctx.Err() != nil {
		return s.reject(id, "Goto", "preempted — command cancelled before send")
	}
	// The earlier FailsafeActive precheck rejects the common case; this context
	// guard closes the TOCTOU window between that check and the actual MAVLink
	// transport write while preserving any mission/operator cancellation guard.
	ctx = s.mgr.WithFailsafeSendGuard(ctx, id)
	if err := d.GotoContext(ctx, lat, lon, alt); err != nil {
		s.audit.Command(id, "Goto", true, "send error: "+err.Error(), -1)
		return res(id, "Goto", false, -1, err.Error())
	}
	s.audit.Command(id, "Goto", true, "sent", 0)
	r := res(id, "Goto", true, 0, "sent")
	r.Outcome = pb.CommandOutcome_OUTCOME_SENT
	return r
}

// ChangeAlt: บินไปความสูงใหม่ ณ ตำแหน่งปัจจุบัน (กำหนดความสูงเองระหว่างบิน)
func (s *Service) ChangeAlt(ctx context.Context, id uint32, alt float64) *pb.CommandResult {
	d, rej := s.drone(id, "ChangeAlt")
	if rej != nil {
		return rej
	}
	if dec := s.env.CheckAltitude(alt); !dec.Allow {
		return s.reject(id, "ChangeAlt", dec.Reason)
	}
	st := d.SafetyState()
	if !st.Armed {
		return s.reject(id, "ChangeAlt", "not armed")
	}
	if st.Lat == 0 && st.Lon == 0 {
		return s.reject(id, "ChangeAlt", "no position")
	}
	if err := d.GotoContext(ctx, st.Lat, st.Lon, alt); err != nil {
		return res(id, "ChangeAlt", false, -1, err.Error())
	}
	msg := fmt.Sprintf("alt -> %.0fm", alt)
	s.audit.Command(id, "ChangeAlt", true, msg, 0)
	return res(id, "ChangeAlt", true, 0, msg)
}

// SetParam ตั้งพารามิเตอร์ที่ FC — ผ่าน audit เหมือนคำสั่งอื่นทุกประการ
//
// ปฏิเสธขณะ armed: การแก้พารามิเตอร์กลางอากาศเปลี่ยนพฤติกรรมการบินได้ทันที
// (เช่น แก้ลิมิตความสูง/แบต) ซึ่งอันตรายและไม่มีเหตุผลที่ต้องทำระหว่างบิน
func (s *Service) SetParam(id uint32, paramID string, value float64) *pb.CommandResult {
	d, rej := s.drone(id, "ParamSet")
	if rej != nil {
		return rej
	}
	if paramID == "" || len(paramID) > 16 {
		return s.reject(id, "ParamSet",
			fmt.Sprintf("param id ต้องยาว 1-16 ตัวอักษร (ได้ %q)", paramID))
	}
	if d.SafetyState().Armed {
		return s.reject(id, "ParamSet", "armed อยู่ — ห้ามแก้พารามิเตอร์กลางอากาศ")
	}
	if err := d.SetParam(paramID, value); err != nil {
		s.audit.Command(id, "ParamSet", true, "send error: "+err.Error(), -1)
		return res(id, "ParamSet", false, -1, err.Error())
	}
	msg := fmt.Sprintf("%s = %g", paramID, value)
	s.audit.Command(id, "ParamSet", true, msg, 0)
	r := res(id, "ParamSet", true, 0, msg)
	r.Outcome = pb.CommandOutcome_OUTCOME_SENT
	return r
}

// SetGeofence ตั้ง polygon ที่ envelope (บังคับใช้ทันทีทุก goto/swarm)
func (s *Service) SetGeofence(poly [][2]float64, confirmed bool) *pb.CommandResult {
	if dec := s.env.CheckDangerous("SetGeofence", confirmed); !dec.Allow {
		return s.reject(0, "SetGeofence", dec.Reason)
	}
	if err := s.env.SetGeofence(poly); err != nil {
		return s.reject(0, "SetGeofence", err.Error())
	}
	s.audit.Event("geofence", fmt.Sprintf("set %d vertices", len(poly)))
	message := fmt.Sprintf("geofence set (%d vertices)", len(poly))
	if len(poly) == 0 {
		message = "geofence cleared"
	}
	return res(0, "SetGeofence", true, 0, message)
}

func itoa(n int32) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
