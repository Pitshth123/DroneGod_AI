// Package events — event/alarm bus (fan-out ไป cockpit ผ่าน SubscribeEvents)
// ใช้โดย failsafe, safety reject, swarm failover
package events

import (
	"log"
	"sync"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

type Bus struct {
	mu   sync.Mutex
	subs map[int]chan *pb.Event
	next int
}

func New() *Bus { return &Bus{subs: make(map[int]chan *pb.Event)} }

func (b *Bus) Subscribe() (int, <-chan *pb.Event) {
	b.mu.Lock()
	defer b.mu.Unlock()
	id := b.next
	b.next++
	ch := make(chan *pb.Event, 64)
	b.subs[id] = ch
	return id, ch
}

func (b *Bus) Unsubscribe(id int) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if ch, ok := b.subs[id]; ok {
		delete(b.subs, id)
		close(ch)
	}
}

// Publish สร้าง event + fan-out (non-blocking) + log
func (b *Bus) Publish(level pb.EventLevel, droneID uint32, category, message string) {
	ev := &pb.Event{
		Level: level, DroneId: droneID, Category: category,
		Message: message, TimestampMs: time.Now().UnixMilli(),
	}
	if level >= pb.EventLevel_EVENT_LEVEL_WARN {
		log.Printf("[event:%s] UAV_%d %s: %s", level, droneID, category, message)
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, ch := range b.subs {
		select {
		case ch <- ev:
		default:
		}
	}
}
