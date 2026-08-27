// Package swarm — leader-follower formation + auto leader-failover
// หลักการ:
//   - "ตัวแม่" (leader) = drone online ตัวแรกตาม priority (id น้อยสุด) — sticky
//   - ตัวลูก (follower) บินเกาะ offset slot รอบตัวแม่ (หมุนตาม heading)
//   - ตัวแม่หาย (telemetry ขาด > LinkLostSec) → เลื่อนตัวถัดไปเป็นแม่อัตโนมัติ
//   - ทุกจุดเป้าผ่าน safety.CheckPoint (alt+radius+geofence); spacing >= MinSeparation
package swarm

import (
	"context"
	"fmt"
	"log"
	"math"
	"sync"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/safety"
	"github.com/swarmgod/backend/pkg/geo"
)

const metersPerDegLat = 111320.0
const returnBaseAltM = 15.0
const returnStageTimeoutSec = 60
const returnTravelTimeoutSec = 180
const fallbackLandingTimeoutSec = 90
const formUpPhaseTimeoutSec = 60

type Manager struct {
	cfg    config.Config
	fleet  *fleet.Manager
	env    *safety.Envelope
	audit  *audit.Logger
	events *events.Bus

	mu          sync.RWMutex
	active      bool
	halted      bool // leader failsafe → formation หยุดแบบ fail-closed (ไม่ auto-resume)
	spacing     float64
	headingMode pb.HeadingMode
	formation   pb.Formation
	leaderID    uint32 // ตัวแม่ปัจจุบัน (sticky)
	pinnedID    uint32 // ตัวแม่ที่ "ผู้ใช้เลือกเอง" — ชนะ auto pick เสมอถ้ายัง online
	note        string

	cancel   context.CancelFunc
	done     chan struct{}
	stopping bool
	stopDone chan struct{}

	returnMu     sync.Mutex
	returnCancel context.CancelFunc
	returnDone   chan struct{}
}

func NewManager(cfg config.Config, fl *fleet.Manager, env *safety.Envelope, aud *audit.Logger, bus *events.Bus) *Manager {
	sp := cfg.MinSeparationM * 2
	if sp < cfg.MinSeparationM {
		sp = cfg.MinSeparationM
	}
	return &Manager{
		cfg: cfg, fleet: fl, env: env, audit: aud, events: bus,
		spacing: sp, headingMode: pb.HeadingMode_HEADING_MODE_HEAD_TO_DIR,
	}
}

// SetConfig ตั้ง spacing/heading/formation + ตรวจระยะห่างจริงทุกคู่
// count = จำนวนโดรนที่จะจัดขบวน (ตรวจว่าไม่มีคู่ไหนใกล้กว่า MinSeparation)
func (m *Manager) SetConfig(spacing float64, heading pb.HeadingMode, formation pb.Formation, count int) error {
	minSep := m.env.MinSeparation()
	sp := spacing
	if sp <= 0 {
		m.mu.RLock()
		sp = m.spacing
		m.mu.RUnlock()
	}
	// ตรวจระยะห่างจริงของขบวน (รวมตัวแม่) — ถ้ามีคู่ใกล้เกิน → ปฏิเสธ
	if count >= 2 {
		got := minPairwiseDist(formation, count, sp)
		if got < minSep {
			return fmt.Errorf("ระยะห่างจริง %.1fm < ขั้นต่ำ %.1fm (%s, %d ลำ) — เพิ่ม spacing หรือเปลี่ยนรูปแบบ",
				got, minSep, formationName(formation), count)
		}
	} else if sp < minSep {
		return fmt.Errorf("spacing %.1fm below min separation %.1fm", sp, minSep)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if spacing > 0 {
		m.spacing = spacing
	}
	m.headingMode = heading
	m.formation = formation
	return nil
}

func formationName(f pb.Formation) string {
	switch f {
	case pb.Formation_FORMATION_LINE:
		return "LINE/หน้ากระดาน"
	case pb.Formation_FORMATION_COLUMN:
		return "COLUMN/แถวตอน"
	case pb.Formation_FORMATION_DIAMOND:
		return "DIAMOND/เพชร"
	case pb.Formation_FORMATION_ECHELON:
		return "ECHELON/ทแยง"
	default:
		return "WEDGE/ลิ่ม"
	}
}

// SetLeader ปักหมุดตัวแม่ตามที่ผู้ใช้เลือก (spec: เปลี่ยน Head ได้เอง)
// ตัวที่ปักหมุดไว้จะชนะการเลือกอัตโนมัติเสมอ ตราบใดที่ยัง online
// id = 0 → ปลดหมุด กลับไปเลือกอัตโนมัติ (id น้อยสุด)
func (m *Manager) SetLeader(id uint32) error {
	if id == 0 {
		m.mu.Lock()
		m.pinnedID = 0
		m.mu.Unlock()
		return nil
	}
	if !contains(m.fleet.OnlineIDs(m.cfg.LinkLostSec), id) {
		return fmt.Errorf("Drone %d ไม่ได้ออนไลน์ — ตั้งเป็นตัวแม่ไม่ได้", id)
	}
	m.mu.Lock()
	m.pinnedID = id
	m.leaderID = id
	m.note = fmt.Sprintf("ผู้ใช้ตั้งตัวแม่ → Drone %d", id)
	note := m.note
	m.mu.Unlock()
	m.audit.Event("swarm", note)
	log.Printf("[swarm] SET LEADER → Drone %d (pinned)", id)
	return nil
}

// pickLeader เลือกตัวแม่จากรายชื่อ online — ตัวที่ผู้ใช้ปักหมุดมาก่อนเสมอ
// (ต้องเรียกภายใต้ m.mu)
func (m *Manager) pickLeader(online []uint32) uint32 {
	if m.pinnedID != 0 && contains(online, m.pinnedID) {
		return m.pinnedID
	}
	return online[0]
}

// Start เริ่ม formation loop
func (m *Manager) Start(parent context.Context) error {
	m.mu.Lock()
	if m.active || m.stopping {
		m.mu.Unlock()
		return fmt.Errorf("swarm already active or stopping")
	}
	// ต้องมีอย่างน้อย 2 ลำ online
	online := m.fleet.OnlineIDs(m.cfg.LinkLostSec)
	if len(online) < 2 {
		m.mu.Unlock()
		return fmt.Errorf("need >= 2 online drones (have %d)", len(online))
	}
	m.active = true
	m.halted = false // เริ่มภารกิจใหม่ = ล้างสถานะ halt เดิม (ต้อง Start ใหม่เท่านั้น)
	m.leaderID = m.pickLeader(online) // เคารพตัวแม่ที่ผู้ใช้ปักหมุดไว้
	m.note = fmt.Sprintf("formation start — leader Drone %d", m.leaderID)
	ctx, cancel := context.WithCancel(parent)
	m.cancel = cancel
	m.done = make(chan struct{})
	done := m.done
	m.mu.Unlock()

	m.audit.Event("swarm", m.note)
	log.Printf("[swarm] START leader=Drone %d spacing=%.1fm", m.leaderID, m.spacing)
	go m.loop(ctx, done)
	return nil
}

// Stop หยุด formation (โดรนค้างที่เดิม — ไม่สั่งอะไรเพิ่ม)
func (m *Manager) Stop() {
	m.mu.Lock()
	if m.stopping {
		wait := m.stopDone
		m.mu.Unlock()
		if wait != nil {
			<-wait
		}
		return
	}
	m.stopping = true
	m.stopDone = make(chan struct{})
	stopDone := m.stopDone
	cancel := m.cancel
	done := m.done
	m.cancel = nil
	m.done = nil
	m.active = false
	m.halted = false
	m.note = "formation stopped"
	m.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	// รอให้ tick ที่กำลังทำงานจบจริง ก่อนคำสั่ง Hold/RTL ถัดไป
	if done != nil {
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			log.Println("[swarm] stop timeout waiting for loop")
		}
	}
	m.mu.Lock()
	m.stopping = false
	m.stopDone = nil
	close(stopDone)
	m.mu.Unlock()
	m.audit.Event("swarm", "formation stopped")
	log.Println("[swarm] STOP")
}

// CancelReturn ยกเลิก return/land sequence และรอให้ goroutine หยุดส่งคำสั่งจริง
// E-STOP ต้องเรียกฟังก์ชันนี้ก่อนสั่ง zero velocity/hold.
func (m *Manager) CancelReturn() {
	m.returnMu.Lock()
	cancel := m.returnCancel
	done := m.returnDone
	m.returnMu.Unlock()
	if cancel == nil {
		return
	}
	cancel()
	if done != nil {
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			log.Println("[swarm] return cancel timeout")
		}
	}
	m.audit.Event("swarm", "return/land cancelled")
}

func (m *Manager) loop(ctx context.Context, done chan struct{}) {
	defer close(done)
	if !m.formUpSequential(ctx) {
		m.mu.Lock()
		m.active = false
		m.note = "formation setup failed"
		m.mu.Unlock()
		return
	}
	ticker := time.NewTicker(400 * time.Millisecond) // เร็วขึ้น — ลูกเกาะแม่ทันขึ้น
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.tick()
		}
	}
}

type formUpTarget struct {
	id                   uint32
	startLat, startLon   float64
	targetLat, targetLon float64
	finalAlt, transitAlt float64
	yaw                  float64
}

// formUpSequential จัดขบวนแบบ fail-closed สามช่วง:
//  1. ยกลูกทีละลำขึ้นชั้นผ่านทางคนละชั้น
//  2. ย้ายแนวราบทีละลำ โดยทุกเส้นทางอยู่คนละความสูง
//  3. ลดลง slot ทีละลำ
//
// จึงไม่มีลูกลำใดบินตัดผ่านตัวแม่/ลูกอีกลำที่ระดับเดียวกันเหมือนการส่ง Goto
// ทุกลำพร้อมกันแบบเดิม. ถ้าเพดานไม่พอหรือ telemetry ไม่ยืนยัน จะหยุดและไม่เปิด loop.
func (m *Manager) formUpSequential(ctx context.Context) bool {
	online := m.fleet.OnlineIDs(m.cfg.LinkLostSec)
	if len(online) < 2 {
		return false
	}
	m.mu.RLock()
	leaderID, spacing, headMode, formation := m.leaderID, m.spacing, m.headingMode, m.formation
	m.mu.RUnlock()
	leader := m.fleet.Drone(leaderID)
	if leader == nil {
		return false
	}
	lLat, lLon, lAlt, lHdg := leader.Nav()
	if lLat == 0 && lLon == 0 {
		m.formUpFailed(0, "ตัวแม่ไม่มีพิกัดที่ยืนยันแล้ว")
		return false
	}

	// ก่อนขยับ ต้องรู้พิกัดทุกลำและทุกคู่ต้องห่างกันอยู่แล้ว.
	type currentPos struct {
		id            uint32
		lat, lon, alt float64
	}
	current := make([]currentPos, 0, len(online))
	maxAlt := lAlt
	for _, id := range online {
		d := m.fleet.Drone(id)
		if d == nil {
			m.formUpFailed(id, "โดรนหายก่อนเริ่มจัดขบวน")
			return false
		}
		lat, lon, alt, _ := d.Nav()
		if lat == 0 && lon == 0 {
			m.formUpFailed(id, "ไม่มีพิกัดที่ยืนยันแล้ว")
			return false
		}
		current = append(current, currentPos{id, lat, lon, alt})
		maxAlt = math.Max(maxAlt, alt)
	}
	minSep := m.env.MinSeparation()
	for i, a := range current {
		for _, b := range current[i+1:] {
			if dist := geo.HaversineM(a.lat, a.lon, b.lat, b.lon); dist < minSep {
				m.formUpFailed(0, fmt.Sprintf("D%d/D%d อยู่ห่างเพียง %.1fm (< %.1fm)",
					a.id, b.id, dist, minSep))
				return false
			}
		}
	}

	// เผื่อ telemetry/การคุมความสูงคลาดเคลื่อนระหว่าง transition 2m;
	// ระยะสามมิติจริงจึงยังไม่ต่ำกว่า min separation ตอนเส้นแนวราบตัดกัน.
	gap := math.Max(4.0, minSep+2.0)
	targets := make([]formUpTarget, 0, len(online)-1)
	fi := 0
	for _, p := range current {
		if p.id == leaderID {
			continue
		}
		n, e, up := slotOffset(formation, fi, spacing)
		if headMode == pb.HeadingMode_HEADING_MODE_HEAD_TO_DIR {
			n, e = rotate(n, e, lHdg)
		}
		t := formUpTarget{
			id: p.id, startLat: p.lat, startLon: p.lon,
			targetLat: lLat + n/metersPerDegLat,
			targetLon: lLon + e/(metersPerDegLat*math.Cos(lLat*math.Pi/180)),
			finalAlt:  lAlt + up, transitAlt: formUpTransitAltitude(maxAlt, fi, gap), yaw: lHdg,
		}
		if dec := m.env.CheckPoint(t.startLat, t.startLon, t.transitAlt); !dec.Allow {
			m.formUpFailed(t.id, "ชั้นผ่านทางไม่ปลอดภัย: "+dec.Reason)
			return false
		}
		if dec := m.env.CheckPoint(t.targetLat, t.targetLon, t.transitAlt); !dec.Allow {
			m.formUpFailed(t.id, "เส้นทางผ่านไม่ปลอดภัย: "+dec.Reason)
			return false
		}
		if dec := m.env.CheckPoint(t.targetLat, t.targetLon, t.finalAlt); !dec.Allow {
			m.formUpFailed(t.id, "slot ไม่ปลอดภัย: "+dec.Reason)
			return false
		}
		targets = append(targets, t)
		fi++
	}

	// ตรึงตัวแม่ไว้ก่อนเริ่ม transition.
	if err := leader.Goto(lLat, lLon, lAlt); err != nil {
		m.formUpFailed(leaderID, "ตรึงตัวแม่ไม่สำเร็จ: "+err.Error())
		return false
	}
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_INFO, 0, "swarm",
		fmt.Sprintf("FORM UP แบบกันชน: %d ลำ, ย้ายทีละลำผ่านชั้นสูง", len(online)))

	for _, t := range targets {
		d := m.fleet.Drone(t.id)
		if d == nil || d.GotoYaw(t.startLat, t.startLon, t.transitAlt, t.yaw) != nil ||
			!m.waitFormUpTarget(ctx, t.id, t.startLat, t.startLon, t.transitAlt) {
			m.formUpFailed(t.id, "ขึ้นชั้นผ่านทางไม่สำเร็จ/หมดเวลา")
			return false
		}
	}
	for _, t := range targets {
		d := m.fleet.Drone(t.id)
		if d == nil || d.GotoYaw(t.targetLat, t.targetLon, t.transitAlt, t.yaw) != nil ||
			!m.waitFormUpTarget(ctx, t.id, t.targetLat, t.targetLon, t.transitAlt) {
			m.formUpFailed(t.id, "ย้ายแนวราบไม่สำเร็จ/หมดเวลา")
			return false
		}
	}
	for _, t := range targets {
		d := m.fleet.Drone(t.id)
		if d == nil || d.GotoYaw(t.targetLat, t.targetLon, t.finalAlt, t.yaw) != nil ||
			!m.waitFormUpTarget(ctx, t.id, t.targetLat, t.targetLon, t.finalAlt) {
			m.formUpFailed(t.id, "ลง slot ไม่สำเร็จ/หมดเวลา")
			return false
		}
	}
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, 0, "swarm", "FORM UP ปลอดภัยเสร็จแล้ว")
	return true
}

func formUpTransitAltitude(maxCurrentAlt float64, followerIndex int, gap float64) float64 {
	return maxCurrentAlt + float64(followerIndex+1)*math.Max(2.0, gap)
}

func (m *Manager) waitFormUpTarget(ctx context.Context, id uint32, lat, lon, alt float64) bool {
	deadline := time.NewTimer(formUpPhaseTimeoutSec * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-deadline.C:
			return false
		case <-ticker.C:
			d := m.fleet.Drone(id)
			if d == nil {
				return false
			}
			gotLat, gotLon, gotAlt, _ := d.Nav()
			if geo.HaversineM(gotLat, gotLon, lat, lon) <= 1.5 && math.Abs(gotAlt-alt) <= 1.0 {
				return true
			}
		}
	}
}

func (m *Manager) formUpFailed(id uint32, reason string) {
	m.audit.Event("swarm", "formation setup failed: "+reason)
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "swarm", "FORM UP หยุด: "+reason)
	log.Printf("[swarm] FORM UP failed D%d: %s", id, reason)
}

// haltFormation หยุด formation แบบ fail-closed เมื่อตัวแม่ถูก failsafe (§9)
//
// latch ไว้: แม้ failsafe ของตัวแม่หายไป loop ก็ไม่กลับมาเดินเอง — ผู้ใช้ต้อง
// Start ใหม่ (mission ไม่ auto-resume). ยิง ALARM ครั้งเดียวตอน latch.
func (m *Manager) haltFormation(reason string) {
	m.mu.Lock()
	if m.halted {
		m.mu.Unlock()
		return
	}
	m.halted = true
	m.note = reason
	leaderID := m.leaderID
	m.mu.Unlock()
	m.audit.Event("swarm", reason)
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, leaderID, "swarm", reason)
	log.Printf("[swarm] HALT (fail-closed): %s", reason)
}

type followerCmd struct {
	id            uint32
	lat, lon, alt float64
	yaw           float64
}

// planFormationTargets คำนวณเป้าหมายของ follower แต่ละลำจากตำแหน่งตัวแม่
//
// ลำที่ failsafe(id)==true (Core กำลัง failsafe RTL) จะถูกข้าม — ไม่ส่ง target ทับ
// RTL ของ Core (§9). slot index อิงลำดับ follower เดิม ลำที่ข้ามยังกิน slot ของ
// ตัวเองไว้ (ลำอื่นไม่เลื่อนเข้ามาแทน) เพื่อคงรูปขบวนของลำที่เหลือ. env-check ทำต่อ
// ที่ผู้เรียก (คงพฤติกรรม/log เดิม).
func planFormationTargets(online []uint32, leaderID uint32,
	lLat, lLon, lAlt, lHdg, spacing float64,
	formation pb.Formation, headMode pb.HeadingMode,
	failsafe func(uint32) bool) []followerCmd {
	out := make([]followerCmd, 0, len(online))
	fi := 0
	for _, fid := range online {
		if fid == leaderID {
			continue
		}
		n, e, up := slotOffset(formation, fi, spacing)
		if headMode == pb.HeadingMode_HEADING_MODE_HEAD_TO_DIR {
			n, e = rotate(n, e, lHdg)
		}
		tLat := lLat + n/metersPerDegLat
		tLon := lLon + e/(metersPerDegLat*math.Cos(lLat*math.Pi/180))
		tAlt := lAlt + up
		fi++
		if failsafe != nil && failsafe(fid) {
			continue // §9: Core กำลัง failsafe RTL ลำนี้ — ห้ามส่ง target ทับ
		}
		out = append(out, followerCmd{fid, tLat, tLon, tAlt, lHdg})
	}
	return out
}

func (m *Manager) tick() {
	m.mu.RLock()
	halted := m.halted
	m.mu.RUnlock()
	if halted {
		return // fail-closed latch — ไม่ auto-resume จนกว่าจะ Start ใหม่
	}
	online := m.fleet.OnlineIDs(m.cfg.LinkLostSec)
	if len(online) == 0 {
		return
	}
	onlineSet := map[uint32]bool{}
	for _, id := range online {
		onlineSet[id] = true
	}

	m.mu.Lock()
	// ── FAILOVER: ตัวแม่ยัง online ไหม? ถ้าไม่ → เลื่อนตัวถัดไป (sticky) ──
	if !onlineSet[m.leaderID] {
		old := m.leaderID
		// ตัวแม่หาย → ถ้าตัวที่ผู้ใช้ปักหมุดยัง online ใช้ตัวนั้น ไม่งั้นเลื่อนตัวถัดไป
		m.leaderID = m.pickLeader(online)
		m.note = fmt.Sprintf("leader Drone %d lost -> promoted Drone %d", old, m.leaderID)
		m.mu.Unlock()
		m.audit.Event("swarm", m.note)
		m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, m.leaderID, "swarm", m.note)
		log.Printf("[swarm] FAILOVER: %s", m.note)
		m.mu.Lock()
	}
	leaderID := m.leaderID
	spacing := m.spacing
	headMode := m.headingMode
	formation := m.formation
	m.mu.Unlock()

	// ตัวแม่ถูก failsafe (battery/link) → Core กำลัง RTL ตัวแม่อยู่
	// formation ทั้งขบวนต้องหยุดแบบ fail-closed ไม่ลากลูกตามตัวแม่ที่กำลังกลับฐาน (§9)
	if m.fleet.FailsafeActive(leaderID) {
		m.haltFormation(fmt.Sprintf(
			"leader Drone %d failsafe — formation halted (fail-closed)", leaderID))
		return
	}

	leader := m.fleet.Drone(leaderID)
	if leader == nil {
		return
	}
	lLat, lLon, lAlt, lHdg := leader.Nav()
	if lLat == 0 && lLon == 0 {
		return // ตัวแม่ยังไม่มี GPS
	}

	// followers = online ทั้งหมด ยกเว้นตัวแม่ (เรียงลำดับ → slot คงที่)
	// ลำที่ Core กำลัง failsafe RTL จะถูกตัดออกจาก planFormationTargets ตั้งแต่ต้น
	for _, c := range planFormationTargets(online, leaderID, lLat, lLon, lAlt, lHdg,
		spacing, formation, headMode, m.fleet.FailsafeActive) {
		fdrone := m.fleet.Drone(c.id)
		if fdrone == nil {
			continue
		}
		if dec := m.env.CheckPoint(c.lat, c.lon, c.alt); !dec.Allow {
			log.Printf("[swarm] Drone %d target unsafe: %s", c.id, dec.Reason)
			continue
		}
		// ลูกหันหน้าตามแม่เสมอ (yaw = heading ของตัวแม่)
		if err := fdrone.GotoYaw(c.lat, c.lon, c.alt, c.yaw); err != nil {
			log.Printf("[swarm] Drone %d goto err: %v", c.id, err)
		}
	}
}

// rotate หมุนเวกเตอร์ (N,E) ตาม heading (deg) ของตัวแม่
func rotate(n, e, hdgDeg float64) (float64, float64) {
	r := hdgDeg * math.Pi / 180
	return n*math.Cos(r) - e*math.Sin(r), n*math.Sin(r) + e*math.Cos(r)
}

// ReturnAndLand — กลับจุดปล่อย + ลงจอดแบบกันชน:
//  1. แยกชั้นแนวดิ่งและรอยืนยันก่อนเริ่มเคลื่อนแนวราบ
//  2. แต่ละลำบินกลับ launch point ของตัวเอง (fallback รอบ GCS เฉพาะลำที่ไม่มีข้อมูล)
//  3. ลงจอดทีละลำ ตัวแม่ก่อน แล้วลูกๆ ตามลำดับ
func (m *Manager) ReturnAndLand(parent context.Context, requested []uint32, requestedBaseAlt, requestedGap float64) error {
	gLat, gLon := m.env.GCS()
	if gLat == 0 && gLon == 0 {
		return fmt.Errorf("ยังไม่ได้ตั้งตำแหน่ง home/GCS")
	}
	online := m.fleet.OnlineIDs(m.cfg.LinkLostSec)
	var err error
	online, err = selectReturnDrones(online, requested)
	if err != nil {
		return err
	}
	if len(online) == 0 {
		return fmt.Errorf("ไม่มีโดรน online")
	}
	// ลำดับ: ตัวแม่ก่อน แล้วที่เหลือ
	m.mu.RLock()
	leader := m.leaderID
	m.mu.RUnlock()
	order := []uint32{}
	if contains(online, leader) {
		order = append(order, leader)
	}
	for _, id := range online {
		if id != leader {
			order = append(order, id)
		}
	}

	if math.IsNaN(requestedBaseAlt) || math.IsInf(requestedBaseAlt, 0) ||
		math.IsNaN(requestedGap) || math.IsInf(requestedGap, 0) {
		return fmt.Errorf("return base altitude/gap must be finite")
	}
	baseAlt := returnBaseAltM
	if requestedBaseAlt > baseAlt {
		baseAlt = requestedBaseAlt
	}
	gap := returnLayerGap(math.Max(m.env.MinSeparation(), requestedGap))
	targetAlt := make(map[uint32]float64, len(order))
	preferredPos := make(map[uint32][2]float64, len(order))
	for i, id := range order {
		alt := baseAlt + float64(i)*gap
		if d := m.fleet.Drone(id); d != nil {
			if lat, lon, ok := d.LaunchPosition(); ok {
				preferredPos[id] = [2]float64{lat, lon}
			}
		}
		targetAlt[id] = alt
	}
	// ใช้จุดปล่อยจริงถ้าห่างกันพอ; จุดที่หายหรือใกล้เกินจะขยับขั้นต่ำ
	// รอบฐานของตัวเองเพื่อคง separation โดยไม่บังคับทุกลำกลับ GCS จุดเดียว.
	targetPos := planReturnPositions(order, preferredPos, gLat, gLon, gap)
	for _, id := range order {
		alt := targetAlt[id]
		pos := targetPos[id]
		lat, lon := pos[0], pos[1]
		if dec := m.env.CheckPoint(lat, lon, alt); !dec.Allow {
			return fmt.Errorf("return target for Drone %d unsafe at %.1fm: %s", id, alt, dec.Reason)
		}
	}
	m.returnMu.Lock()
	if m.returnCancel != nil {
		m.returnMu.Unlock()
		return fmt.Errorf("return/land sequence already active")
	}
	ctx, cancel := context.WithCancel(parent)
	done := make(chan struct{})
	m.returnCancel = cancel
	m.returnDone = done
	m.returnMu.Unlock()
	// ลงทะเบียน cancellation ก่อนหยุด formation เพื่อให้ E-STOP ที่เข้าพร้อมกัน
	// มองเห็น operation นี้และยกเลิกได้ ไม่มีช่องว่างให้ sequence หลุดไปรันทีหลัง.
	m.Stop() // หยุด follow loop — เข้าโหมดกลับฐาน

	go func() {
		defer func() {
			m.returnMu.Lock()
			if m.returnDone == done {
				m.returnCancel = nil
				m.returnDone = nil
			}
			m.returnMu.Unlock()
			close(done)
		}()
		m.returnSeq(ctx, order, targetAlt, targetPos, gap)
	}()
	return nil
}

func (m *Manager) returnSeq(ctx context.Context, order []uint32, targetAlt map[uint32]float64,
	targetPos map[uint32][2]float64, gap float64) {
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_INFO, 0, "return",
		fmt.Sprintf("กลับจุดปล่อยรายลำ — แยกชั้นและจุดลงห่างกันอย่างน้อย %.1fm", gap))

	// Phase 1: ไต่/ลดที่ตำแหน่งปัจจุบันก่อน ห้ามเคลื่อนแนวราบเข้าหากัน
	// จน telemetry ยืนยันว่าทุกลำอยู่คนละชั้นแล้ว.
	if !m.stageReturnAltitudes(ctx, order, targetAlt) {
		if ctx.Err() == nil {
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, 0, "return",
				"แยกชั้นไม่ครบ — เปลี่ยนเป็น FC RTL ทีละลำ")
			m.fallbackRTL(ctx, order)
		}
		return
	}

	// Phase 2: เมื่อชั้นแยกแล้วจึงบินแนวราบไปเหนือ launch point ของแต่ละลำ.
	sentAll := true
	for _, id := range order {
		if ctx.Err() != nil {
			return
		}
		if d := m.fleet.Drone(id); d != nil {
			alt := targetAlt[id]
			pos := targetPos[id]
			if err := d.Goto(pos[0], pos[1], alt); err != nil {
				sentAll = false
				m.audit.Command(id, "SwarmReturn", true, "goto home send error: "+err.Error(), -1)
				m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, id, "return",
					fmt.Sprintf("Drone %d ส่งคำสั่งกลับฐานไม่สำเร็จ", id))
			} else {
				m.audit.Command(id, "SwarmReturn", true,
					fmt.Sprintf("goto landing point %.7f,%.7f at %.1fm", pos[0], pos[1], alt), 0)
			}
		} else {
			sentAll = false
		}
	}
	if ctx.Err() != nil {
		return
	}
	if !sentAll || !m.waitNear(ctx, order, targetAlt, targetPos, returnTravelTimeoutSec) {
		if ctx.Err() != nil {
			return
		}
		m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, 0, "return",
			"กลับถึงจุดปล่อยไม่ครบ — ยกเลิก LAND และใช้ FC RTL ทีละลำ")
		m.fallbackRTL(ctx, order)
		return
	}

	// Phase 3: ลงจอดทีละลำ (แม่ก่อน)
	for i, id := range order {
		if ctx.Err() != nil {
			return
		}
		d := m.fleet.Drone(id)
		if d == nil {
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "return",
				fmt.Sprintf("Drone %d หายระหว่างลำดับลงจอด — ยกเลิกลำที่เหลือ", id))
			m.fallbackRTL(ctx, order[i:])
			return
		}
		m.events.Publish(pb.EventLevel_EVENT_LEVEL_INFO, id, "return",
			fmt.Sprintf("Drone %d กำลังลงจอด...", id))
		cctx, cancel := context.WithTimeout(ctx, 8*time.Second)
		code, err := d.LandNow(cctx)
		cancel()
		if err != nil || code != 0 {
			m.audit.Command(id, "SwarmLand", true, fmt.Sprintf("land failed code=%d err=%v", code, err), code)
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "return",
				fmt.Sprintf("Drone %d LAND ไม่สำเร็จ — หยุดลำดับและใช้ FC RTL", id))
			m.fallbackRTL(ctx, order[i:])
			return
		}
		m.audit.Command(id, "SwarmLand", true, "accepted", code)
		if m.waitLanded(ctx, id, 45) {
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, id, "return",
				fmt.Sprintf("Drone %d ลงจอดแล้ว", id))
		} else {
			if ctx.Err() != nil {
				return
			}
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "return",
				fmt.Sprintf("Drone %d ยังไม่ยืนยันว่าลงจอด — ไม่สั่งลำถัดไป", id))
			m.fallbackRTL(ctx, order[i:])
			return
		}
	}
	m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, 0, "return", "ทุกลำลงจอดเรียบร้อย")
}

func (m *Manager) fallbackRTL(ctx context.Context, ids []uint32) {
	// ห้ามยิง RTL ทุกลำติดกัน: FC แต่ละตัวอาจมี home เดียว/ใกล้กันและเส้นทาง
	// จะบรรจบพร้อมกัน. ส่งทีละลำและรอยืนยัน ground+disarmed ก่อนลำถัดไป.
	for _, id := range ids {
		if ctx.Err() != nil {
			return
		}
		d := m.fleet.Drone(id)
		if d == nil {
			continue
		}
		cctx, cancel := context.WithTimeout(ctx, 8*time.Second)
		code, err := d.ReturnHome(cctx)
		cancel()
		m.audit.Command(id, "FallbackRTL", true, fmt.Sprintf("code=%d err=%v", code, err), code)
		if err != nil || code != 0 {
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "return",
				fmt.Sprintf("Drone %d FC RTL ไม่สำเร็จ — ไม่สั่งลำถัดไป", id))
			return
		}
		if !m.waitLanded(ctx, id, fallbackLandingTimeoutSec) {
			if ctx.Err() == nil {
				m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "return",
					fmt.Sprintf("Drone %d ยังไม่ยืนยันลงจอด — ไม่สั่งลำถัดไป", id))
			}
			return
		}
	}
}

func (m *Manager) stageReturnAltitudes(ctx context.Context, ids []uint32,
	targetAlt map[uint32]float64) bool {
	for _, id := range ids {
		d := m.fleet.Drone(id)
		if d == nil {
			return false
		}
		lat, lon, _, _ := d.Nav()
		if lat == 0 && lon == 0 {
			return false
		}
		if err := d.Goto(lat, lon, targetAlt[id]); err != nil {
			m.audit.Command(id, "SwarmReturnStage", true, "stage altitude send error: "+err.Error(), -1)
			return false
		}
	}
	return m.waitAtReturnAltitudes(ctx, ids, targetAlt, returnStageTimeoutSec)
}

func (m *Manager) waitAtReturnAltitudes(ctx context.Context, ids []uint32,
	targetAlt map[uint32]float64, secs int) bool {
	for i := 0; i < secs; i++ {
		allReady := true
		for _, id := range ids {
			d := m.fleet.Drone(id)
			if d == nil {
				allReady = false
				continue
			}
			_, _, alt, _ := d.Nav()
			if !returnAltitudeReached(alt, targetAlt[id]) {
				allReady = false
			}
		}
		if allReady {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(time.Second):
		}
	}
	return false
}

// waitNear — รอจนทุกลำเข้าใกล้จุดลงและชั้นความสูงของตัวเอง (หรือหมดเวลา)
func (m *Manager) waitNear(ctx context.Context, ids []uint32, targetAlt map[uint32]float64, targetPos map[uint32][2]float64, secs int) bool {
	for i := 0; i < secs; i++ {
		allNear := true
		for _, id := range ids {
			d := m.fleet.Drone(id)
			if d == nil {
				allNear = false
				continue
			}
			lat, lon, alt, _ := d.Nav()
			pos := targetPos[id]
			if !returnTargetReached(lat, lon, alt, pos[0], pos[1], targetAlt[id]) {
				allNear = false
			}
		}
		if allNear {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(time.Second):
		}
	}
	return false
}

// waitLanded — ยืนยันทั้งแตะพื้นและ disarmed ก่อนสั่งลำถัดไป
func (m *Manager) waitLanded(ctx context.Context, id uint32, secs int) bool {
	for i := 0; i < secs; i++ {
		d := m.fleet.Drone(id)
		if d == nil {
			return false
		}
		_, _, alt, _ := d.Nav()
		if landedConfirmed(d.IsArmed(), alt) {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(time.Second):
		}
	}
	return false
}

func returnTargetReached(lat, lon, alt, gLat, gLon, targetAlt float64) bool {
	return geo.HaversineM(lat, lon, gLat, gLon) <= 6.0 && math.Abs(alt-targetAlt) <= 1.5
}

func returnAltitudeReached(alt, targetAlt float64) bool {
	return math.Abs(alt-targetAlt) <= 1.5
}

func landedConfirmed(armed bool, alt float64) bool {
	return !armed && alt < 1.0
}

func returnLayerGap(minSeparation float64) float64 {
	return math.Max(2.0, minSeparation)
}

// returnLandingEastOffset วางตัวแม่ที่ home แล้วสลับจุดลง +E/-E ออกไปทีละ gap:
// 0, +gap, -gap, +2gap, -2gap ... ทำให้ทุกคู่ห่างกันไม่น้อยกว่า gap.
func returnLandingEastOffset(index int, gap float64) float64 {
	if index <= 0 {
		return 0
	}
	step := float64((index + 1) / 2)
	if index%2 == 0 {
		return -step * gap
	}
	return step * gap
}

// planReturnPositions คง launch point เดิมให้มากที่สุด. ถ้าจุดหายหรืออยู่ใกล้
// จุดที่จัดไปแล้วเกินไป จะเลื่อนตะวันออก/ตะวันตกจากฐานของลำนั้นทีละ gap.
func planReturnPositions(order []uint32, preferred map[uint32][2]float64,
	fallbackLat, fallbackLon, gap float64) map[uint32][2]float64 {
	out := make(map[uint32][2]float64, len(order))
	used := make([][2]float64, 0, len(order))
	for _, id := range order {
		base, ok := preferred[id]
		if !ok || (base[0] == 0 && base[1] == 0) {
			base = [2]float64{fallbackLat, fallbackLon}
		}
		chosen := base
		for attempt := 0; attempt < len(order)*2+4; attempt++ {
			lat, lon := geo.OffsetM(base[0], base[1], 0, returnLandingEastOffset(attempt, gap))
			candidate := [2]float64{lat, lon}
			separated := true
			for _, prior := range used {
				if geo.HaversineM(candidate[0], candidate[1], prior[0], prior[1]) < gap-0.05 {
					separated = false
					break
				}
			}
			if separated {
				chosen = candidate
				break
			}
		}
		out[id] = chosen
		used = append(used, chosen)
	}
	return out
}

func selectReturnDrones(online, requested []uint32) ([]uint32, error) {
	if len(requested) == 0 {
		return append([]uint32(nil), online...), nil
	}
	selected := make([]uint32, 0, len(requested))
	seen := make(map[uint32]bool, len(requested))
	for _, id := range requested {
		if id == 0 || seen[id] {
			continue
		}
		if !contains(online, id) {
			return nil, fmt.Errorf("Drone %d ไม่ได้ online — เริ่ม RETURN ไม่ได้", id)
		}
		seen[id] = true
		selected = append(selected, id)
	}
	return selected, nil
}

func contains(s []uint32, v uint32) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

// State คืนสถานะปัจจุบัน (ให้ UI)
func (m *Manager) State() *pb.SwarmState {
	m.mu.RLock()
	defer m.mu.RUnlock()
	st := &pb.SwarmState{
		Active:      m.active,
		HeadingMode: m.headingMode,
		Formation:   m.formation,
		Spacing:     m.spacing,
		LeaderId:    m.leaderID,
		Note:        m.note,
	}
	if m.active {
		for _, fid := range m.fleet.OnlineIDs(m.cfg.LinkLostSec) {
			if fid != m.leaderID {
				st.Edges = append(st.Edges, &pb.SwarmState_Edge{
					LeaderId: m.leaderID, FollowerId: fid})
			}
		}
	}
	return st
}
