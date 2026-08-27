package store

import (
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

var (
	ErrSessionInvalid = errors.New("store: session invalid or expired")
)

// Session = ผลลัพธ์การ login (spec §15) — เก็บเฉพาะ hash ของ token ใน DB
type Session struct {
	ID      string
	UserID  int64
	Token   string // token ดิบ (คืนครั้งเดียวตอนสร้าง; ไม่ถูกเก็บใน DB)
	Expires time.Time
}

// randToken สร้าง opaque token (32 bytes base64url)
func randToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func hashToken(tok string) string {
	sum := sha256.Sum256([]byte(tok))
	return hex.EncodeToString(sum[:])
}

// CreateSession ออก session ใหม่ให้ user (อายุ ttl)
func (s *Store) CreateSession(userID int64, ttl time.Duration) (*Session, error) {
	id, err := randToken()
	if err != nil {
		return nil, err
	}
	tok, err := randToken()
	if err != nil {
		return nil, err
	}
	now := nowUTC()
	exp := now.Add(ttl)
	_, err = s.exec(`
INSERT INTO sessions(id, user_id, token_hash, created_at, expires_at, revoked)
VALUES(?,?,?,?,?,0)`,
		id, userID, hashToken(tok),
		now.Format(time.RFC3339Nano), exp.Format(time.RFC3339Nano),
	)
	if err != nil {
		return nil, err
	}
	return &Session{ID: id, UserID: userID, Token: tok, Expires: exp}, nil
}

// ValidateSession ตรวจ (id, token) — คืน userID ถ้ายัง valid, ไม่ revoked, ไม่หมดอายุ
func (s *Store) ValidateSession(id, token string) (int64, error) {
	var (
		userID    int64
		tokenHash string
		expires   string
		revoked   int
	)
	err := s.db.QueryRow(
		`SELECT user_id, token_hash, expires_at, revoked FROM sessions WHERE id = ?`, id,
	).Scan(&userID, &tokenHash, &expires, &revoked)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, ErrSessionInvalid
	}
	if err != nil {
		return 0, err
	}
	if revoked != 0 || hashToken(token) != tokenHash {
		return 0, ErrSessionInvalid
	}
	exp, _ := time.Parse(time.RFC3339Nano, expires)
	if nowUTC().After(exp) {
		return 0, ErrSessionInvalid
	}
	return userID, nil
}

// Bearer คืน token รวม "id.token" สำหรับใส่ใน gRPC metadata (authorization: Bearer <...>)
func (s *Session) Bearer() string {
	return s.ID + "." + s.Token
}

// ValidateBearer แยก "id.token" แล้วตรวจ session — คืน userID ถ้า valid
// (id/token เป็น base64url ไม่มี '.' → แยกที่ '.' ตัวแรกได้ปลอดภัย)
func (s *Store) ValidateBearer(bearer string) (int64, error) {
	id, token, found := strings.Cut(bearer, ".")
	if !found {
		return 0, ErrSessionInvalid
	}
	return s.ValidateSession(id, token)
}

// RevokeSession เพิกถอน session (logout) — idempotent
func (s *Store) RevokeSession(id string) error {
	_, err := s.exec(`UPDATE sessions SET revoked = 1 WHERE id = ?`, id)
	return err
}

// PurgeExpiredSessions ลบ session ที่หมดอายุแล้ว คืนจำนวนที่ลบ (housekeeping)
func (s *Store) PurgeExpiredSessions() (int64, error) {
	r, err := s.exec(`DELETE FROM sessions WHERE expires_at < ?`,
		nowUTC().Format(time.RFC3339Nano))
	if err != nil {
		return 0, err
	}
	return r.RowsAffected()
}
