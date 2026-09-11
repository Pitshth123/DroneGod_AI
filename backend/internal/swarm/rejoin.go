package swarm

import (
	"context"
	"errors"
	"fmt"
	"log"
	"math"
	"sort"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/pkg/geo"
)

// REJOIN — fly an operator-detached (TAKE CONTROL) aircraft back into its own
// formation slot, then hand it back to the follower loop.
//
// The approach reuses FORM UP's collision-avoiding pattern for one aircraft:
//  1. climb in place to a transit layer above every online aircraft
//  2. cross horizontally at that layer to above its slot
//  3. descend into the slot
//
// Slots never renumber during a formation run (slotOwners), so the aircraft
// returns to a physically vacant slot and no other follower is re-targeted.
// Legs 2–3 track the leader's current position, so the slot is found even if
// the leader drifts, but operators should hold the leader still meanwhile.
//
// The aircraft stays manualExcluded until it is settled in the slot, so the
// follower tick never commands it mid-approach. Fail-closed: an unsafe point,
// failsafe, timeout or ownership change aborts the run, the aircraft stays
// INDIVIDUAL, and (when Core still owns it) it is braked in place.

const (
	rejoinMinAltM      = 2.0 // below this the aircraft is not airborne enough to transit
	rejoinArriveHorizM = 2.0
	rejoinArriveVertM  = 1.0
	rejoinLegInterval  = 400 * time.Millisecond
)

var (
	errRejoinRevoked  = errors.New("rejoin ownership revoked")
	errRejoinFailsafe = errors.New("failsafe active")
)

type rejoinRun struct {
	token  uint64
	slot   int
	cancel context.CancelFunc
}

// claimSlotLocked returns id's own slot, else the first vacant slot, else a new
// slot at the end. Caller holds m.mu (write).
func (m *Manager) claimSlotLocked(id uint32) int {
	for i, owner := range m.slotOwners {
		if owner == id {
			return i
		}
	}
	for i, owner := range m.slotOwners {
		if owner == 0 {
			m.slotOwners[i] = id
			return i
		}
	}
	m.slotOwners = append(m.slotOwners, id)
	return len(m.slotOwners) - 1
}

// syncSlotsLocked keeps slot ownership stable across membership changes: the
// leader never owns a follower slot and every managed follower owns exactly one.
// Excluded/offline owners keep their slot reserved, so nobody is re-targeted
// into the space they physically occupy. Caller holds m.mu (write).
func (m *Manager) syncSlotsLocked(managed []uint32) {
	for i, owner := range m.slotOwners {
		if owner != 0 && owner == m.leaderID {
			m.slotOwners[i] = 0
		}
	}
	for _, id := range managed {
		if id != m.leaderID {
			m.claimSlotLocked(id)
		}
	}
}

func (m *Manager) rejoinPrecheckLocked(id uint32) error {
	switch {
	case m.missionLeaderID != 0:
		return fmt.Errorf("ขบวนอยู่ใต้ Core mission — REJOIN ใช้ไม่ได้ระหว่าง mission")
	case !m.active || m.stopping || m.halted:
		return fmt.Errorf("ขบวนไม่ได้ทำงานอยู่ — ใช้ START formation ใหม่แทน")
	case !m.ready:
		return fmt.Errorf("ขบวนยังจัดรูปไม่เสร็จ — รอให้พร้อมก่อน")
	case m.rejoin[id] != nil:
		return fmt.Errorf("Drone %d กำลังกลับเข้าขบวนอยู่แล้ว", id)
	case !m.manualExcluded[id]:
		return fmt.Errorf("Drone %d อยู่ในขบวนอยู่แล้ว (ไม่ได้ถูก TAKE CONTROL)", id)
	}
	return nil
}

// Rejoin starts flying a TAKE CONTROL aircraft back into its reserved slot and
// returns that slot index. Ownership moves from the operator to the rejoin run
// immediately; the follower loop takes over once the aircraft is in the slot.
func (m *Manager) Rejoin(id uint32) (int, error) {
	if id == 0 {
		return 0, fmt.Errorf("REJOIN ต้องระบุโดรน 1 ลำ")
	}
	m.mu.RLock()
	err := m.rejoinPrecheckLocked(id)
	m.mu.RUnlock()
	if err != nil {
		return 0, err
	}
	if m.fleet == nil {
		return 0, fmt.Errorf("fleet unavailable")
	}
	if !contains(m.fleet.OnlineIDs(m.cfg.LinkLostSec), id) {
		return 0, fmt.Errorf("Drone %d ไม่ได้ออนไลน์", id)
	}
	if m.fleet.FailsafeActive(id) {
		return 0, fmt.Errorf("Drone %d อยู่ใน failsafe — กลับเข้าขบวนไม่ได้", id)
	}
	d := m.fleet.Drone(id)
	if d == nil {
		return 0, fmt.Errorf("Drone %d ไม่อยู่ในระบบ", id)
	}
	st := d.SafetyState()
	switch {
	case !st.Armed:
		return 0, fmt.Errorf("Drone %d ยังไม่ได้ arm", id)
	case st.Mode != "GUIDED":
		return 0, fmt.Errorf("Drone %d ต้องอยู่โหมด GUIDED ก่อนกลับเข้าขบวน (ตอนนี้ %s)", id, st.Mode)
	case st.Lat == 0 && st.Lon == 0:
		return 0, fmt.Errorf("Drone %d ยังไม่มีพิกัด GPS", id)
	case st.AltRel < rejoinMinAltM:
		return 0, fmt.Errorf("Drone %d ต้องบินอยู่ (สูง ≥ %.0f m) ก่อนกลับเข้าขบวน", id, rejoinMinAltM)
	}

	m.mu.Lock()
	if err := m.rejoinPrecheckLocked(id); err != nil {
		m.mu.Unlock()
		return 0, err
	}
	if m.formationCtx == nil {
		m.mu.Unlock()
		return 0, fmt.Errorf("ขบวนไม่ได้ทำงานอยู่ — ใช้ START formation ใหม่แทน")
	}
	if _, _, _, _, ok := m.slotTargetLocked(0); !ok {
		m.mu.Unlock()
		return 0, fmt.Errorf("ยังไม่มีพิกัดของตัวแม่ Drone %d", m.leaderID)
	}
	if m.rejoin == nil {
		m.rejoin = make(map[uint32]*rejoinRun)
	}
	slot := m.claimSlotLocked(id)
	m.rejoinSeqN++
	token := m.rejoinSeqN
	ctx, cancel := context.WithCancel(m.formationCtx)
	m.rejoin[id] = &rejoinRun{token: token, slot: slot, cancel: cancel}
	m.note = fmt.Sprintf("REJOIN Drone %d → ช่อง %d ของขบวน (leader Drone %d)", id, slot+1, m.leaderID)
	note := m.note
	m.mu.Unlock()

	m.publishSwarm(pb.EventLevel_EVENT_LEVEL_INFO, id, note)
	go m.rejoinSeq(ctx, d, id, token, slot)
	return slot, nil
}

// AbortRejoin cancels an in-progress REJOIN (operator takeover / STOP). Taking
// m.mu is the rejoin final-write boundary: once it returns true, no later rejoin
// navigation write for id can begin. The aircraft stays INDIVIDUAL.
func (m *Manager) AbortRejoin(id uint32) bool {
	m.mu.Lock()
	aborted := m.abortRejoinLocked(id)
	m.mu.Unlock()
	if aborted {
		m.publishSwarm(pb.EventLevel_EVENT_LEVEL_WARN, id, fmt.Sprintf(
			"REJOIN Drone %d ยกเลิก — ผู้ควบคุมรับคุมคืน (ยังเป็นควบคุมเดี่ยว)", id))
	}
	return aborted
}

func (m *Manager) abortRejoinLocked(id uint32) bool {
	r := m.rejoin[id]
	if r == nil {
		return false
	}
	delete(m.rejoin, id)
	r.cancel()
	return true
}

// RejoinInProgress reports whether id is currently flying back into formation.
func (m *Manager) RejoinInProgress(id uint32) bool {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.rejoin[id] != nil
}

func (m *Manager) rejoiningIDsLocked() []uint32 {
	ids := make([]uint32, 0, len(m.rejoin))
	for id := range m.rejoin {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

// sendRejoinIfOwned is the rejoin final-write boundary (mirrors
// sendFollowerIfOwned): the write runs under RLock only while this exact run
// still owns the aircraft and the formation is live.
func (m *Manager) sendRejoinIfOwned(id uint32, token uint64, send func() error) (bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	r := m.rejoin[id]
	if r == nil || r.token != token || !m.active || !m.ready || m.stopping || m.halted ||
		m.missionLeaderID != 0 {
		return false, nil
	}
	return true, send()
}

// slotTargetLocked = where slot sits around the current leader. Caller holds m.mu.
func (m *Manager) slotTargetLocked(slot int) (lat, lon, alt, yaw float64, ok bool) {
	if m.fleet == nil {
		return 0, 0, 0, 0, false
	}
	leader := m.fleet.Drone(m.leaderID)
	if leader == nil {
		return 0, 0, 0, 0, false
	}
	lLat, lLon, lAlt, lHdg := leader.Nav()
	if lLat == 0 && lLon == 0 {
		return 0, 0, 0, 0, false
	}
	lat, lon, alt = slotPosition(lLat, lLon, lAlt, lHdg, m.spacing, m.formation, m.headingMode, slot)
	return lat, lon, alt, lHdg, true
}

func (m *Manager) slotTarget(slot int) (lat, lon, alt, yaw float64, ok bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.slotTargetLocked(slot)
}

// rejoinTransitAlt = a layer above every online aircraft (same rule as FORM UP).
func (m *Manager) rejoinTransitAlt(gap float64) float64 {
	maxAlt := 0.0
	for _, oid := range m.fleet.OnlineIDs(m.cfg.LinkLostSec) {
		if od := m.fleet.Drone(oid); od != nil {
			_, _, alt, _ := od.Nav()
			maxAlt = math.Max(maxAlt, alt)
		}
	}
	return formUpTransitAltitude(maxAlt, 0, gap)
}

type rejoinTarget func() (lat, lon, alt, yaw float64, ok bool)

func (m *Manager) rejoinSeq(ctx context.Context, d *fleet.Drone, id uint32, token uint64, slot int) {
	lat0, lon0, _, _ := d.Nav()
	gap := math.Max(4.0, m.env.MinSeparation()+2.0)
	transit := m.rejoinTransitAlt(gap)
	legs := []struct {
		label  string
		target rejoinTarget
	}{
		{"ไต่ขึ้นชั้นผ่านทาง", func() (float64, float64, float64, float64, bool) {
			_, _, _, yaw, ok := m.slotTarget(slot)
			return lat0, lon0, transit, yaw, ok
		}},
		{"บินไปเหนือช่องในขบวน", func() (float64, float64, float64, float64, bool) {
			lat, lon, alt, yaw, ok := m.slotTarget(slot)
			return lat, lon, math.Max(transit, alt+gap), yaw, ok
		}},
		{"ลดลงเข้าช่อง", func() (float64, float64, float64, float64, bool) {
			return m.slotTarget(slot)
		}},
	}
	for _, leg := range legs {
		if err := m.rejoinLeg(ctx, d, id, token, leg.label, leg.target); err != nil {
			reason := err.Error()
			if errors.Is(err, errRejoinFailsafe) {
				reason = "Core failsafe รับคุมลำนี้แล้ว"
			}
			m.finishRejoin(ctx, d, id, token, false, reason, !errors.Is(err, errRejoinFailsafe))
			return
		}
	}
	m.finishRejoin(ctx, d, id, token, true, "", false)
}

func (m *Manager) rejoinLeg(ctx context.Context, d *fleet.Drone, id uint32, token uint64,
	label string, target rejoinTarget) error {
	deadline := time.NewTimer(formUpPhaseTimeoutSec * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(rejoinLegInterval)
	defer ticker.Stop()
	for {
		if m.fleet.FailsafeActive(id) {
			return errRejoinFailsafe
		}
		lat, lon, alt, yaw, ok := target()
		if !ok {
			return fmt.Errorf("%s: ไม่มีพิกัดตัวแม่", label)
		}
		if dec := m.env.CheckPoint(lat, lon, alt); !dec.Allow {
			return fmt.Errorf("%s ไม่ปลอดภัย: %s", label, dec.Reason)
		}
		sent, err := m.sendRejoinIfOwned(id, token, func() error {
			return d.GotoYawContext(m.fleet.WithFailsafeSendGuard(ctx, id), lat, lon, alt, yaw)
		})
		if !sent {
			return errRejoinRevoked
		}
		if err != nil {
			if ctx.Err() != nil {
				return errRejoinRevoked
			}
			if m.fleet.FailsafeActive(id) {
				return errRejoinFailsafe
			}
			return fmt.Errorf("%s: ส่งคำสั่งไม่สำเร็จ: %v", label, err)
		}
		gLat, gLon, gAlt, _ := d.Nav()
		if geo.HaversineM(gLat, gLon, lat, lon) <= rejoinArriveHorizM &&
			math.Abs(gAlt-alt) <= rejoinArriveVertM {
			return nil
		}
		select {
		case <-ctx.Done():
			return errRejoinRevoked
		case <-deadline.C:
			return fmt.Errorf("%s หมดเวลา (%ds)", label, formUpPhaseTimeoutSec)
		case <-ticker.C:
		}
	}
}

// finishRejoin hands a settled aircraft to the follower loop, or fails closed:
// the run releases ownership, the aircraft stays INDIVIDUAL and — only while this
// run still owns it and no failsafe owns it — is braked in place.
func (m *Manager) finishRejoin(ctx context.Context, d *fleet.Drone, id uint32, token uint64,
	ok bool, reason string, brake bool) {
	if ok {
		m.mu.Lock()
		r := m.rejoin[id]
		if r == nil || r.token != token || !m.active || m.stopping || m.halted {
			m.mu.Unlock()
			return
		}
		delete(m.rejoin, id)
		delete(m.manualExcluded, id)
		m.formationGen++ // follower tick now owns it — invalidate older plans
		m.note = fmt.Sprintf("REJOIN Drone %d สำเร็จ — กลับเข้าช่อง %d ของขบวนแล้ว", id, r.slot+1)
		note := m.note
		m.mu.Unlock()
		r.cancel()
		m.publishSwarm(pb.EventLevel_EVENT_LEVEL_OK, id, note)
		return
	}

	braked := false
	if brake {
		sent, err := m.sendRejoinIfOwned(id, token, func() error {
			return d.MoveVelocityContext(m.fleet.WithFailsafeSendGuard(ctx, id), 0, 0, 0, 0)
		})
		braked = sent && err == nil
	}
	m.mu.Lock()
	r := m.rejoin[id]
	own := r != nil && r.token == token
	if own {
		delete(m.rejoin, id)
	}
	m.mu.Unlock()
	if !own {
		return // aborted by takeover/STOP/formation stop — the aborter reports it
	}
	r.cancel()
	msg := fmt.Sprintf("REJOIN Drone %d หยุด: %s — ยังเป็นควบคุมเดี่ยว (INDIVIDUAL)", id, reason)
	if braked {
		msg += " · เบรกค้างอยู่กับที่"
	}
	m.publishSwarm(pb.EventLevel_EVENT_LEVEL_WARN, id, msg)
}

func (m *Manager) publishSwarm(level pb.EventLevel, id uint32, msg string) {
	if m.audit != nil {
		m.audit.Event("swarm", msg)
	}
	if m.events != nil {
		m.events.Publish(level, id, "swarm", msg)
	}
	log.Printf("[swarm] %s", msg)
}
