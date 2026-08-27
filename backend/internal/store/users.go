package store

import (
	"crypto/rand"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"time"

	"golang.org/x/crypto/argon2"
)

// ── Errors ที่ caller แยกเคสได้ ──
var (
	ErrUserExists   = errors.New("store: username already exists")
	ErrAuthFailed   = errors.New("store: invalid username or password")
	ErrUserDisabled = errors.New("store: user disabled")
	ErrWeakPassword = errors.New("store: password too short (min 8 chars)")
)

// User = แถวในตาราง users (ไม่รวม pw_hash)
type User struct {
	ID       int64
	Username string
	Role     string
	Disabled bool
	Created  time.Time
}

// ── Argon2id parameters (spec §15) ──
// ค่าตาม OWASP baseline; ปรับได้ถ้า benchmark hardware แล้ว
const (
	argonTime    = 3
	argonMemory  = 64 * 1024 // 64 MiB
	argonThreads = 4
	argonKeyLen  = 32
	argonSaltLen = 16
)

// hashPassword คืน PHC string เช่น
//
//	$argon2id$v=19$m=65536,t=3,p=4$<salt-b64>$<hash-b64>
func hashPassword(password string) (string, error) {
	salt := make([]byte, argonSaltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key := argon2.IDKey([]byte(password), salt, argonTime, argonMemory, argonThreads, argonKeyLen)
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version, argonMemory, argonTime, argonThreads,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key),
	), nil
}

// verifyPassword ตรวจ password กับ PHC string แบบ constant-time
func verifyPassword(password, encoded string) bool {
	parts := strings.Split(encoded, "$")
	// ["", "argon2id", "v=19", "m=..,t=..,p=..", salt, hash]
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false
	}
	var version int
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil || version != argon2.Version {
		return false
	}
	var mem uint32
	var t uint32
	var p uint8
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &mem, &t, &p); err != nil {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return false
	}
	got := argon2.IDKey([]byte(password), salt, t, mem, p, uint32(len(want)))
	return subtle.ConstantTimeCompare(got, want) == 1
}

// CreateUser สร้าง user ใหม่ (hash ด้วย Argon2id) — ไม่มี default password (spec §15)
func (s *Store) CreateUser(username, password, role string) (*User, error) {
	if len(password) < 8 {
		return nil, ErrWeakPassword
	}
	if role == "" {
		role = "operator"
	}
	hash, err := hashPassword(password)
	if err != nil {
		return nil, err
	}
	now := nowUTC()
	r, err := s.exec(
		`INSERT INTO users(username, pw_hash, role, created_at) VALUES(?,?,?,?)`,
		username, hash, role, now.Format(time.RFC3339Nano),
	)
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			return nil, ErrUserExists
		}
		return nil, err
	}
	id, _ := r.LastInsertId()
	return &User{ID: id, Username: username, Role: role, Created: now}, nil
}

// Authenticate ตรวจ username/password — คืน User ถ้าผ่าน
// หมายเหตุ: เพื่อกัน user-enumeration/timing, verify hash ปลอมเสมอเมื่อไม่พบ user
func (s *Store) Authenticate(username, password string) (*User, error) {
	var (
		u       User
		pwHash  string
		created string
		dis     int
	)
	err := s.db.QueryRow(
		`SELECT id, username, pw_hash, role, disabled, created_at FROM users WHERE username = ?`,
		username,
	).Scan(&u.ID, &u.Username, &pwHash, &u.Role, &dis, &created)

	if errors.Is(err, sql.ErrNoRows) {
		// ทำงานเทียบเท่ากรณีพบ user เพื่อลด timing signal
		verifyPassword(password, dummyHash)
		return nil, ErrAuthFailed
	}
	if err != nil {
		return nil, err
	}
	if !verifyPassword(password, pwHash) {
		return nil, ErrAuthFailed
	}
	if dis != 0 {
		return nil, ErrUserDisabled
	}
	u.Disabled = false
	u.Created, _ = time.Parse(time.RFC3339Nano, created)
	return &u, nil
}

// dummyHash ใช้ burn เวลาเมื่อ user ไม่มีจริง (password = "x", ค่าคงที่)
var dummyHash, _ = hashPassword("timing-equalizer-not-a-real-secret")

// GetUser ดึง user ตาม username
func (s *Store) GetUser(username string) (*User, error) {
	var (
		u       User
		created string
		dis     int
	)
	err := s.db.QueryRow(
		`SELECT id, username, role, disabled, created_at FROM users WHERE username = ?`,
		username,
	).Scan(&u.ID, &u.Username, &u.Role, &dis, &created)
	if err != nil {
		return nil, err
	}
	u.Disabled = dis != 0
	u.Created, _ = time.Parse(time.RFC3339Nano, created)
	return &u, nil
}

// CountUsers ใช้เช็คว่าระบบยังไม่มี user (สำหรับ bootstrap warning)
func (s *Store) CountUsers() (int, error) {
	var n int
	err := s.db.QueryRow(`SELECT COUNT(*) FROM users`).Scan(&n)
	return n, err
}
