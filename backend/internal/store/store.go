// Package store — persistence layer ของ SwarmGod (SQLite + WAL)
//
// เก็บ: users, vehicle registry, sessions, flight sessions และ audit index
// (ดู master spec §3.4 Storage, §16 Audit/Flight Session)
//
// กฎเหล็ก (spec §5.9, §3.4): DB ห้ามอยู่ใน flight-command critical path
//   - audit ตัวจริงยังเขียน JSONL แบบ append-only (ดู package audit)
//   - store เป็นแค่ index/query layer; การเขียน audit index ทำแบบ async non-blocking
//   - ถ้า DB ช้า/ล่ม flight loop ต้องไม่ค้าง
package store

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite" // pure-Go SQLite driver (ไม่ต้องใช้ cgo)
)

// Store เป็นเจ้าของ *sql.DB เดียว (SQLite serialize เขียนเองผ่าน WAL)
type Store struct {
	db  *sql.DB
	idx *AsyncIndexer
}

// Open เปิด (หรือสร้าง) ไฟล์ DB, ตั้ง WAL/pragmas ที่ปลอดภัย แล้วรัน migrations
//
// path เป็น ":memory:" ได้สำหรับเทสต์ (ใช้ cache=shared เพื่อให้ pool เห็น DB เดียวกัน)
func Open(path string) (*Store, error) {
	dsn := path
	if path == ":memory:" {
		dsn = "file::memory:?cache=shared"
	}
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("store: open: %w", err)
	}
	// SQLite เขียนได้ทีละ writer — จำกัด pool ให้ชัดเจนกัน "database is locked"
	// ":memory:" ต้องมี conn เดียวไม่งั้นแต่ละ conn เห็นคนละ DB
	if path == ":memory:" {
		db.SetMaxOpenConns(1)
	} else {
		db.SetMaxOpenConns(4)
	}
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(time.Hour)

	pragmas := []string{
		"PRAGMA journal_mode = WAL",   // อ่าน/เขียนพร้อมกันได้ (spec §3.4)
		"PRAGMA synchronous = NORMAL", // ทน crash พอเพียงใน WAL, เร็วกว่า FULL
		"PRAGMA foreign_keys = ON",
		"PRAGMA busy_timeout = 5000", // รอ lock สูงสุด 5s แทนที่จะ error ทันที
	}
	for _, p := range pragmas {
		if _, err := db.Exec(p); err != nil {
			db.Close()
			return nil, fmt.Errorf("store: pragma %q: %w", p, err)
		}
	}

	s := &Store{db: db}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// DB คืน *sql.DB ตรง ๆ (สำหรับ query เฉพาะทาง/เทสต์)
func (s *Store) DB() *sql.DB { return s.db }

// Close ปิด indexer (ถ้ามี) แล้วปิด DB
func (s *Store) Close() error {
	if s.idx != nil {
		s.idx.Close()
	}
	return s.db.Close()
}

// nowUTC = wall clock สำหรับ audit/persistence (spec §5.7: audit ใช้ wall clock)
func nowUTC() time.Time { return time.Now().UTC() }

// ── helper: exec ที่มี context timeout สั้น ๆ กัน query ค้างถาวร ──
func (s *Store) exec(query string, args ...any) (sql.Result, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return s.db.ExecContext(ctx, query, args...)
}
