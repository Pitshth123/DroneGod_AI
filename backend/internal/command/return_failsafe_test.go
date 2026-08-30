package command

import (
	"context"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/message"
	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/safety"
)

// The automated Return path must check the fleet failsafe latch before it can
// delegate to the ordinary RTL transport path. This source-order assertion
// complements fleet failsafe latch tests without constructing a live MAVLink FC.
func TestMissionReturnRTLChecksFailsafeBeforeRTL(t *testing.T) {
	src, err := os.ReadFile("service.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(src)
	start := strings.Index(text, "func (s *Service) MissionReturnRTL(")
	if start < 0 {
		t.Fatal("MissionReturnRTL missing")
	}
	body := text[start:]
	failsafe := strings.Index(body, "s.mgr.FailsafeActive(id)")
	finalGuard := strings.Index(body, "s.mgr.WithFailsafeSendGuard(ctx, id)")
	rtl := strings.Index(body, "return s.RTL(ctx, id)")
	if failsafe < 0 || finalGuard < 0 || rtl < 0 || !(failsafe < finalGuard && finalGuard < rtl) {
		t.Fatal("MissionReturnRTL must check failsafe precheck, then compose the atomic final-write guard, before RTL dispatch")
	}
}

// ── execution-level regressions for the generic post-mission Return TOCTOU ──

type countingSender struct {
	mu   sync.Mutex
	sent int
}

func (s *countingSender) Send(message.Message) error {
	s.mu.Lock()
	s.sent++
	s.mu.Unlock()
	return nil
}
func (s *countingSender) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sent
}

type signalingSender struct {
	once sync.Once
	sent chan struct{}
}

func (s *signalingSender) Send(message.Message) error {
	s.once.Do(func() { close(s.sent) })
	return nil
}

// barrierReturnGuard stands in for the Return lease/cancellation guard already on
// the batch context. It blocks the very first guarded transport write so a test
// can latch failsafe strictly between MissionReturnRTL's early precheck and the
// COMMAND_LONG write, then observe that the composed failsafe guard refuses it.
type barrierReturnGuard struct {
	once    sync.Once
	entered chan struct{}
	release chan struct{}
}

func (g *barrierReturnGuard) DoSend(send func() error) error {
	g.once.Do(func() { close(g.entered) })
	<-g.release
	return send()
}

// cancelledReturnGuard stands in for a Return lease that has already been revoked
// by an operator/emergency takeover.
type cancelledReturnGuard struct{}

func (cancelledReturnGuard) DoSend(func() error) error { return context.Canceled }

func returnTestService(t *testing.T, id uint32, conn interface {
	Send(message.Message) error
}) (*Service, *fleet.Manager) {
	t.Helper()
	cfg := config.Default()
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(aud.Close) // release the audit file before t.TempDir cleanup (Windows lock)
	mgr := fleet.NewManagerForTest(cfg, safety.New(cfg), aud, events.New())
	mgr.RegisterDroneForTest(id, "return-drone", conn)
	return NewService(mgr, safety.New(cfg), aud), mgr
}

// Critical 2 — the generic (SINGLE/GROUPED) post-mission Return RTL must not reach
// the FC after a failsafe latches between its early precheck and conn.Send.
//
//	Initial state:       automatic Return, failsafe inactive (precheck passes).
//	Injected failure:    battery failsafe latches after the precheck but before the
//	                     COMMAND_LONG transport write.
//	Expected Safe State: MissionReturnRTL is refused; zero RTL writes reach the FC;
//	                     the pre-existing Return lease guard still composed and ran.
func TestMissionReturnRTLBlocksAutomaticReturnWhenFailsafeLatchesBeforeCommandLong(t *testing.T) {
	const id = uint32(5)
	sender := &countingSender{}
	svc, mgr := returnTestService(t, id, sender)

	// The Return batch context already carries the Return lease/cancellation guard.
	barrier := &barrierReturnGuard{entered: make(chan struct{}), release: make(chan struct{})}
	ctx := fleet.WithSendGuard(context.Background(), barrier)

	done := make(chan *pb.CommandResult, 1)
	go func() { done <- svc.MissionReturnRTL(ctx, id) }()

	<-barrier.entered // early FailsafeActive precheck already passed
	mgr.LatchBatteryFailsafeForTest(id)
	close(barrier.release)

	res := <-done
	if res == nil || res.Ok {
		t.Fatalf("automatic Return RTL must be refused after the failsafe latch: %+v", res)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("stale automatic Return RTL reached the FC after failsafe ownership: sends=%d", got)
	}
}

// The early precheck still fast-fails when failsafe is already latched, and emits
// no transport write.
func TestMissionReturnRTLPrecheckRejectsWhenFailsafeAlreadyOwned(t *testing.T) {
	const id = uint32(6)
	sender := &countingSender{}
	svc, mgr := returnTestService(t, id, sender)
	mgr.LatchBatteryFailsafeForTest(id)

	res := svc.MissionReturnRTL(context.Background(), id)
	if res == nil || res.Ok || !strings.Contains(res.Message, "failsafe") {
		t.Fatalf("failsafe-owned automatic Return must fast-fail at precheck: %+v", res)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("precheck-rejected Return still wrote to the FC: sends=%d", got)
	}
}

// Composing the failsafe guard must not drop the existing Return lease guard: a
// revoked lease still wins and blocks the transport write.
func TestMissionReturnRTLPreservesReturnLeaseCancellationGuard(t *testing.T) {
	const id = uint32(7)
	sender := &countingSender{}
	svc, _ := returnTestService(t, id, sender)

	ctx := fleet.WithSendGuard(context.Background(), cancelledReturnGuard{})
	res := svc.MissionReturnRTL(ctx, id)
	if res == nil || res.Ok {
		t.Fatalf("revoked Return lease must still block the RTL write: %+v", res)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("cancelled Return reached the FC: sends=%d", got)
	}
}

// The failsafe final-write guard covers only the COMMAND_LONG write; fsMu must be
// released before the COMMAND_ACK wait, or a later failsafe latch would block
// behind an ACK that never arrives.
func TestMissionReturnRTLDoesNotHoldFsMuDuringAckWait(t *testing.T) {
	const id = uint32(8)
	sender := &signalingSender{sent: make(chan struct{})}
	svc, mgr := returnTestService(t, id, sender)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan *pb.CommandResult, 1)
	go func() { done <- svc.MissionReturnRTL(ctx, id) }()

	select {
	case <-sender.sent: // COMMAND_LONG written; now blocked on the ACK wait
	case <-time.After(time.Second):
		t.Fatal("Return RTL COMMAND_LONG was never written")
	}

	latched := make(chan struct{})
	go func() {
		mgr.LatchBatteryFailsafeForTest(id)
		close(latched)
	}()
	select {
	case <-latched: // fsMu was free: the ACK wait is not holding it
	case <-time.After(200 * time.Millisecond):
		cancel()
		<-done
		t.Fatal("failsafe latch blocked behind the Return COMMAND_ACK wait; fsMu scope too broad")
	}
	cancel()
	<-done
}
