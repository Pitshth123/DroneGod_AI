package store

import (
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"time"
)

// FlightSession = บริบทการบิน/ทดสอบหนึ่งครั้ง (spec §16.2)
type FlightSession struct {
	ID           string
	Operator     string
	CoreVersion  string
	ProtoVersion string
	ConfigHash   string
	Profile      string
	Started      time.Time
	Ended        *time.Time
	Notes        string
}

// ConfigHash คำนวณ hash เสถียรจาก config snapshot (spec §17: config ต้องมี hash)
// รับ map[string]string ที่ caller แปลงค่ามาแล้ว — เรียง key เพื่อให้ผลคงที่
func ConfigHash(fields map[string]string) string {
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	h := sha256.New()
	for _, k := range keys {
		h.Write([]byte(k))
		h.Write([]byte{0})
		h.Write([]byte(fields[k]))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))
}

// StartFlightSession เปิด session ใหม่ คืน id
func (s *Store) StartFlightSession(operator, coreVersion, protoVersion, configHash, profile string) (string, error) {
	id, err := randToken()
	if err != nil {
		return "", err
	}
	if profile == "" {
		profile = "sitl"
	}
	_, err = s.exec(`
INSERT INTO flight_sessions(id, operator, core_version, proto_version, config_hash, profile, started_at)
VALUES(?,?,?,?,?,?,?)`,
		id, operator, coreVersion, protoVersion, configHash, profile,
		nowUTC().Format(time.RFC3339Nano),
	)
	if err != nil {
		return "", err
	}
	return id, nil
}

// EndFlightSession ปิด session (ตั้ง ended_at + notes) — idempotent
func (s *Store) EndFlightSession(id, notes string) error {
	_, err := s.exec(
		`UPDATE flight_sessions SET ended_at = ?, notes = ? WHERE id = ? AND ended_at IS NULL`,
		nowUTC().Format(time.RFC3339Nano), notes, id,
	)
	return err
}

// GetFlightSession ดึง session ตาม id
func (s *Store) GetFlightSession(id string) (*FlightSession, error) {
	var (
		fs      FlightSession
		started string
		ended   *string
		notes   *string
	)
	err := s.db.QueryRow(`
SELECT id, operator, core_version, proto_version, config_hash, profile, started_at, ended_at, notes
FROM flight_sessions WHERE id = ?`, id,
	).Scan(&fs.ID, &fs.Operator, &fs.CoreVersion, &fs.ProtoVersion, &fs.ConfigHash,
		&fs.Profile, &started, &ended, &notes)
	if err != nil {
		return nil, err
	}
	fs.Started, _ = time.Parse(time.RFC3339Nano, started)
	if ended != nil {
		t, _ := time.Parse(time.RFC3339Nano, *ended)
		fs.Ended = &t
	}
	if notes != nil {
		fs.Notes = *notes
	}
	return &fs, nil
}
