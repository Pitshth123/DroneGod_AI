package fleet

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/bluenviron/gomavlib/v3/pkg/message"
)

// testSendGuard mirrors the API mission guard contract without importing api
// (which would create an import cycle).  Cancellation and DoSend share one tiny
// mutex, so the test can prove the final transport write is atomic with respect
// to preemption.
type testSendGuard struct {
	mu        sync.Mutex
	cancelled bool
}

func (g *testSendGuard) DoSend(send func() error) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.cancelled {
		return context.Canceled
	}
	return send()
}

func (g *testSendGuard) cancel() {
	g.mu.Lock()
	g.cancelled = true
	g.mu.Unlock()
}

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

func TestGotoContextGuardCancelWinsBeforeTransportWrite(t *testing.T) {
	sender := &countingSender{}
	d := newDrone(1, "d1", "127.0.0.1", 0, sender)
	guard := &testSendGuard{}
	guard.cancel() // takeover wins before the final write critical section
	ctx := WithSendGuard(context.Background(), guard)

	err := d.GotoContext(ctx, 13.7, 100.5, 20)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("stale GOTO reached transport after cancellation: sends=%d", got)
	}
}

func TestGotoYawContextGuardCancelWinsBeforeTransportWrite(t *testing.T) {
	sender := &countingSender{}
	d := newDrone(1, "d1", "127.0.0.1", 0, sender)
	guard := &testSendGuard{}
	guard.cancel()
	ctx := WithSendGuard(context.Background(), guard)

	err := d.GotoYawContext(ctx, 13.7, 100.5, 20, 90)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("stale GOTO+YAW reached transport after cancellation: sends=%d", got)
	}
}

func TestSendCmdGuardCancelWinsBeforeCommandLongWrite(t *testing.T) {
	sender := &countingSender{}
	d := newDrone(1, "d1", "127.0.0.1", 0, sender)
	guard := &testSendGuard{}
	guard.cancel()
	ctx := WithSendGuard(context.Background(), guard)

	_, err := d.SetMode(ctx, 4)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("stale COMMAND_LONG reached transport after cancellation: sends=%d", got)
	}
}

type barrierSendGuard struct {
	once    sync.Once
	entered chan struct{}
	release chan struct{}
}

func (g *barrierSendGuard) DoSend(send func() error) error {
	g.once.Do(func() { close(g.entered) })
	<-g.release
	return send()
}

func TestFailsafeFinalWriteGuardBlocksMissionAndFormationWritesAfterPrecheck(t *testing.T) {
	tests := []struct {
		name string
		run  func(*Drone, context.Context) error
	}{
		{"mission-goto", func(d *Drone, ctx context.Context) error {
			return d.GotoContext(ctx, 13.7, 100.5, 20)
		}},
		{"mission-hold-zero-velocity", func(d *Drone, ctx context.Context) error {
			return d.MoveVelocityContext(ctx, 0, 0, 0, 0)
		}},
		{"mission-hold-position", func(d *Drone, ctx context.Context) error {
			return d.GotoContext(ctx, 13.7, 100.5, 20)
		}},
		{"mission-hold-set-mode", func(d *Drone, ctx context.Context) error {
			_, err := d.SetMode(ctx, 4)
			return err
		}},
		{"formation-goto-yaw", func(d *Drone, ctx context.Context) error {
			return d.GotoYawContext(ctx, 13.7, 100.5, 20, 90)
		}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			const id = uint32(7)
			m := newTestManager(t, newFakeReg())
			sender := &countingSender{}
			d := newDrone(id, "d7", "127.0.0.1", 0, sender)
			if m.FailsafeActive(id) {
				t.Fatal("precondition: failsafe must be inactive at the earlier precheck")
			}

			barrier := &barrierSendGuard{entered: make(chan struct{}), release: make(chan struct{})}
			ctx := WithSendGuard(context.Background(), barrier)
			ctx = m.WithFailsafeSendGuard(ctx, id)
			done := make(chan error, 1)
			go func() { done <- tc.run(d, ctx) }()
			<-barrier.entered // earlier precheck passed; final write has not entered failsafe guard yet

			m.fsMu.Lock()
			m.fsBatt[id] = true
			m.fsMu.Unlock()
			close(barrier.release)

			if err := <-done; err == nil {
				t.Fatal("stale write succeeded after failsafe latched between precheck and transport")
			}
			if got := sender.count(); got != 0 {
				t.Fatalf("stale write reached transport after failsafe latch: sends=%d", got)
			}
		})
	}
}

func TestAdditionalFailsafeGuardPreservesExistingMissionCancellationGuard(t *testing.T) {
	const id = uint32(8)
	m := newTestManager(t, newFakeReg())
	sender := &countingSender{}
	d := newDrone(id, "d8", "127.0.0.1", 0, sender)
	missionGuard := &testSendGuard{}
	missionGuard.cancel()
	ctx := WithSendGuard(context.Background(), missionGuard)
	ctx = m.WithFailsafeSendGuard(ctx, id)

	if err := d.GotoContext(ctx, 13.7, 100.5, 20); !errors.Is(err, context.Canceled) {
		t.Fatalf("existing mission cancellation guard was replaced: err=%v", err)
	}
	if got := sender.count(); got != 0 {
		t.Fatalf("cancelled mission write reached transport: sends=%d", got)
	}
}

type signalingSender struct {
	once sync.Once
	sent chan struct{}
}

func (s *signalingSender) Send(message.Message) error {
	s.once.Do(func() { close(s.sent) })
	return nil
}

func TestFailsafeFinalWriteGuardDoesNotHoldFsMuAcrossCommandAckWait(t *testing.T) {
	const id = uint32(9)
	m := newTestManager(t, newFakeReg())
	sender := &signalingSender{sent: make(chan struct{})}
	d := newDrone(id, "d9", "127.0.0.1", 0, sender)
	ctx, cancel := context.WithCancel(m.WithFailsafeSendGuard(context.Background(), id))
	defer cancel()

	done := make(chan error, 1)
	go func() {
		_, err := d.SetMode(ctx, 4)
		done <- err
	}()
	select {
	case <-sender.sent:
	case <-time.After(time.Second):
		t.Fatal("SetMode transport write did not occur")
	}

	latched := make(chan struct{})
	go func() {
		m.fsMu.Lock()
		m.fsBatt[id] = true
		m.fsMu.Unlock()
		close(latched)
	}()
	select {
	case <-latched:
		// expected: conn.Send already returned, so the ACK wait holds no fsMu lock
	case <-time.After(200 * time.Millisecond):
		cancel()
		<-done
		t.Fatal("failsafe latch blocked behind COMMAND_ACK wait; fsMu scope is too broad")
	}
	cancel()
	if err := <-done; err == nil {
		t.Fatal("SetMode without an ACK should exit on context cancellation")
	}
}
