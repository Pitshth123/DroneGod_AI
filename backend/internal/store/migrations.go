package store

import (
	"context"
	"fmt"
	"time"
)

// migration = SQL ก้อนหนึ่งที่รันครั้งเดียว, ระบุด้วย version ที่เพิ่มขึ้นเรื่อย ๆ
// ห้ามแก้ SQL ของ version เดิมที่ปล่อยไปแล้ว — ให้เพิ่ม version ใหม่ต่อท้าย
type migration struct {
	version int
	name    string
	sql     string
}

// migrations เรียงตาม version จากน้อยไปมาก
var migrations = []migration{
	{
		version: 1,
		name:    "initial_schema",
		sql: `
CREATE TABLE users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL UNIQUE,
    pw_hash    TEXT    NOT NULL,           -- PHC string (Argon2id) — spec §15, ไม่มี default password
    role       TEXT    NOT NULL DEFAULT 'operator',
    disabled   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);

-- Vehicle registry: vehicle_uuid = ตัวตนถาวร (spec §6.1)
CREATE TABLE vehicles (
    vehicle_uuid    TEXT PRIMARY KEY,
    mav_system_id   INTEGER NOT NULL,
    mav_component_id INTEGER NOT NULL DEFAULT 1,
    display_name    TEXT    NOT NULL,
    hardware_serial TEXT,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL
);
CREATE INDEX idx_vehicles_sysid ON vehicles(mav_system_id);

CREATE TABLE sessions (
    id         TEXT    PRIMARY KEY,        -- session id (opaque)
    user_id    INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT    NOT NULL,           -- เก็บ hash ของ token ไม่เก็บ token ดิบ
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

-- Flight Session: บริบทของการบิน/ทดสอบหนึ่งครั้ง (spec §16.2)
CREATE TABLE flight_sessions (
    id            TEXT PRIMARY KEY,
    operator      TEXT NOT NULL,
    core_version  TEXT NOT NULL,
    proto_version TEXT NOT NULL,
    config_hash   TEXT NOT NULL,           -- hash ของ config snapshot (spec §17)
    profile       TEXT NOT NULL DEFAULT 'sitl',
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    notes         TEXT
);

-- Audit index: ดัชนีสำหรับ query เท่านั้น — บันทึกฉบับเต็มอยู่ใน JSONL (spec §16.1)
-- เขียนแบบ async (ดู audit_index.go) เพื่อไม่ให้ DB บล็อก command path
CREATE TABLE audit_index (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT    NOT NULL,
    flight_session_id TEXT,
    entry_type        TEXT    NOT NULL,     -- "command" | "event"
    drone_id          INTEGER,
    command           TEXT,
    category          TEXT,
    allowed           INTEGER,
    reason            TEXT,
    result_code       INTEGER
);
CREATE INDEX idx_audit_ts       ON audit_index(ts);
CREATE INDEX idx_audit_drone    ON audit_index(drone_id);
CREATE INDEX idx_audit_session  ON audit_index(flight_session_id);
`,
	},
	{
		version: 2,
		name:    "audit_request_id",
		// request/parent ID ใน audit trail (spec §16.1)
		sql: `
ALTER TABLE audit_index ADD COLUMN request_id TEXT;
CREATE INDEX idx_audit_reqid ON audit_index(request_id);
`,
	},
}

// migrate รัน migration ที่ยังไม่ถูก apply (idempotent — รันซ้ำได้ไม่พัง)
func (s *Store) migrate() error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if _, err := s.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)`); err != nil {
		return fmt.Errorf("store: ensure schema_migrations: %w", err)
	}

	var current int
	// COALESCE กัน NULL ตอนตารางว่าง
	if err := s.db.QueryRowContext(ctx,
		`SELECT COALESCE(MAX(version), 0) FROM schema_migrations`).Scan(&current); err != nil {
		return fmt.Errorf("store: read migration version: %w", err)
	}

	for _, m := range migrations {
		if m.version <= current {
			continue
		}
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return fmt.Errorf("store: begin migration %d: %w", m.version, err)
		}
		if _, err := tx.ExecContext(ctx, m.sql); err != nil {
			tx.Rollback()
			return fmt.Errorf("store: apply migration %d (%s): %w", m.version, m.name, err)
		}
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)`,
			m.version, m.name, nowUTC().Format(time.RFC3339Nano)); err != nil {
			tx.Rollback()
			return fmt.Errorf("store: record migration %d: %w", m.version, err)
		}
		if err := tx.Commit(); err != nil {
			return fmt.Errorf("store: commit migration %d: %w", m.version, err)
		}
	}
	return nil
}

// SchemaVersion คืน version ล่าสุดที่ apply แล้ว (สำหรับ diagnostics/เทสต์)
func (s *Store) SchemaVersion() (int, error) {
	var v int
	err := s.db.QueryRow(`SELECT COALESCE(MAX(version), 0) FROM schema_migrations`).Scan(&v)
	return v, err
}
