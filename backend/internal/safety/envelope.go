// Package safety — Safety Envelope: ด่านบังคับก่อนทุกคำสั่งออกไปโดรน
// หลักการ (ดู docs/SECURITY.md §3): UI ข้ามไม่ได้ — ทุก command ต้องผ่านที่นี่
package safety

import (
	"fmt"
	"math"
	"sync"

	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/pkg/geo"
)

// DroneState = สถานะเท่าที่ safety ต้องใช้ตัดสิน (fleet ส่งมาให้)
type DroneState struct {
	ID              uint32
	Lat, Lon        float64
	AltRel          float64
	BatteryPct      float64
	SatCount        int
	GpsFix          int // 0..5 (3 = 3D fix)
	Armed           bool
	Mode            string
	Heading         float64
	TelemetryAgeSec float64 // -1 = ยังไม่เคยได้ telemetry
}

// Decision = ผลการตรวจ 1 คำสั่ง
type Decision struct {
	Allow  bool
	Reason string // ถ้า Allow=false บอกเหตุผลกลับไป UI + audit
	// Transient = เงื่อนไขนี้ "รอแล้วหายได้" (เช่น GPS ยังไม่ fix ตอนเพิ่งต่อ)
	// ตรงข้ามกับเงื่อนไขถาวร (เช่น แบตต่ำ) ที่รอไปก็ไม่ดีขึ้น
	// ผู้เรียกใช้ตัดสินใจได้ว่าจะรอความพร้อม หรือปฏิเสธทันที
	Transient bool
}

func allow() Decision                  { return Decision{Allow: true} }
func deny(f string, a ...any) Decision { return Decision{Allow: false, Reason: fmt.Sprintf(f, a...)} }

// denyTransient = ปฏิเสธแบบ "รอได้" — สถานะอาจดีขึ้นเองในไม่กี่วินาที
func denyTransient(f string, a ...any) Decision {
	d := deny(f, a...)
	d.Transient = true
	return d
}

// Envelope ถือ config + geofence ปัจจุบัน + ตำแหน่ง GCS
type Envelope struct {
	mu       sync.RWMutex
	cfg      config.Config
	gcsLat   float64
	gcsLon   float64
	geofence [][2]float64 // polygon; ว่าง = ปิด fence
}

func New(cfg config.Config) *Envelope {
	return &Envelope{cfg: cfg, gcsLat: cfg.GCSLat, gcsLon: cfg.GCSLon}
}

func (e *Envelope) SetGCS(lat, lon float64) {
	e.mu.Lock()
	e.gcsLat, e.gcsLon = lat, lon
	e.mu.Unlock()
}

func (e *Envelope) SetGeofence(poly [][2]float64) error {
	if len(poly) > 1 && poly[0] == poly[len(poly)-1] {
		poly = poly[:len(poly)-1]
	}
	if err := ValidateGeofence(poly); err != nil {
		return err
	}
	e.mu.Lock()
	e.geofence = append([][2]float64(nil), poly...)
	e.mu.Unlock()
	return nil
}

// ValidateGeofence ป้องกัน fence ที่ดูเหมือนเปิดอยู่แต่ core บังคับใช้ไม่ได้
// polygon ว่างคือคำสั่ง clear; ค่าอื่นต้องเป็น polygon ปิดโดยนัยที่ไม่ไขว้กัน.
func ValidateGeofence(poly [][2]float64) error {
	if len(poly) == 0 {
		return nil
	}
	if len(poly) < 3 {
		return fmt.Errorf("geofence requires at least 3 vertices")
	}
	seen := make(map[[2]float64]struct{}, len(poly))
	for i, p := range poly {
		if math.IsNaN(p[0]) || math.IsNaN(p[1]) || math.IsInf(p[0], 0) || math.IsInf(p[1], 0) {
			return fmt.Errorf("geofence vertex %d must be finite", i+1)
		}
		if p[0] < -90 || p[0] > 90 || p[1] < -180 || p[1] > 180 {
			return fmt.Errorf("geofence vertex %d is outside valid latitude/longitude", i+1)
		}
		if _, exists := seen[p]; exists {
			return fmt.Errorf("geofence contains duplicate vertex %d", i+1)
		}
		seen[p] = struct{}{}
	}
	area2 := 0.0
	for i, p := range poly {
		n := poly[(i+1)%len(poly)]
		area2 += p[1]*n[0] - n[1]*p[0]
	}
	if math.Abs(area2) < 1e-12 {
		return fmt.Errorf("geofence vertices do not form a polygon")
	}
	for i := range poly {
		a, b := poly[i], poly[(i+1)%len(poly)]
		for j := i + 1; j < len(poly); j++ {
			if j == i || j == (i+1)%len(poly) || (i == 0 && j == len(poly)-1) {
				continue
			}
			c, d := poly[j], poly[(j+1)%len(poly)]
			if segmentsCross(a, b, c, d) {
				return fmt.Errorf("geofence edges intersect")
			}
		}
	}
	return nil
}

func segmentsCross(a, b, c, d [2]float64) bool {
	cross := func(p, q, r [2]float64) float64 {
		return (q[1]-p[1])*(r[0]-p[0]) - (q[0]-p[0])*(r[1]-p[1])
	}
	onSegment := func(p, q, r [2]float64) bool {
		return r[0] >= math.Min(p[0], q[0])-1e-12 && r[0] <= math.Max(p[0], q[0])+1e-12 &&
			r[1] >= math.Min(p[1], q[1])-1e-12 && r[1] <= math.Max(p[1], q[1])+1e-12
	}
	abc, abd := cross(a, b, c), cross(a, b, d)
	cda, cdb := cross(c, d, a), cross(c, d, b)
	if ((abc > 0 && abd < 0) || (abc < 0 && abd > 0)) &&
		((cda > 0 && cdb < 0) || (cda < 0 && cdb > 0)) {
		return true
	}
	return (math.Abs(abc) <= 1e-12 && onSegment(a, b, c)) ||
		(math.Abs(abd) <= 1e-12 && onSegment(a, b, d)) ||
		(math.Abs(cda) <= 1e-12 && onSegment(c, d, a)) ||
		(math.Abs(cdb) <= 1e-12 && onSegment(c, d, b))
}

func (e *Envelope) limits() (float64, float64, [][2]float64) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.gcsLat, e.gcsLon, append([][2]float64(nil), e.geofence...)
}

// ── การตรวจแต่ละแบบ (แยกเพื่อทดสอบ + reuse) ──────────────────

// CheckAltitude: 0 < alt ≤ MaxAlt
func (e *Envelope) CheckAltitude(alt float64) Decision {
	if math.IsNaN(alt) || math.IsInf(alt, 0) {
		return deny("altitude must be finite")
	}
	if alt <= 0 {
		return deny("altitude must be > 0 (got %.1fm)", alt)
	}
	if alt > e.cfg.MaxAltM {
		return deny("altitude %.1fm exceeds limit %.1fm", alt, e.cfg.MaxAltM)
	}
	return allow()
}

// CheckArmPrecondition: gate ก่อน arm/takeoff (battery + GPS)
// GPS/sat ที่ยังไม่พร้อมถูกทำเครื่องหมาย Transient — เพิ่งต่อโดรนแล้ว GPS ยังไม่ fix
// เป็นเรื่องปกติและหายเองใน 2-3 วินาที ผู้เรียกจึงเลือก "รอ" ได้แทนที่จะทิ้งคำสั่ง
// (แบตต่ำไม่ใช่ transient — รอไปก็ไม่ดีขึ้น จึงปฏิเสธทันที)
func (e *Envelope) CheckArmPrecondition(s DroneState) Decision {
	if math.IsNaN(s.BatteryPct) || math.IsInf(s.BatteryPct, 0) {
		return deny("UAV_%d battery telemetry is invalid", s.ID)
	}
	if s.BatteryPct < e.cfg.MinArmBatteryPct {
		return deny("UAV_%d battery %.0f%% below arm minimum %.0f%%",
			s.ID, s.BatteryPct, e.cfg.MinArmBatteryPct)
	}
	if s.GpsFix < 3 {
		return denyTransient("UAV_%d no 3D GPS fix (fix=%d)", s.ID, s.GpsFix)
	}
	if s.SatCount < e.cfg.MinSatCount {
		return denyTransient("UAV_%d only %d sats (need %d)", s.ID, s.SatCount, e.cfg.MinSatCount)
	}
	return allow()
}

// CheckGoto: จุดเป้าต้องอยู่ในรัศมี + ใน geofence + alt ok + armed
func (e *Envelope) CheckGoto(s DroneState, lat, lon, alt float64, others []DroneState) Decision {
	if !s.Armed {
		return deny("UAV_%d not armed", s.ID)
	}
	if !validCoordinate(lat, lon) {
		return deny("target latitude/longitude is invalid")
	}
	if d := e.CheckAltitude(alt); !d.Allow {
		return d
	}
	gcsLat, gcsLon, fence := e.limits()
	if gcsLat != 0 || gcsLon != 0 {
		if r := geo.HaversineM(gcsLat, gcsLon, lat, lon); r > e.cfg.MaxRadiusM {
			return deny("target %.0fm from GCS exceeds radius %.0fm", r, e.cfg.MaxRadiusM)
		}
	}
	if !geo.PointInPolygon(lat, lon, fence) {
		return deny("target outside geofence")
	}
	// inter-drone separation
	for _, o := range others {
		if o.ID == s.ID {
			continue
		}
		if o.TelemetryAgeSec < 0 || o.TelemetryAgeSec > e.cfg.LinkWarnSec || !validCoordinate(o.Lat, o.Lon) {
			return deny("cannot verify separation from UAV_%d (position telemetry unavailable)", o.ID)
		}
		if d := geo.HaversineM(o.Lat, o.Lon, lat, lon); d < e.cfg.MinSeparationM {
			return deny("target %.1fm from UAV_%d < min separation %.1fm",
				d, o.ID, e.cfg.MinSeparationM)
		}
	}
	return allow()
}

// CheckManualState บังคับให้ manual velocity ใช้เฉพาะตำแหน่งสดและถูกต้อง
// เพื่อไม่คำนวณทิศ/ขอบเขตจาก telemetry เก่าหรือพิกัดศูนย์.
func (e *Envelope) CheckManualState(s DroneState) Decision {
	if s.TelemetryAgeSec < 0 || s.TelemetryAgeSec > e.cfg.LinkWarnSec {
		return deny("UAV_%d telemetry is stale (age %.1fs)", s.ID, s.TelemetryAgeSec)
	}
	if !validCoordinate(s.Lat, s.Lon) || math.IsNaN(s.Heading) || math.IsInf(s.Heading, 0) {
		return deny("UAV_%d has no valid position/heading", s.ID)
	}
	return allow()
}

// CheckPoint: ตรวจจุดเป้า (alt + radius + geofence) — ใช้ตอน swarm follow
// (ไม่เช็ค armed/separation เพราะ formation offset การันตี spacing โดยออกแบบ)
func (e *Envelope) CheckPoint(lat, lon, alt float64) Decision {
	if !validCoordinate(lat, lon) {
		return deny("target latitude/longitude is invalid")
	}
	if d := e.CheckAltitude(alt); !d.Allow {
		return d
	}
	gcsLat, gcsLon, fence := e.limits()
	if gcsLat != 0 || gcsLon != 0 {
		if r := geo.HaversineM(gcsLat, gcsLon, lat, lon); r > e.cfg.MaxRadiusM {
			return deny("target %.0fm from GCS exceeds radius %.0fm", r, e.cfg.MaxRadiusM)
		}
	}
	if !geo.PointInPolygon(lat, lon, fence) {
		return deny("target outside geofence")
	}
	return allow()
}

func validCoordinate(lat, lon float64) bool {
	return !math.IsNaN(lat) && !math.IsNaN(lon) && !math.IsInf(lat, 0) && !math.IsInf(lon, 0) &&
		lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && !(lat == 0 && lon == 0)
}

// MinSeparation คืนระยะห่างขั้นต่ำ (ให้ swarm ตรวจ spacing ตอนตั้งค่า)
func (e *Envelope) MinSeparation() float64 { return e.cfg.MinSeparationM }

// GCS คืนตำแหน่ง GCS/home (สำหรับ RTL/landing)
func (e *Envelope) GCS() (lat, lon float64) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.gcsLat, e.gcsLon
}

// Geofence คืน polygon ปัจจุบัน + เปิดใช้อยู่ไหม (สำหรับ geofence herd)
func (e *Envelope) Geofence() [][2]float64 {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return append([][2]float64(nil), e.geofence...)
}
func (e *Envelope) GeofenceEnabled() bool {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return len(e.geofence) >= 3
}

// CheckSpeed: ≤ MaxSpeed
func (e *Envelope) CheckSpeed(v float64) Decision {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return deny("speed must be finite")
	}
	if v <= 0 {
		return deny("speed must be > 0")
	}
	if v > e.cfg.MaxSpeedMS {
		return deny("speed %.1f m/s exceeds limit %.1f m/s", v, e.cfg.MaxSpeedMS)
	}
	return allow()
}

// CheckDangerous: คำสั่งกลุ่มเสี่ยงต้อง confirmed=true (KILL, disarm-in-air)
func (e *Envelope) CheckDangerous(name string, confirmed bool) Decision {
	if !confirmed {
		return deny("%s requires explicit confirmation", name)
	}
	return allow()
}
