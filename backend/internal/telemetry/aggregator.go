// Package telemetry — fan-out snapshot ไปยัง subscriber (gRPC streams)
// แทน TelemetryBridge เดิม: manager tick → Broadcast → ทุก subscriber
package telemetry

import (
	"sync"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// Aggregator กระจาย Telemetry ไปยัง subscriber แบบ non-blocking
// (ถ้า subscriber ช้า → drop frame ล่าสุดทิ้ง ไม่บล็อกทั้งระบบ)
type Aggregator struct {
	mu   sync.Mutex
	subs map[int]chan *pb.Telemetry
	next int
}

func New() *Aggregator {
	return &Aggregator{subs: make(map[int]chan *pb.Telemetry)}
}

// Subscribe คืน id + channel (buffered) — เรียก Unsubscribe เมื่อเลิก
func (a *Aggregator) Subscribe() (int, <-chan *pb.Telemetry) {
	a.mu.Lock()
	defer a.mu.Unlock()
	id := a.next
	a.next++
	ch := make(chan *pb.Telemetry, 256)
	a.subs[id] = ch
	return id, ch
}

func (a *Aggregator) Unsubscribe(id int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if ch, ok := a.subs[id]; ok {
		delete(a.subs, id)
		close(ch)
	}
}

// Broadcast ส่ง telemetry ไปทุก subscriber (drop ถ้า buffer เต็ม)
func (a *Aggregator) Broadcast(t *pb.Telemetry) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, ch := range a.subs {
		select {
		case ch <- t:
		default: // subscriber ช้า — ข้าม frame นี้ (rate protection)
		}
	}
}

func (a *Aggregator) SubscriberCount() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return len(a.subs)
}
