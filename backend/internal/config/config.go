// Package config โหลดค่าตั้งของ core จาก env + default ที่ปลอดภัย
// หลักการ: ไม่ hardcode limit/ip/port ในโค้ด — อ่านที่เดียวจาก Config
package config

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
)

// Config = ค่าตั้งทั้งหมดของ SwarmGod core
type Config struct {
	// ── gRPC server (คุยกับ Python cockpit) ──
	GRPCAddr string // default 127.0.0.1:50051 (bind localhost เพื่อความปลอดภัย)
	TLSCert  string // path cert (mTLS)
	TLSKey   string // path key
	CACert   string // path CA ที่เซ็น client cert

	// ── MAVLink ──
	MAVLinkSignKey    string // path shared key สำหรับ MAVLink signing (HMAC)
	MAVLinkSignStrict bool   // reject frame ที่ไม่เซ็น (ห้ามเปิดกับ SITL ที่ไม่ตั้ง signing)
	MAVLinkSigningOff bool   // diagnostic only; production ห้ามปิด

	// MAVLinkSysID = System ID ที่ core ใช้ส่งออก **ต้องตรงกับ SYSID_MYGCS ที่ FC**
	//
	// ArduPilot รับ RC_CHANNELS_OVERRIDE เฉพาะจาก GCS ที่ sysid ตรงกับ SYSID_MYGCS
	// (`handle_rc_channels_override`: `if (msg.sysid != sysid_my_gcs()) return;`)
	// ถ้าไม่ตรง คำสั่งถูกทิ้งเงียบ ๆ ไม่มี error กลับมาเลย
	//
	// 255 คือค่ามาตรฐานของ GCS ทั่วไป (Mission Planner ฯลฯ) แต่รีโมท/แอปบางตัว
	// ตั้ง SYSID_MYGCS เป็นค่าอื่น เช่น G12 + MX330 ใช้ 250 — ต้องตั้งให้ตรงกัน
	MAVLinkSysID byte

	// ── Safety envelope (บังคับที่ core) ──
	MaxAltM          float64 // เพดานความสูง (m)
	MaxRadiusM       float64 // ระยะไกลสุดจาก GCS (m)
	MinArmBatteryPct float64 // แบตขั้นต่ำก่อน arm/takeoff
	MinSatCount      int     // ดาวเทียมขั้นต่ำก่อน arm
	MinSeparationM   float64 // ระยะห่างขั้นต่ำระหว่างโดรน (m)
	MaxSpeedMS       float64 // ความเร็วสูงสุด (m/s)
	GCSLat           float64 // ตำแหน่ง home/GCS สำหรับ radius + swarm return
	GCSLon           float64
	HomeExplicit     bool // true เมื่อผู้ใช้ตั้ง SWARMGOD_HOME_LOC เอง

	// ── Failsafe ──
	LinkWarnSec     float64 // ไม่ได้ telemetry นานเท่านี้ → WARN
	LinkLostSec     float64 // ไม่ได้ telemetry นานเท่านี้ → failsafe RTL
	BattFailsafePct float64 // แบตต่ำกว่านี้ (ขณะ armed) → failsafe RTL

	// ── Telemetry ──
	TelemetryHz float64 // อัตราสูงสุดที่ stream ไป cockpit ต่อลำ

	// ── Paths ──
	AuditDir string // โฟลเดอร์ audit log (append-only)
	LogDir   string
	DBPath   string // ไฟล์ SQLite (users/registry/sessions/audit index) — spec §3.4

	// ── Profile ──
	Profile string // sitl | setup | hil | production (spec §17)
}

// Default = ค่าปลอดภัยตั้งต้น (ตรงกับที่ระบุใน SECURITY.md)
func Default() Config {
	return Config{
		GRPCAddr:         "127.0.0.1:50051",
		TLSCert:          "../certs/server.crt",
		TLSKey:           "../certs/server.key",
		CACert:           "../certs/ca.crt",
		MAVLinkSignKey:   "../certs/mavlink_key",
		MAVLinkSysID:     255,
		MaxAltM:          120.0,
		MaxRadiusM:       500.0,
		MinArmBatteryPct: 25.0,
		MinSatCount:      6,
		MinSeparationM:   5.0,
		MaxSpeedMS:       15.0,
		// ตรงกับตำแหน่ง SITL เริ่มต้นใน launcher; production ต้อง override
		// ผ่าน SWARMGOD_HOME_LOC ก่อนเริ่มระบบ
		GCSLat:          14.9581695,
		GCSLon:          102.0986187,
		LinkWarnSec:     3.0,
		LinkLostSec:     10.0,
		BattFailsafePct: 15.0,
		TelemetryHz:     10.0,
		AuditDir:        "logs/audit",
		LogDir:          "logs",
		DBPath:          "logs/swarmgod.db",
		Profile:         "sitl",
	}
}

// Load = เริ่มจาก Default แล้ว override ด้วย env (SWARMGOD_*)
func Load() Config {
	c := Default()
	if v := os.Getenv("SWARMGOD_GRPC_ADDR"); v != "" {
		c.GRPCAddr = v
	}
	if v := envFloat("SWARMGOD_MAX_ALT"); v > 0 {
		c.MaxAltM = v
	}
	if v := envFloat("SWARMGOD_MAX_RADIUS"); v > 0 {
		c.MaxRadiusM = v
	}
	if v := envFloat("SWARMGOD_TELEMETRY_HZ"); v > 0 {
		c.TelemetryHz = v
	}
	if v := os.Getenv("SWARMGOD_HOME_LOC"); v != "" {
		if lat, lon, ok := parseHome(v); ok {
			c.GCSLat, c.GCSLon = lat, lon
			c.HomeExplicit = true
		}
	}
	if s := os.Getenv("SWARMGOD_MIN_ARM_BATTERY"); s != "" {
		if f, err := strconv.ParseFloat(s, 64); err == nil && f >= 0 {
			c.MinArmBatteryPct = f
		}
	}
	if v := os.Getenv("SWARMGOD_MAVLINK_KEY"); v != "" {
		c.MAVLinkSignKey = v
	}
	if os.Getenv("SWARMGOD_MAVLINK_STRICT") == "1" {
		c.MAVLinkSignStrict = true
	}
	switch strings.ToLower(strings.TrimSpace(os.Getenv("SWARMGOD_MAVLINK_SIGNING"))) {
	case "off", "0", "false":
		c.MAVLinkSigningOff = true
	}
	if s := os.Getenv("SWARMGOD_MAVLINK_SYSID"); s != "" {
		if v, err := strconv.Atoi(s); err == nil && v >= 1 && v <= 255 {
			c.MAVLinkSysID = byte(v)
		}
	}
	if v := os.Getenv("SWARMGOD_DB"); v != "" {
		c.DBPath = v
	}
	if v := os.Getenv("SWARMGOD_PROFILE"); v != "" {
		c.Profile = v
	}
	return c
}

// Validate ตรวจเงื่อนไขที่ต้อง fail closed ก่อนเปิดรับคำสั่ง
func (c Config) Validate() error {
	profile := strings.ToLower(strings.TrimSpace(c.Profile))
	switch profile {
	case "sitl", "setup", "hil", "production":
	default:
		return fmt.Errorf("unknown SWARMGOD_PROFILE %q (want sitl, setup, hil, or production)", c.Profile)
	}
	// setup is the real-aircraft telemetry-only bootstrap profile. It deliberately
	// allows Core/UI startup before a field Home is known; flight-mutating RPCs
	// and background command writers are blocked elsewhere until Core restarts in
	// hil/production with an explicit SWARMGOD_HOME_LOC.
	if profile != "sitl" && profile != "setup" && !c.HomeExplicit {
		return fmt.Errorf("%s requires explicit SWARMGOD_HOME_LOC (lat,lon,alt,heading)", profile)
	}
	if profile == "production" {
		if c.MAVLinkSigningOff {
			return fmt.Errorf("production forbids SWARMGOD_MAVLINK_SIGNING=off")
		}
		if !c.MAVLinkSignStrict {
			return fmt.Errorf("production requires SWARMGOD_MAVLINK_STRICT=1")
		}
		key, err := os.ReadFile(c.MAVLinkSignKey)
		if err != nil {
			return fmt.Errorf("production requires readable MAVLink signing key: %w", err)
		}
		if len(key) < 32 {
			return fmt.Errorf("production MAVLink signing key must contain at least 32 bytes")
		}
	}
	return nil
}

// TelemetryOnly is true only for the real-aircraft setup bootstrap profile.
// Callers use it to suppress every background/automatic FC write while still
// allowing the operator to connect and inspect telemetry before choosing Home.
func (c Config) TelemetryOnly() bool {
	return strings.EqualFold(strings.TrimSpace(c.Profile), "setup")
}

// Snapshot คืนค่า config เป็น map[string]string สำหรับคำนวณ config hash (spec §17)
func (c Config) Snapshot() map[string]string {
	return map[string]string{
		"grpc_addr":           c.GRPCAddr,
		"mavlink_sign_strict": strconv.FormatBool(c.MAVLinkSignStrict),
		"mavlink_signing_off": strconv.FormatBool(c.MAVLinkSigningOff),
		"max_alt_m":           strconv.FormatFloat(c.MaxAltM, 'f', -1, 64),
		"max_radius_m":        strconv.FormatFloat(c.MaxRadiusM, 'f', -1, 64),
		"min_arm_battery_pct": strconv.FormatFloat(c.MinArmBatteryPct, 'f', -1, 64),
		"min_sat_count":       strconv.Itoa(c.MinSatCount),
		"min_separation_m":    strconv.FormatFloat(c.MinSeparationM, 'f', -1, 64),
		"max_speed_ms":        strconv.FormatFloat(c.MaxSpeedMS, 'f', -1, 64),
		"gcs_lat":             strconv.FormatFloat(c.GCSLat, 'f', -1, 64),
		"gcs_lon":             strconv.FormatFloat(c.GCSLon, 'f', -1, 64),
		"link_warn_sec":       strconv.FormatFloat(c.LinkWarnSec, 'f', -1, 64),
		"link_lost_sec":       strconv.FormatFloat(c.LinkLostSec, 'f', -1, 64),
		"batt_failsafe_pct":   strconv.FormatFloat(c.BattFailsafePct, 'f', -1, 64),
		"telemetry_hz":        strconv.FormatFloat(c.TelemetryHz, 'f', -1, 64),
		"profile":             c.Profile,
	}
}

// parseHome รับรูปแบบเดียวกับ launcher/ArduPilot: lat,lon[,alt,heading]
func parseHome(raw string) (lat, lon float64, ok bool) {
	parts := strings.Split(raw, ",")
	if len(parts) < 2 {
		return 0, 0, false
	}
	lat, errLat := strconv.ParseFloat(strings.TrimSpace(parts[0]), 64)
	lon, errLon := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
	if errLat != nil || errLon != nil || math.IsNaN(lat) || math.IsNaN(lon) ||
		math.IsInf(lat, 0) || math.IsInf(lon, 0) || lat < -90 || lat > 90 ||
		lon < -180 || lon > 180 || (lat == 0 && lon == 0) {
		return 0, 0, false
	}
	return lat, lon, true
}

func envFloat(key string) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return 0
}
