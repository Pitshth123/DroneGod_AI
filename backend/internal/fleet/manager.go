// Package fleet — จัดการโดรนหลายลำ (แทน drone_manager.py)
// 1 goroutine/ลำ อ่าน MAVLink; ticker กลางรวม snapshot → telemetry.Aggregator
package fleet

import (
	"context"
	"fmt"
	"log"
	"os"
	"sort"
	"sync"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/message"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/mavlink"
	"github.com/swarmgod/backend/internal/safety"
	"github.com/swarmgod/backend/internal/telemetry"
	"github.com/swarmgod/backend/pkg/geo"
)

// VehicleRegistry = sink บันทึก vehicle registry (spec §6.1) — เช่น store.Store
// แยกเป็น interface เพื่อไม่ผูก fleet กับ store โดยตรง (เทสต์ใช้ fake ได้)
// การเรียกอยู่นอก command critical path (best-effort) — error ไม่ทำให้ flight loop ค้าง
type VehicleRegistry interface {
	UpsertVehicle(uuid string, systemID, componentID int, displayName, serial string) error
}

type Manager struct {
	cfg        config.Config
	agg        *telemetry.Aggregator
	audit      *audit.Logger
	events     *events.Bus
	env        *safety.Envelope
	reg        VehicleRegistry // optional (nil = ไม่บันทึก registry)
	signKey    []byte
	signStrict bool

	mu      sync.RWMutex
	drones  map[uint32]*Drone
	cancels map[uint32]context.CancelFunc

	fsMu      sync.Mutex
	fsLink    map[uint32]int  // 0=ok 1=warn 2=lost
	fsBatt    map[uint32]bool // battery failsafe triggered
	herded    map[uint32]bool // geofence herd active
	dupWarned bool            // เตือน duplicate System ID ไปแล้ว (กัน event ท่วม)
}

func NewManager(cfg config.Config, agg *telemetry.Aggregator, aud *audit.Logger,
	bus *events.Bus, env *safety.Envelope, reg VehicleRegistry) *Manager {
	m := &Manager{
		cfg:        cfg,
		agg:        agg,
		audit:      aud,
		events:     bus,
		env:        env,
		reg:        reg,
		signStrict: cfg.MAVLinkSignStrict,
		drones:     make(map[uint32]*Drone),
		cancels:    make(map[uint32]context.CancelFunc),
		fsLink:     make(map[uint32]int),
		fsBatt:     make(map[uint32]bool),
		herded:     make(map[uint32]bool),
	}
	// SWARMGOD_MAVLINK_SIGNING=off → ไม่เซ็นเฟรมที่ส่งออก
	//
	// เดิมทำไว้เพื่อทดสอบสมมติฐาน "ดีเลย์เกิดจาก signing timestamp"
	// **ผลทดสอบบนโดรนจริง (§11.31): สมมติฐานผิด** — ปิด signing แล้ว
	//   · ยังสั่งได้ตามปกติ  → FC ไม่ได้บังคับ signing
	//   · ดีเลย์ยังอยู่ 7 วิ   → signing ไม่ใช่ต้นเหตุ
	//
	// คงสวิตช์ไว้เป็นเครื่องมือ diagnostic (และเผื่อ setup ที่ไม่ต้องการ signing)
	// ⚠️ ถ้า FC เปิด signing อยู่ ปิดฝั่งนี้จะสั่งอะไรไม่ได้เลย — ของเครื่องนี้ไม่ได้เปิด
	if cfg.MAVLinkSigningOff {
		log.Printf("[fleet] MAVLink signing ปิดไว้ด้วย SWARMGOD_MAVLINK_SIGNING=off " +
			"— โหมดทดสอบ ถ้า FC บังคับ signing จะสั่งอะไรไม่ได้เลย")
		return m
	}
	if b, err := os.ReadFile(cfg.MAVLinkSignKey); err == nil && len(b) >= 32 {
		m.signKey = b[:32]
		log.Printf("[fleet] MAVLink signing ENABLED (strict=%v)", cfg.MAVLinkSignStrict)
	}
	return m
}

// Connect เพิ่มโดรน + เปิด MAVLink connection + spawn reader goroutine
func (m *Manager) Connect(parent context.Context, id uint32, name, proto, host string, port uint32) error {
	m.mu.Lock()
	if _, exists := m.drones[id]; exists {
		m.mu.Unlock()
		return fmt.Errorf("drone %d already connected", id)
	}
	m.mu.Unlock()

	sysID := m.cfg.MAVLinkSysID
	if sysID == 0 {
		sysID = 255
	}
	conn, err := mavlink.Dial(proto, host, int(port), sysID, m.signKey, m.signStrict)
	if err != nil {
		return err
	}

	d := newDrone(id, name, host, port, conn)
	ctx, cancel := context.WithCancel(parent)

	m.mu.Lock()
	m.drones[id] = d
	m.cancels[id] = cancel
	m.mu.Unlock()

	go func() {
		log.Printf("[fleet] Drone %d reader started (%s://%s:%d)", id, proto, host, port)
		// 1 connection = 1 โดรน → route ทุก message เข้าลำนี้
		conn.Run(ctx, func(sysID byte, msg message.Message) {
			d.HandleFrame(sysID, msg)
		})
		log.Printf("[fleet] Drone %d reader stopped", id)
	}()
	// อุ่นช่องทาง RC override ไว้ก่อน — แก้อาการ "คำสั่งแรกหลังเชื่อมต่อถูกเมินหลายวินาที"
	// ส่งแบบ "ปล่อยทุกช่อง" จึงไม่แตะการควบคุม (ดู Drone.RCWarmup)
	go d.RCWarmup(ctx)
	return nil
}

// ── Vehicle registry + duplicate System ID (spec §6.1) ──────────

// vehicleUUID สร้างตัวตนถาวรของ vehicle:
//   - มี hardware serial → "hw-<hex>" (คงที่ข้ามการเชื่อมต่อ/reboot)
//   - ไม่มี serial (SITL) → "name-<display>" (fallback: ใช้ชื่อที่ operator ตั้ง)
func vehicleUUID(serial uint64, name string) string {
	if serial != 0 {
		return fmt.Sprintf("hw-%016x", serial)
	}
	return "name-" + name
}

// DuplicateSystemID ตรวจว่า drone id มี System ID ชนกับลำอื่นที่ verified แล้วหรือไม่
// คืน (id ที่ชน, true) ถ้าเจอ — ใช้ gate ก่อน Arm/group command
func (m *Manager) DuplicateSystemID(id uint32) (uint32, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	target := m.drones[id]
	if target == nil || !target.Verified() {
		return 0, false
	}
	sid := target.SystemID()
	if sid == 0 {
		return 0, false
	}
	for oid, d := range m.drones {
		if oid == id {
			continue
		}
		if d.Verified() && d.SystemID() == sid {
			return oid, true
		}
	}
	return 0, false
}

// vehicleInfo = snapshot เล็ก ๆ สำหรับ upsert นอก lock
type vehicleInfo struct {
	id     uint32
	sysID  byte
	serial uint64
	name   string
}

// RunRegistry เดิน loop ช้า (10s) บันทึก vehicle registry + last_seen (best-effort)
// อยู่นอก command critical path — DB error แค่ log ไม่กระทบ flight loop (spec §5.9)
func (m *Manager) RunRegistry(ctx context.Context) {
	if m.reg == nil {
		return
	}
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	m.reconcileRegistry() // ทำครั้งแรกทันที
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.reconcileRegistry()
		}
	}
}

func (m *Manager) reconcileRegistry() {
	if m.reg == nil {
		return
	}
	// เก็บ info ภายใต้ lock แล้วค่อยเขียน DB นอก lock
	m.mu.RLock()
	infos := make([]vehicleInfo, 0, len(m.drones))
	dupFound := false
	seen := map[byte]uint32{}
	for id, d := range m.drones {
		if !d.Verified() {
			continue
		}
		sid := d.SystemID()
		infos = append(infos, vehicleInfo{id: id, sysID: sid, serial: d.Serial(), name: d.Name})
		if sid != 0 {
			if _, dup := seen[sid]; dup {
				dupFound = true
			}
			seen[sid] = id
		}
	}
	m.mu.RUnlock()

	for _, in := range infos {
		serialStr := ""
		if in.serial != 0 {
			serialStr = fmt.Sprintf("%016x", in.serial)
		}
		uuid := vehicleUUID(in.serial, in.name)
		if err := m.reg.UpsertVehicle(uuid, int(in.sysID), 1, in.name, serialStr); err != nil {
			log.Printf("[fleet] registry upsert drone %d failed: %v", in.id, err)
		}
	}

	// เตือน duplicate System ID (ครั้งเดียวจนกว่าจะหาย)
	m.fsMu.Lock()
	warned := m.dupWarned
	m.dupWarned = dupFound
	m.fsMu.Unlock()
	if dupFound && !warned {
		m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, 0, "identity",
			"พบ MAVLink System ID ซ้ำระหว่างโดรน — Arm/group command จะถูกปฏิเสธ (spec §6.1)")
		m.audit.Event("identity", "duplicate System ID detected")
	}
}

// Disconnect หยุด reader + ลบโดรน
func (m *Manager) Disconnect(id uint32) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if cancel, ok := m.cancels[id]; ok {
		cancel()
		delete(m.cancels, id)
	}
	delete(m.drones, id)
}

// Drone คืนโดรนตาม id (nil ถ้าไม่มี)
func (m *Manager) Drone(id uint32) *Drone {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.drones[id]
}

// OtherSafetyStates คืน state ของโดรนอื่น (ยกเว้น exceptID) — สำหรับเช็ค separation
func (m *Manager) OtherSafetyStates(exceptID uint32) []safety.DroneState {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]safety.DroneState, 0, len(m.drones))
	for id, d := range m.drones {
		if id == exceptID {
			continue
		}
		out = append(out, d.SafetyState())
	}
	return out
}

// IDs คืน drone id ทั้งหมด (เรียงจากน้อยไปมาก)
func (m *Manager) IDs() []uint32 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	ids := make([]uint32, 0, len(m.drones))
	for id := range m.drones {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

// OnlineIDs คืน id ที่ online (verified + telemetry ล่าสุด) เรียงลำดับ — priority สำหรับเลือกตัวแม่
func (m *Manager) OnlineIDs(within float64) []uint32 {
	m.mu.RLock()
	defer m.mu.RUnlock()
	ids := make([]uint32, 0, len(m.drones))
	for id, d := range m.drones {
		if d.Online(within) {
			ids = append(ids, id)
		}
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

// FailsafeActive คืน true เมื่อโดรนนี้อยู่ในสภาวะ failsafe ที่ Core สั่ง RTL ไปแล้ว
// (battery critical หรือ link lost) — ดู failsafeTick.
//
// swarm formation loop ใช้ค่านี้กันไม่ให้ส่ง Goto/GotoYaw ทับ failsafe RTL ของ Core
// (ดู WAYPOINT_WAIT_SWARM_MODE_TEST_PLAN.md §9). ไม่รวม link "warn" (delayed=1)
// ซึ่งยังไม่ได้สั่ง RTL — เฉพาะ link "lost" (2) เท่านั้นที่ถือว่า failsafe.
func (m *Manager) FailsafeActive(id uint32) bool {
	m.fsMu.Lock()
	defer m.fsMu.Unlock()
	return m.fsBatt[id] || m.fsLink[id] >= 2
}

// Snapshot คืน telemetry ทุกลำ
func (m *Manager) Snapshot() []*pb.Telemetry {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]*pb.Telemetry, 0, len(m.drones))
	for _, d := range m.drones {
		out = append(out, d.Snapshot())
	}
	return out
}

// RunFailsafe ตรวจ link-loss + battery ทุก 1s → เตือน/สั่ง RTL อัตโนมัติ (ดู SECURITY.md §6)
func (m *Manager) RunFailsafe(ctx context.Context) {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.failsafeTick(ctx)
		}
	}
}

func (m *Manager) failsafeTick(ctx context.Context) {
	m.mu.RLock()
	ds := make([]*Drone, 0, len(m.drones))
	for _, d := range m.drones {
		ds = append(ds, d)
	}
	m.mu.RUnlock()

	for _, d := range ds {
		id := d.ID
		sec := d.SecondsSinceLastMsg()
		armed := d.IsArmed()

		// ── FC ไม่ยอมรับ RC override ที่ส่งไปแล้ว (ดู rcdebug.go) ──
		// เงียบ ๆ แล้วผู้ใช้เดาว่า "ปุ่มเสีย" ไม่ได้ — ต้องบอกตรง ๆ พร้อมสาเหตุที่พบบ่อยสุด
		if ch, want, got, since, stuck := d.OverrideStuck(); stuck {
			msg := fmt.Sprintf(
				"เครื่องบินยังไม่รับคำสั่ง CH%d (สั่ง %d แต่ FC รายงาน %d) มา %.0f วินาที "+
					"— เกิดเฉพาะคำสั่งแรกหลังเชื่อมต่อ ปกติหายเองใน ~15 วินาที "+
					"แล้วหลังจากนั้นสั่งได้ทันทีตลอด · คำสั่งออกจาก core ถูกต้องแล้ว "+
					"(ตัด signing/นาฬิกา/PWM ออกไปแล้วด้วยการวัด)",
				ch, want, got, since.Seconds())
			log.Printf("[fleet] Drone %d: %s", id, msg)
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, id, "servo", msg)
		} else if since, back := d.OverrideRecovered(); back {
			// เตือนไปแล้วต้องบอกตอนหายด้วย ไม่งั้นผู้ใช้ไม่รู้ว่าใช้ได้เมื่อไหร่
			msg := fmt.Sprintf("เครื่องบินรับคำสั่งแล้ว (ใช้เวลา %.0f วินาที)", since.Seconds())
			log.Printf("[fleet] Drone %d: %s", id, msg)
			m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, id, "servo", msg)
		}

		// วัดเวลา "คำสั่งแรกหลังเชื่อมต่อ" ทุกเที่ยวบิน แม้จะเร็วกว่าเกณฑ์เตือน
		// — ไม่งั้นพอปรับจนเร็วขึ้นแล้ว log จะไม่มีตัวเลขให้เทียบผลเลย
		if lat, ok := d.OverrideLatency(); ok {
			log.Printf("[fleet] Drone %d: FC รับคำสั่งแรกหลังเชื่อมต่อใน %.1f วินาที",
				id, lat.Seconds())
		}

		// ── link failsafe ──
		m.fsMu.Lock()
		prev := m.fsLink[id]
		m.fsMu.Unlock()
		if sec >= 0 && sec > m.cfg.LinkLostSec {
			if prev < 2 {
				m.setLink(id, 2)
				m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "link",
					fmt.Sprintf("link lost %.0fs — failsafe RTL", sec))
				m.audit.Command(id, "Failsafe-RTL", true, "link lost", 0)
				if armed {
					go d.ReturnHome(ctx) // best-effort (link อาจขาดจริง)
				}
			}
		} else if sec >= 0 && sec > m.cfg.LinkWarnSec {
			if prev != 1 {
				m.setLink(id, 1)
				m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, id, "link",
					fmt.Sprintf("telemetry delayed %.0fs", sec))
			}
		} else if sec >= 0 {
			if prev != 0 {
				if prev == 2 {
					m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, id, "link", "link recovered")
				}
				m.setLink(id, 0)
			}
		}

		// ── battery failsafe ──
		batt := d.SafetyState().BatteryPct
		m.fsMu.Lock()
		bprev := m.fsBatt[id]
		m.fsMu.Unlock()
		if armed && batt > 0 && batt < m.cfg.BattFailsafePct {
			if !bprev {
				m.fsMu.Lock()
				m.fsBatt[id] = true
				m.fsMu.Unlock()
				m.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, id, "battery",
					fmt.Sprintf("battery %.0f%% critical — failsafe RTL", batt))
				m.audit.Command(id, "Failsafe-RTL", true, "battery critical", 0)
				go d.ReturnHome(ctx)
			}
		} else if batt > m.cfg.BattFailsafePct+5 {
			m.fsMu.Lock()
			m.fsBatt[id] = false
			m.fsMu.Unlock()
		}

		// ── geofence herd: แตะ/ข้ามเส้น → ดึงกลับเข้าเขต 2m (armed เท่านั้น) ──
		if armed && m.env.GeofenceEnabled() {
			st := d.SafetyState()
			if st.Lat != 0 || st.Lon != 0 {
				poly := m.env.Geofence()
				inside := geo.PointInPolygon(st.Lat, st.Lon, poly)
				edgeDist := geo.DistanceToEdge(st.Lat, st.Lon, poly)
				breaching := !inside || edgeDist < 2.0
				m.fsMu.Lock()
				wasHerded := m.herded[id]
				m.fsMu.Unlock()
				if breaching {
					tLat, tLon := geo.HerdTarget(st.Lat, st.Lon, poly, 2.0)
					d.Goto(tLat, tLon, st.AltRel) // ดึงเข้าเขตที่ระดับเดิม (targets แยกตามจุดที่แตะ = กันชน)
					if !wasHerded {
						m.fsMu.Lock()
						m.herded[id] = true
						m.fsMu.Unlock()
						m.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, id, "geofence",
							"แตะเส้นเขต — ดึงกลับเข้าเขต 2m")
						m.audit.Command(id, "GeofenceHerd", true, "pull-in 2m", 0)
					}
				} else if inside && edgeDist > 3.0 && wasHerded {
					m.fsMu.Lock()
					m.herded[id] = false
					m.fsMu.Unlock()
					m.events.Publish(pb.EventLevel_EVENT_LEVEL_OK, id, "geofence", "กลับเข้าเขตแล้ว")
				}
			}
		}
	}
}

func (m *Manager) setLink(id uint32, s int) {
	m.fsMu.Lock()
	m.fsLink[id] = s
	m.fsMu.Unlock()
}

// Run เดิน ticker กลาง: ทุก 1/Hz วินาที broadcast snapshot ทุกลำ
func (m *Manager) Run(ctx context.Context) {
	hz := m.cfg.TelemetryHz
	if hz <= 0 {
		hz = 10
	}
	ticker := time.NewTicker(time.Duration(float64(time.Second) / hz))
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.mu.RLock()
			for _, d := range m.drones {
				m.agg.Broadcast(d.Snapshot())
			}
			m.mu.RUnlock()
		}
	}
}
