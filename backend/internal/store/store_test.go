package store

import (
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// openTest เปิด store บนไฟล์ชั่วคราว (แยกไฟล์ต่อเทสต์ กัน :memory: shared-cache ปนกัน)
func openTest(t *testing.T) *Store {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.db")
	s, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

// STORE-DB-001: migrations idempotent — เปิดไฟล์เดิมซ้ำต้องไม่ error และ version คงเดิม
func TestMigrateIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "idem.db")
	s1, err := Open(path)
	if err != nil {
		t.Fatalf("open1: %v", err)
	}
	v1, _ := s1.SchemaVersion()
	s1.Close()

	s2, err := Open(path) // เปิดซ้ำ → migrate ต้อง skip ของเดิม
	if err != nil {
		t.Fatalf("open2 (idempotent): %v", err)
	}
	defer s2.Close()
	v2, _ := s2.SchemaVersion()

	if v1 != v2 || v1 != len(migrations) {
		t.Fatalf("schema version mismatch: v1=%d v2=%d want=%d", v1, v2, len(migrations))
	}
}

func TestWALEnabled(t *testing.T) {
	s := openTest(t)
	var mode string
	if err := s.db.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil {
		t.Fatalf("pragma: %v", err)
	}
	if mode != "wal" {
		t.Fatalf("journal_mode = %q, want wal", mode)
	}
}

// SEC-PW-001: Argon2id — สร้าง user, auth ถูก/ผิด, ห้าม duplicate, ห้ามรหัสสั้น
func TestUsersAuth(t *testing.T) {
	s := openTest(t)

	if _, err := s.CreateUser("alice", "short", "operator"); err != ErrWeakPassword {
		t.Fatalf("weak password: got %v, want ErrWeakPassword", err)
	}

	u, err := s.CreateUser("alice", "correct horse battery", "admin")
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	if u.Role != "admin" {
		t.Fatalf("role = %q", u.Role)
	}

	if _, err := s.CreateUser("alice", "another password here", "operator"); err != ErrUserExists {
		t.Fatalf("duplicate: got %v, want ErrUserExists", err)
	}

	// pw_hash ต้องเป็น PHC argon2id ไม่ใช่ plaintext
	var hash string
	s.db.QueryRow(`SELECT pw_hash FROM users WHERE username='alice'`).Scan(&hash)
	if len(hash) < 20 || hash[:9] != "$argon2id" {
		t.Fatalf("pw_hash not argon2id PHC: %q", hash)
	}

	got, err := s.Authenticate("alice", "correct horse battery")
	if err != nil {
		t.Fatalf("Authenticate ok case: %v", err)
	}
	if got.ID != u.ID {
		t.Fatalf("auth returned wrong user")
	}

	if _, err := s.Authenticate("alice", "wrong password"); err != ErrAuthFailed {
		t.Fatalf("wrong password: got %v, want ErrAuthFailed", err)
	}
	if _, err := s.Authenticate("nobody", "whatever here"); err != ErrAuthFailed {
		t.Fatalf("unknown user: got %v, want ErrAuthFailed", err)
	}
}

func TestResetUserPassword(t *testing.T) {
	s := openTest(t)
	u, err := s.CreateUser("alice", "original password", "operator")
	if err != nil {
		t.Fatal(err)
	}
	sess, err := s.CreateSession(u.ID, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.ResetUserPassword("alice", "replacement password"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Authenticate("alice", "original password"); err != ErrAuthFailed {
		t.Fatalf("old password: got %v, want ErrAuthFailed", err)
	}
	if _, err := s.Authenticate("alice", "replacement password"); err != nil {
		t.Fatalf("new password: %v", err)
	}
	parts := strings.Split(sess.Bearer(), ".")
	if _, err := s.ValidateSession(parts[0], parts[1]); err != ErrSessionInvalid {
		t.Fatalf("old session: got %v, want ErrSessionInvalid", err)
	}
}

// STORE-VEH-001: registry upsert idempotent, first_seen คงที่, list ได้
func TestVehiclesUpsert(t *testing.T) {
	s := openTest(t)
	if err := s.UpsertVehicle("uuid-1", 1, 1, "UAV_1", ""); err != nil {
		t.Fatalf("upsert1: %v", err)
	}
	vs, _ := s.ListVehicles()
	if len(vs) != 1 {
		t.Fatalf("len=%d want 1", len(vs))
	}
	first := vs[0].FirstSeen

	time.Sleep(5 * time.Millisecond)
	if err := s.UpsertVehicle("uuid-1", 2, 1, "UAV_1_renamed", "SN123"); err != nil {
		t.Fatalf("upsert2: %v", err)
	}
	vs, _ = s.ListVehicles()
	if len(vs) != 1 {
		t.Fatalf("upsert should not add row: len=%d", len(vs))
	}
	if vs[0].SystemID != 2 || vs[0].DisplayName != "UAV_1_renamed" || vs[0].Serial != "SN123" {
		t.Fatalf("upsert did not update fields: %+v", vs[0])
	}
	if !vs[0].FirstSeen.Equal(first) {
		t.Fatalf("first_seen changed on upsert: %v -> %v", first, vs[0].FirstSeen)
	}
	if !vs[0].LastSeen.After(first) {
		t.Fatalf("last_seen not advanced")
	}
}

// STORE-SESS-001: session create/validate/expire/revoke
func TestSessions(t *testing.T) {
	s := openTest(t)
	u, _ := s.CreateUser("bob", "password bob strong", "operator")

	sess, err := s.CreateSession(u.ID, time.Hour)
	if err != nil {
		t.Fatalf("CreateSession: %v", err)
	}
	uid, err := s.ValidateSession(sess.ID, sess.Token)
	if err != nil || uid != u.ID {
		t.Fatalf("validate: uid=%d err=%v", uid, err)
	}
	// token ผิด
	if _, err := s.ValidateSession(sess.ID, "wrong"); err != ErrSessionInvalid {
		t.Fatalf("wrong token: %v", err)
	}
	// revoke
	if err := s.RevokeSession(sess.ID); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	if _, err := s.ValidateSession(sess.ID, sess.Token); err != ErrSessionInvalid {
		t.Fatalf("revoked still valid")
	}

	// หมดอายุ
	expired, _ := s.CreateSession(u.ID, -time.Minute)
	if _, err := s.ValidateSession(expired.ID, expired.Token); err != ErrSessionInvalid {
		t.Fatalf("expired session validated")
	}
	n, err := s.PurgeExpiredSessions()
	if err != nil || n < 1 {
		t.Fatalf("purge: n=%d err=%v", n, err)
	}
}

// STORE-FS-001: flight session start/end + config hash เสถียร
func TestFlightSession(t *testing.T) {
	s := openTest(t)
	h1 := ConfigHash(map[string]string{"a": "1", "b": "2"})
	h2 := ConfigHash(map[string]string{"b": "2", "a": "1"}) // order ต่าง ต้อง hash เท่ากัน
	if h1 != h2 {
		t.Fatalf("ConfigHash not order-stable: %s != %s", h1, h2)
	}
	h3 := ConfigHash(map[string]string{"a": "1", "b": "3"})
	if h1 == h3 {
		t.Fatalf("ConfigHash collision on different values")
	}

	id, err := s.StartFlightSession("op1", "v0.1", "swarmgod.v1", h1, "sitl")
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	fs, err := s.GetFlightSession(id)
	if err != nil || fs.Ended != nil {
		t.Fatalf("get before end: fs=%+v err=%v", fs, err)
	}
	if err := s.EndFlightSession(id, "done"); err != nil {
		t.Fatalf("end: %v", err)
	}
	fs, _ = s.GetFlightSession(id)
	if fs.Ended == nil || fs.Notes != "done" {
		t.Fatalf("end not recorded: %+v", fs)
	}
}

// STORE-AUD-001: audit index เขียน async แล้ว query กลับได้
func TestAuditIndexRoundtrip(t *testing.T) {
	s := openTest(t)
	idx := s.NewAsyncIndexer(64)
	idx.SetFlightSession("fs-1")

	idx.Index(map[string]any{
		"type": "command", "drone_id": uint32(7), "command": "Arm",
		"allowed": true, "reason": "ok", "result_code": int32(0),
		"ts": nowUTC().Format(time.RFC3339Nano),
	})
	idx.Index(map[string]any{
		"type": "event", "category": "geofence", "reason": "set 4 vertices",
		"ts": nowUTC().Format(time.RFC3339Nano),
	})

	// รอ goroutine เขียนเสร็จ (poll สั้น ๆ)
	waitFor(t, func() bool { return idx.Written() >= 2 }, 2*time.Second)

	rows, err := s.QueryAudit(AuditFilter{Limit: 10})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("rows=%d want 2", len(rows))
	}
	// filter ตาม drone
	d := uint32(7)
	rows, _ = s.QueryAudit(AuditFilter{DroneID: &d})
	if len(rows) != 1 || rows[0].Command != "Arm" || rows[0].FlightSessionID != "fs-1" {
		t.Fatalf("drone filter wrong: %+v", rows)
	}
	if rows[0].Allowed == nil || !*rows[0].Allowed {
		t.Fatalf("allowed not decoded")
	}
}

// §16.1: request_id เขียนลง audit_index แล้ว query กลับได้
func TestAuditIndexRequestID(t *testing.T) {
	s := openTest(t)
	idx := s.NewAsyncIndexer(64)
	idx.Index(map[string]any{
		"type": "request", "drone_id": uint32(3), "command": "Takeoff",
		"request_id": "req-abc", "allowed": true, "reason": "OUTCOME_ACCEPTED",
		"ts": nowUTC().Format(time.RFC3339Nano),
	})
	waitFor(t, func() bool { return idx.Written() >= 1 }, 2*time.Second)

	rows, err := s.QueryAudit(AuditFilter{Limit: 10})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(rows) != 1 || rows[0].RequestID != "req-abc" {
		t.Fatalf("request_id ไม่ถูก index: %+v", rows)
	}
	if rows[0].Command != "Takeoff" || rows[0].Type != "request" {
		t.Fatalf("fields ผิด: %+v", rows[0])
	}
}

// SAFE-DB-001: หัวใจของ spec §5.9 — Index() ต้องไม่บล็อกแม้ buffer เต็ม
// ยิง entry ถล่มเกิน buffer ด้วย indexer ที่ goroutine ยังไม่ทันดึง → ต้องกลับมาเร็ว + มี dropped
func TestIndexerNeverBlocks(t *testing.T) {
	s := openTest(t)
	idx := s.NewAsyncIndexer(8) // buffer เล็ก

	// เขียนถล่ม 5000 entry — ถ้า Index บล็อก เทสต์นี้จะ timeout
	done := make(chan struct{})
	go func() {
		for i := 0; i < 5000; i++ {
			idx.Index(map[string]any{
				"type": "command", "drone_id": uint32(i), "command": "spam",
				"ts": nowUTC().Format(time.RFC3339Nano),
			})
		}
		close(done)
	}()

	select {
	case <-done:
		// ผ่าน: Index ไม่บล็อก
	case <-time.After(5 * time.Second):
		t.Fatal("Index() blocked — ละเมิด spec §5.9 (DB ห้ามอยู่ใน critical path)")
	}

	// ต้องมีบางส่วนถูกทิ้ง (buffer 8 << 5000) — พิสูจน์ว่า drop-on-full ทำงาน ไม่ใช่บล็อกรอ
	if idx.Dropped() == 0 {
		t.Fatalf("expected some dropped entries with tiny buffer, got 0")
	}
	t.Logf("dropped=%d written=%d (drop-on-full ok)", idx.Dropped(), idx.Written())
}

func waitFor(t *testing.T, cond func() bool, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("condition not met within %v", timeout)
}
