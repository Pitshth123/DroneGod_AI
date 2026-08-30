package command

import (
	"sync"
	"time"

	"google.golang.org/protobuf/proto"

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
	result   *pb.CommandResult
	expires  time.Time
	done     chan struct{}
	complete bool
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

// do runs fn exactly once per key within the retention window:
//   - empty requestID: no dedup, run fn directly
//   - completed key: replay the cached result
//   - in-flight key: wait for the ORIGINAL execution to finish; never start a
//     second fn merely because an arbitrary wall-clock timeout elapsed
//
// The in-flight entry owns a completion channel.  This is important for long
// commands such as Takeoff (up to ~90 s): a same-request retry must not turn into
// a second execution or a self-BUSY result after 10 s.  If the original panics,
// the slot is deleted and waiters are released so the key cannot deadlock
// permanently; a waiter may then become the next real attempt.
func (s *idemStore) do(requestID string, droneID uint32, fn func() *pb.CommandResult) (*pb.CommandResult, bool) {
	if requestID == "" {
		return fn(), false
	}
	key := requestID + "#" + utoa(droneID)

	for {
		now := time.Now()
		s.mu.Lock()
		s.sweepLocked()
		if e, ok := s.entries[key]; ok {
			if e.complete {
				if now.Before(e.expires) {
					res := dupResult(e.result)
					s.mu.Unlock()
					return res, true
				}
				delete(s.entries, key)
			} else {
				done := e.done
				s.mu.Unlock()
				<-done
				// The original either completed (next loop replays it) or panicked
				// and removed the slot (next loop safely elects a new owner).
				continue
			}
		}

		done := make(chan struct{})
		s.entries[key] = idemEntry{done: done}
		s.mu.Unlock()

		var res *pb.CommandResult
		var panicked any
		func() {
			defer func() {
				if p := recover(); p != nil {
					panicked = p
				}
			}()
			res = fn()
		}()

		s.mu.Lock()
		e, stillOwner := s.entries[key]
		if stillOwner && e.done == done {
			if panicked != nil {
				delete(s.entries, key)
			} else {
				e.result = res
				e.complete = true
				e.expires = time.Now().Add(s.retention)
				s.entries[key] = e
			}
			close(done)
		}
		s.mu.Unlock()

		if panicked != nil {
			panic(panicked)
		}
		return res, false
	}
}

// sweepLocked ลบ entry หมดอายุ (เรียกภายใต้ lock; throttle ทุก ~retention)
func (s *idemStore) sweepLocked() {
	now := time.Now()
	if now.Sub(s.lastSweep) < s.retention {
		return
	}
	s.lastSweep = now
	for k, e := range s.entries {
		// Never sweep an in-flight owner: waiters are blocked on e.done and the
		// original execution is the only command allowed to complete this key.
		if e.complete && now.After(e.expires) {
			delete(s.entries, k)
		}
	}
}

// dupResult clone result + ทำเครื่องหมายว่าเป็นผล cache (idempotent replay)
func dupResult(r *pb.CommandResult) *pb.CommandResult {
	if r == nil {
		return nil
	}
	// Preserve the complete protobuf result shape (including request_id and
	// per-drone aggregate detail) without copying protoimpl.MessageState locks.
	// Only the human-readable message is annotated as a replay.
	out, ok := proto.Clone(r).(*pb.CommandResult)
	if !ok || out == nil {
		return nil
	}
	if out.Message != "" {
		out.Message += " (idempotent replay)"
	}
	return out
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
