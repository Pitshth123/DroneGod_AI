package swarm

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
)

func TestFollowerAuthorityRequiresReadyAndFixedLeaderBinding(t *testing.T) {
	m := &Manager{active: true, leaderID: 2}
	if m.FollowerAuthorityReady(2) || m.ClaimMissionLeader(2) {
		t.Fatal("Mission must not claim leader during form-up")
	}
	m.ready = true
	if !m.FollowerAuthorityReady(2) || m.FollowerAuthorityReady(1) {
		t.Fatal("ready formation must match the exact non-zero leader")
	}
	if !m.ClaimMissionLeader(2) || m.missionLeaderID != 2 {
		t.Fatal("ready fixed leader should bind to Mission")
	}
	if m.ClaimMissionLeader(1) {
		t.Fatal("different leader cannot overlap existing Mission binding")
	}
	m.ReleaseMissionLeader(1)
	if m.missionLeaderID != 2 {
		t.Fatal("stale mismatched release cleared current binding")
	}
	m.ReleaseMissionLeader(2)
	if m.missionLeaderID != 0 {
		t.Fatal("matching release should clear binding")
	}
}

func TestFollowerSendBoundaryBlocksTakeoverThenRejectsStaleSend(t *testing.T) {
	m := &Manager{active: true, ready: true, leaderID: 1}
	started := make(chan struct{})
	release := make(chan struct{})
	sendDone := make(chan struct{})
	go func() {
		defer close(sendDone)
		allowed, err := m.sendFollowerIfOwned(0, 1, 2, func() error {
			close(started)
			<-release
			return errors.New("test send result")
		})
		if !allowed || err == nil {
			t.Errorf("current formation send should run and return its result")
		}
	}()
	<-started

	// The takeover write lock cannot clear ownership until the already-started
	// follower send exits; this is the same lock ordering used by Stop.
	takeoverDone := make(chan struct{})
	go func() {
		m.mu.Lock()
		m.active = false
		m.ready = false
		m.mu.Unlock()
		close(takeoverDone)
	}()
	select {
	case <-takeoverDone:
		t.Fatal("takeover crossed an in-progress follower send")
	case <-time.After(20 * time.Millisecond):
	}
	close(release)
	select {
	case <-sendDone:
	case <-time.After(time.Second):
		t.Fatal("send did not exit")
	}
	select {
	case <-takeoverDone:
	case <-time.After(time.Second):
		t.Fatal("takeover did not acquire authority after send exited")
	}

	called := false
	if allowed, _ := m.sendFollowerIfOwned(0, 1, 2, func() error {
		called = true
		return nil
	}); allowed || called {
		t.Fatal("stale formation send began after takeover cleared ownership")
	}
}

func TestFormationTargetsNeverCommandLeaderAndSkipFailsafeFollower(t *testing.T) {
	got := planFormationTargets(
		[]uint32{1, 2, 3}, 1, 14, 100, 20, 0, 10,
		0, 0, func(id uint32) bool { return id == 3 })
	if len(got) != 1 || got[0].id != 2 {
		t.Fatalf("want only healthy follower D2, got %+v", got)
	}
}

func TestFormationGotoYawUsesAtomicFailsafeFinalWriteGuard(t *testing.T) {
	b, err := os.ReadFile("manager.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "allowed, err := m.sendFollowerIfOwned(")
	if start < 0 {
		t.Fatal("formation follower final-write boundary missing")
	}
	body := src[start:]
	if end := strings.Index(body, "\n\t\tif !allowed"); end >= 0 {
		body = body[:end]
	}
	failsafeGuard := strings.Index(body, "m.fleet.WithFailsafeSendGuard(context.Background(), c.id)")
	gotoYaw := strings.Index(body, "fdrone.GotoYawContext(ctx,")
	if failsafeGuard < 0 || gotoYaw < 0 || failsafeGuard > gotoYaw {
		t.Fatal("formation GotoYaw must attach fleet atomic failsafe guard at the final transport boundary")
	}
}

func TestRevokeFormationNavigationReturnsBeforeLoopTeardown(t *testing.T) {
	loopDone := make(chan struct{})
	ctx, cancel := context.WithCancel(context.Background())
	guard := &navigationSendGuard{}
	m := &Manager{
		active: true, ready: true, leaderID: 1,
		cancel: cancel, done: loopDone, formationGuard: guard,
	}
	_ = ctx

	start := time.Now()
	stopped := m.RevokeFormationNavigation()
	if elapsed := time.Since(start); elapsed > 100*time.Millisecond {
		t.Fatalf("authority revocation waited for loop teardown: %v", elapsed)
	}
	if m.active || m.ready {
		t.Fatal("formation write authority must be revoked before return")
	}
	guardCalled := false
	if err := guard.DoSend(func() error {
		guardCalled = true
		return nil
	}); !errors.Is(err, context.Canceled) || guardCalled {
		t.Fatalf("form-up final-write guard remained open after revocation: err=%v called=%v", err, guardCalled)
	}

	// A second emergency/revocation must join the same teardown rather than wait
	// for the old loop synchronously while holding an API ownership lock.
	start = time.Now()
	if again := m.RevokeFormationNavigation(); again != stopped {
		t.Fatal("concurrent revocation should observe the same teardown generation")
	}
	if elapsed := time.Since(start); elapsed > 100*time.Millisecond {
		t.Fatalf("second revocation blocked on teardown: %v", elapsed)
	}

	called := false
	if allowed, _ := m.sendFollowerIfOwned(0, 1, 2, func() error {
		called = true
		return nil
	}); allowed || called {
		t.Fatal("no stale follower send may begin after authority revocation")
	}
	close(loopDone)
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("formation teardown did not finish after loop exit")
	}
}

func TestRevokeReturnNavigationClosesFinalWriteGateWithoutWaiting(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	guard := &navigationSendGuard{}
	done := make(chan struct{})
	m := &Manager{returnCancel: cancel, returnDone: done, returnGuard: guard}

	start := time.Now()
	gotDone := m.RevokeReturnNavigation()
	if gotDone != done {
		t.Fatal("return revocation must preserve the running sequence completion handle")
	}
	if elapsed := time.Since(start); elapsed > 100*time.Millisecond {
		t.Fatalf("return revocation waited for sequence teardown: %v", elapsed)
	}
	if ctx.Err() == nil {
		t.Fatal("return context must be cancelled")
	}
	called := false
	if err := guard.DoSend(func() error {
		called = true
		return nil
	}); !errors.Is(err, context.Canceled) || called {
		t.Fatalf("stale return transport write escaped guard: err=%v called=%v", err, called)
	}
	close(done)
}

// V3-S09-C succession — RebindMissionMembership is the only formation-ownership
// handoff. It must move leader ownership, stale the previous formation generation,
// and leave excluded members with no formation authority at all.
//
//	Initial state:       ready formation, leader D1, mission members {1,2,3}.
//	Injected failure:    operator takes over leader D1; succession rebinds to D2.
//	Expected Safe State: leader/mission ownership is D2, generation bumped, D3 still
//	                     owned, D1 owned by neither mission nor formation.
//	MUST NOT happen:     a stale-generation follower write; any write to the excluded
//	                     drone; the new leader being commanded as its own follower.
func TestRebindMissionMembershipMovesOwnershipAndStalesOldGeneration(t *testing.T) {
	m := &Manager{active: true, ready: true, leaderID: 1}
	if !m.ClaimMissionMembership(1, []uint32{1, 2, 3}) {
		t.Fatal("precondition: mission membership claim failed")
	}
	oldGen := m.formationGen
	if ok, _ := m.sendFollowerIfOwned(oldGen, 1, 2, func() error { return nil }); !ok {
		t.Fatal("precondition: current-generation follower send must be allowed")
	}

	if !m.RebindMissionMembership(1, 2, []uint32{2, 3}, []uint32{1}) {
		t.Fatal("rebind to an eligible successor must succeed")
	}
	if m.leaderID != 2 || m.missionLeaderID != 2 {
		t.Fatalf("leader ownership not moved: leader=%d mission=%d", m.leaderID, m.missionLeaderID)
	}
	if m.formationGen == oldGen {
		t.Fatal("rebind must bump the formation generation")
	}
	if ok, _ := m.sendFollowerIfOwned(oldGen, 2, 3, func() error { return nil }); ok {
		t.Fatal("stale formation generation still commanded a follower")
	}
	if ok, _ := m.sendFollowerIfOwned(m.formationGen, 2, 1, func() error { return nil }); ok {
		t.Fatal("excluded drone received a formation command")
	}
	if ok, _ := m.sendFollowerIfOwned(m.formationGen, 2, 2, func() error { return nil }); ok {
		t.Fatal("new leader was commanded as its own follower")
	}
	if ok, _ := m.sendFollowerIfOwned(m.formationGen, 2, 3, func() error { return nil }); !ok {
		t.Fatal("remaining follower lost formation ownership")
	}
}

// A rebind that does not describe a consistent handoff must be refused outright
// rather than half-applied, so formation ownership can never split.
func TestRebindMissionMembershipRejectsStaleOrInconsistentHandoff(t *testing.T) {
	bound := func(t *testing.T) *Manager {
		t.Helper()
		m := &Manager{active: true, ready: true, leaderID: 1}
		if !m.ClaimMissionMembership(1, []uint32{1, 2, 3}) {
			t.Fatal("precondition: mission membership claim failed")
		}
		return m
	}
	cases := []struct {
		name                 string
		oldLeader, newLeader uint32
		active, excluded     []uint32
	}{
		{"stale old leader", 3, 2, []uint32{2, 3}, []uint32{1}},
		{"successor not active", 1, 4, []uint32{2, 3}, []uint32{1}},
		{"member both active and excluded", 1, 2, []uint32{2, 3}, []uint32{3}},
		{"below minimum formation", 1, 2, []uint32{2}, []uint32{1, 3}},
		{"zero successor", 1, 0, []uint32{2, 3}, []uint32{1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			m := bound(t)
			gen := m.formationGen
			if m.RebindMissionMembership(tc.oldLeader, tc.newLeader, tc.active, tc.excluded) {
				t.Fatal("inconsistent rebind must be refused")
			}
			if m.leaderID != 1 || m.missionLeaderID != 1 || m.formationGen != gen {
				t.Fatalf("refused rebind mutated ownership: leader=%d mission=%d gen=%d",
					m.leaderID, m.missionLeaderID, m.formationGen)
			}
		})
	}
}
