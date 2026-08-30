package swarm

import (
	"context"
	"sort"
	"sync"
	"testing"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/common"
	"github.com/bluenviron/gomavlib/v3/pkg/message"

	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/safety"
)

// Critical 1 execution harness. Form-up navigation writes must compose the fleet
// per-aircraft failsafe final-write guard, so a battery/link failsafe that latches
// after planning can never let a stale form-up GOTO/GotoYaw reach the FC.

type fakeConn interface{ Send(message.Message) error }

// countingSender counts the transport writes a real link would carry.
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

// mirrorSignalSender mirrors each commanded position back into the drone's Nav so
// waitFormUpTarget observes arrival, and signals (once) after exactly signalAt
// writes so the test can latch failsafe on another goroutine — never inside the
// send, which runs under fsMu. The latch is applied before the next phase because
// waitFormUpTarget's 250ms poll separates the signalling write from the next one.
type mirrorSignalSender struct {
	mu       sync.Mutex
	sent     int
	drone    *fleet.Drone
	signalAt int
	reached  chan struct{}
}

func (s *mirrorSignalSender) Send(m message.Message) error {
	s.mu.Lock()
	s.sent++
	n := s.sent
	s.mu.Unlock()
	if p, ok := m.(*common.MessageSetPositionTargetGlobalInt); ok && s.drone != nil {
		s.drone.HandleFrame(1, &ardupilotmega.MessageGlobalPositionInt{
			Lat:         p.LatInt,
			Lon:         p.LonInt,
			RelativeAlt: int32(p.Alt * 1000),
		})
	}
	if n == s.signalAt {
		close(s.reached)
	}
	return nil
}
func (s *mirrorSignalSender) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sent
}

func feedPos(d *fleet.Drone, lat, lon, altM float64) {
	d.HandleFrame(1, &ardupilotmega.MessageGlobalPositionInt{
		Lat:         int32(lat * 1e7),
		Lon:         int32(lon * 1e7),
		RelativeAlt: int32(altM * 1000),
	})
}

// formUpHarness builds a swarm Manager over a connection-free fleet with the given
// drones positioned safely apart (~22m along latitude, well over MinSeparation).
// Leader is the lowest id.
func formUpHarness(t *testing.T, conns map[uint32]fakeConn) (*Manager, *fleet.Manager) {
	t.Helper()
	cfg := config.Default()
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(aud.Close)
	env := safety.New(cfg)
	bus := events.New()
	fl := fleet.NewManagerForTest(cfg, env, aud, bus)

	ids := make([]uint32, 0, len(conns))
	for id := range conns {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(a, b int) bool { return ids[a] < ids[b] })
	// Positioned near the default GCS so every planned point stays inside the
	// envelope radius; ~22m apart (1e-4 deg ≈ 11.1m) clears MinSeparation (5m).
	const baseLat, baseLon = 14.9581695, 102.0986187
	for i, id := range ids {
		d := fl.RegisterDroneForTest(id, "d", conns[id])
		feedPos(d, baseLat+float64(i)*2e-4, baseLon, 20)
	}
	m := NewManager(cfg, fl, env, aud, bus)
	m.leaderID = ids[0]
	m.spacing = 10
	return m, fl
}

// 1. Leader form-up GOTO: planning has passed; failsafe owns the leader; the leader
//    pin write must be refused with zero leader writes and no follower phase.
func TestFormUpLeaderGotoRefusedWhenLeaderFailsafeOwned(t *testing.T) {
	leader := &countingSender{}
	f2 := &countingSender{}
	f3 := &countingSender{}
	m, fl := formUpHarness(t, map[uint32]fakeConn{1: leader, 2: f2, 3: f3})
	fl.LatchBatteryFailsafeForTest(1) // leader owned by failsafe before form-up write

	if m.formUpSequential(context.Background()) {
		t.Fatal("form-up must fail closed when the leader pin is failsafe-owned")
	}
	if leader.count() != 0 {
		t.Fatalf("stale leader pin reached the FC after failsafe latch: sends=%d", leader.count())
	}
	if f2.count() != 0 || f3.count() != 0 {
		t.Fatalf("followers were commanded after leader-pin failure: f2=%d f3=%d", f2.count(), f3.count())
	}
}

// 2. Follower transit GotoYaw: failsafe owns one follower; leader pin proceeds, but
//    the owned follower's first (transit) write is refused with zero writes to it.
func TestFormUpFollowerTransitRefusedWhenFollowerFailsafeOwned(t *testing.T) {
	leader := &countingSender{}
	f2 := &countingSender{}
	m, fl := formUpHarness(t, map[uint32]fakeConn{1: leader, 2: f2})
	fl.LatchBatteryFailsafeForTest(2) // the only follower is failsafe-owned

	if m.formUpSequential(context.Background()) {
		t.Fatal("form-up must fail closed when a follower transit write is failsafe-owned")
	}
	if leader.count() != 1 {
		t.Fatalf("leader pin should proceed (leader not failsafe): sends=%d", leader.count())
	}
	if f2.count() != 0 {
		t.Fatalf("stale follower transit write reached the FC after failsafe latch: sends=%d", f2.count())
	}
}

// 3. Follower later phases: the same guard blocks the horizontal and final-slot
//    writes. A mirroring sender lets earlier phases complete, then latches failsafe
//    so the next phase is refused.
func TestFormUpFollowerLaterPhasesRefusedWhenFailsafeLatchesMidSequence(t *testing.T) {
	for _, tc := range []struct {
		name       string
		latchAfter int // writes to the follower before failsafe latches
		wantWrites int // follower transport writes that should have occurred
	}{
		{"horizontal-phase", 1, 1}, // latch after transit; horizontal refused
		{"final-slot-phase", 2, 2}, // latch after horizontal; final refused
	} {
		t.Run(tc.name, func(t *testing.T) {
			leader := &countingSender{}
			follower := &mirrorSignalSender{signalAt: tc.latchAfter, reached: make(chan struct{})}
			m, fl := formUpHarness(t, map[uint32]fakeConn{1: leader, 2: follower})
			follower.drone = fl.Drone(2)

			done := make(chan bool, 1)
			go func() { done <- m.formUpSequential(context.Background()) }()

			<-follower.reached // the signalling phase write completed and mirrored Nav
			// Latch on this goroutine (never inside the send, which holds fsMu). The
			// form-up loop is now in waitFormUpTarget's 250ms poll, so the latch is
			// established before the next phase attempts its write.
			fl.LatchBatteryFailsafeForTest(2)

			if <-done {
				t.Fatal("form-up must fail closed once failsafe owns the follower mid-sequence")
			}
			if got := follower.count(); got != tc.wantWrites {
				t.Fatalf("follower writes = %d, want %d (a later-phase write bypassed the guard)",
					got, tc.wantWrites)
			}
		})
	}
}

// 4. The existing formation cancellation guard remains effective when composed with
//    the failsafe guard: a cancelled formation guard blocks every form-up write.
func TestFormUpCancellationGuardStillBlocksWhenComposedWithFailsafe(t *testing.T) {
	leader := &countingSender{}
	f2 := &countingSender{}
	m, _ := formUpHarness(t, map[uint32]fakeConn{1: leader, 2: f2})
	guard := &navigationSendGuard{}
	guard.Cancel() // operator/return revocation wins before any form-up write
	ctx := fleet.WithSendGuard(context.Background(), guard)

	if m.formUpSequential(ctx) {
		t.Fatal("form-up must fail closed when the formation cancellation guard is revoked")
	}
	if leader.count() != 0 || f2.count() != 0 {
		t.Fatalf("stale form-up writes crossed the cancellation guard: leader=%d f2=%d",
			leader.count(), f2.count())
	}
}
