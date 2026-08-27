package fleet

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/common"
	"github.com/bluenviron/gomavlib/v3/pkg/message"
)

// fakeSender วัดว่ามี sendCmd กี่ตัว "อยู่ระหว่างส่ง" พร้อมกัน + ตอบ ACK ให้อัตโนมัติ
type fakeSender struct {
	d        *Drone
	inFlight atomic.Int32
	maxSeen  atomic.Int32
	sends    atomic.Int32
}

func (f *fakeSender) Send(msg message.Message) error {
	n := f.inFlight.Add(1)
	for { // อัปเดต maxSeen แบบ atomic
		m := f.maxSeen.Load()
		if n <= m || f.maxSeen.CompareAndSwap(m, n) {
			break
		}
	}
	f.sends.Add(1)
	time.Sleep(3 * time.Millisecond) // ถ่างช่วงเวลาให้เห็น concurrency ถ้ามี
	f.inFlight.Add(-1)

	// ตอบ ACK ให้คำสั่งนี้ (async — sendCmd ถือ lock รออยู่)
	if cl, ok := msg.(*common.MessageCommandLong); ok {
		go f.d.deliverAck(cl.Command, 0)
	}
	return nil
}

// SAFE-CMDSEQ-001: ยิง sendCmd ชนิดเดียวกันพร้อมกันหลายตัว (จำลอง user cmd + failsafe RTL)
// ต้อง serialize → ไม่มีช่วงที่ Send ซ้อนกัน (maxSeen == 1) และทุกคำสั่งได้ ACK ครบ
func TestSendCmdSerialized(t *testing.T) {
	d := newDrone(1, "UAV_1", "host", 5760, nil)
	f := &fakeSender{d: d}
	d.conn = f

	const N = 30
	var wg sync.WaitGroup
	var okCount atomic.Int32
	for i := 0; i < N; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			// ใช้ cmd ชนิดเดียวกันทุกตัว = เคสที่ pendingAcks[cmd] เคยชนกัน
			code, err := d.sendCmd(ctx, common.MAV_CMD_COMPONENT_ARM_DISARM, [7]float32{1})
			if err == nil && code == 0 {
				okCount.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := f.maxSeen.Load(); got != 1 {
		t.Fatalf("Send ซ้อนกัน %d ตัว — ไม่ได้ serialize (ละเมิด spec §8.2)", got)
	}
	if got := okCount.Load(); got != N {
		t.Fatalf("ได้ ACK ครบ %d/%d — บางคำสั่ง ACK หาย (pendingAcks ชนกัน)", got, N)
	}
	if got := f.sends.Load(); got != N {
		t.Fatalf("ส่ง %d ครั้ง คาดว่า %d", got, N)
	}
}
