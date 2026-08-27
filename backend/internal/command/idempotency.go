package command

import (
	"sync"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// idemStore = เก็บผลของ request ที่ประมวลผลแล้ว ตาม retention window (spec §8.2)
//
// key = "<requestID>#<droneID>" (child key ต่อโดรน) — group request ใช้ parent requestID
// เดียวกัน แต่แยก child ต่อโดรน จึง dedup ได้ถูกลำ
//
// ถ้า client ส่ง requestID ว่าง = ไม่ dedup (backward compatible)
type idemStore struct {
	mu        sync.Mutex
	entries   map[string]idemEntry
	retention time.Duration
	lastSweep time.Time
}

type idemEntry struct {
	result  *pb.CommandResult
	expires time.Time
}

func newIdemStore(retention time.Duration) *idemStore {
	if retention <= 0 {
		retention = 60 * time.Second
	}
	return &idemStore{
		entries:   make(map[string]idemEntry),
		retention: retention,
		lastSweep: time.Now(),
	}
}

// do รัน fn ครั้งเดียวต่อ key ภายใน retention window:
//   - requestID ว่าง → รัน fn ตรง ๆ ไม่ cache
//   - key ซ้ำและยังไม่หมดอายุ → คืนผลเดิม (ไม่ยิงคำสั่งซ้ำ)
//
// หมายเหตุ concurrency: ใช้ per-key "in-flight" guard กัน 2 request พร้อมกันด้วย key เดียว
// ยิงคำสั่งซ้อน — ตัวที่สองรอผลตัวแรก
// do คืน (result, replay) — replay=true ถ้าเป็นผลจาก cache (ไม่ได้ยิงคำสั่งจริง)
func (s *idemStore) do(requestID string, droneID uint32, fn func() *pb.CommandResult) (*pb.CommandResult, bool) {
	if requestID == "" {
		return fn(), false // ไม่มี id = ไม่ dedup
	}
	key := requestID + "#" + utoa(droneID)

	s.mu.Lock()
	s.sweepLocked()
	if e, ok := s.entries[key]; ok && time.Now().Before(e.expires) {
		if e.result != nil { // ผลพร้อมแล้ว → คืนเลย
			s.mu.Unlock()
			return dupResult(e.result), true
		}
		// กำลังประมวลผลอยู่ (result=nil) → ปล่อย lock แล้วรอสั้น ๆ
		s.mu.Unlock()
		return s.waitInflight(key, fn, droneID, requestID)
	}
	// จอง slot (in-flight)
	s.entries[key] = idemEntry{result: nil, expires: time.Now().Add(s.retention)}
	s.mu.Unlock()

	res := fn()

	s.mu.Lock()
	s.entries[key] = idemEntry{result: res, expires: time.Now().Add(s.retention)}
	s.mu.Unlock()
	return res, false
}

// waitInflight รอผลของ request ที่ key เดียวกันซึ่งกำลังประมวลผล (poll สั้น ๆ, มี deadline)
func (s *idemStore) waitInflight(key string, fn func() *pb.CommandResult, droneID uint32, requestID string) (*pb.CommandResult, bool) {
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
		s.mu.Lock()
		e, ok := s.entries[key]
		if ok && e.result != nil {
			s.mu.Unlock()
			return dupResult(e.result), true
		}
		if !ok { // slot หายไป (หมดอายุ/ถูก sweep) → เริ่มใหม่
			s.mu.Unlock()
			return s.do(requestID, droneID, fn)
		}
		s.mu.Unlock()
	}
	// รอนานเกิน → ยอมยิงเอง (กัน deadlock)
	return fn(), false
}

// sweepLocked ลบ entry หมดอายุ (เรียกภายใต้ lock; throttle ทุก ~retention)
func (s *idemStore) sweepLocked() {
	now := time.Now()
	if now.Sub(s.lastSweep) < s.retention {
		return
	}
	s.lastSweep = now
	for k, e := range s.entries {
		if now.After(e.expires) {
			delete(s.entries, k)
		}
	}
}

// dupResult clone result + ทำเครื่องหมายว่าเป็นผล cache (idempotent replay)
func dupResult(r *pb.CommandResult) *pb.CommandResult {
	if r == nil {
		return nil
	}
	msg := r.Message
	if msg != "" {
		msg += " (idempotent replay)"
	}
	return &pb.CommandResult{
		Ok: r.Ok, DroneId: r.DroneId, Command: r.Command,
		ResultCode: r.ResultCode, Message: msg, Outcome: r.Outcome,
	}
}

func utoa(n uint32) string {
	if n == 0 {
		return "0"
	}
	var b [10]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
