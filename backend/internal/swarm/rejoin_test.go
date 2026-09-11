package swarm

import (
	"context"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

func targetOf(cmds []followerCmd, id uint32) (followerCmd, bool) {
	for _, c := range cmds {
		if c.id == id {
			return c, true
		}
	}
	return followerCmd{}, false
}

// Slots never renumber: a vacant slot (0) and an excluded follower both keep their
// index, so no other follower is re-targeted into the space they occupy.
func TestPlanFormationTargetsKeepsVacantSlotIndex(t *testing.T) {
	line := pb.Formation_FORMATION_LINE
	full := planFormationTargets([]uint32{2, 3, 4}, 1, 14, 100, 20, 0, 10, line, 0, nil)
	gap := planFormationTargets([]uint32{2, 0, 4}, 1, 14, 100, 20, 0, 10, line, 0, nil)
	if len(gap) != 2 {
		t.Fatalf("vacant slot must not get a target, got %+v", gap)
	}
	want, _ := targetOf(full, 4)
	got, ok := targetOf(gap, 4)
	if !ok || got != want {
		t.Fatalf("Drone 4 shifted when slot 2 went vacant: %+v vs %+v", got, want)
	}
}

func TestTakeControlFollowerKeepsItsSlotReserved(t *testing.T) {
	// Regression: TAKE CONTROL D2 used to re-target D3 into D2's slot while D2
	// was still hovering there.
	line := pb.Formation_FORMATION_LINE
	full := planFormationTargets([]uint32{2, 3}, 1, 14, 100, 20, 0, 10, line, 0, nil)
	excl := planFormationTargets([]uint32{2, 3}, 1, 14, 100, 20, 0, 10, line, 0,
		func(id uint32) bool { return id == 2 })
	if _, sent := targetOf(excl, 2); sent {
		t.Fatal("excluded Drone 2 must not be commanded by the follower loop")
	}
	want, _ := targetOf(full, 3)
	if got, _ := targetOf(excl, 3); got != want {
		t.Fatalf("Drone 3 must keep its own slot: %+v vs %+v", got, want)
	}
}

func TestSyncSlotsVacatesLeaderAndBackfillsFirstVacant(t *testing.T) {
	// Drone 3 promoted to leader; old leader 1 is a follower again → takes the
	// slot 3 just vacated (a swap), nobody else moves.
	m := &Manager{leaderID: 3, slotOwners: []uint32{2, 3, 4}}
	m.syncSlotsLocked([]uint32{1, 2, 3, 4})
	if want := []uint32{2, 1, 4}; !equalIDs(m.slotOwners, want) {
		t.Fatalf("slotOwners = %v, want %v", m.slotOwners, want)
	}
}

func TestSyncSlotsKeepsReservedOwnersAndAppendsNewcomer(t *testing.T) {
	// Drone 2 is excluded/offline (not managed) → keeps its slot reserved;
	// Drone 5 appears mid-run → gets a new slot at the end.
	m := &Manager{leaderID: 1, slotOwners: []uint32{2, 3}}
	m.syncSlotsLocked([]uint32{1, 3, 5})
	if want := []uint32{2, 3, 5}; !equalIDs(m.slotOwners, want) {
		t.Fatalf("slotOwners = %v, want %v", m.slotOwners, want)
	}
	if got := m.claimSlotLocked(2); got != 0 {
		t.Fatalf("rejoining Drone 2 must get its own reserved slot 0, got %d", got)
	}
}

func equalIDs(a, b []uint32) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestRejoinPreconditionsFailClosedBeforeTouchingFleet(t *testing.T) {
	run := &rejoinRun{token: 1, cancel: func() {}}
	for name, m := range map[string]*Manager{
		"inactive":     {manualExcluded: map[uint32]bool{2: true}},
		"mission":      {active: true, ready: true, missionLeaderID: 1, manualExcluded: map[uint32]bool{2: true}},
		"arranging":    {active: true, manualExcluded: map[uint32]bool{2: true}},
		"halted":       {active: true, ready: true, halted: true, manualExcluded: map[uint32]bool{2: true}},
		"member":       {active: true, ready: true, manualExcluded: map[uint32]bool{}},
		"already busy": {active: true, ready: true, manualExcluded: map[uint32]bool{2: true}, rejoin: map[uint32]*rejoinRun{2: run}},
	} {
		if _, err := m.Rejoin(2); err == nil {
			t.Fatalf("%s: REJOIN must be refused", name)
		}
		if name != "already busy" && m.rejoin[2] != nil {
			t.Fatalf("%s: refused REJOIN must not create a run", name)
		}
	}
}

func newRejoiningManager(cancel context.CancelFunc) *Manager {
	return &Manager{active: true, ready: true, leaderID: 1, formationGen: 5,
		manualExcluded: map[uint32]bool{2: true},
		rejoin:         map[uint32]*rejoinRun{2: {token: 7, slot: 0, cancel: cancel}}}
}

func TestRejoinSendBoundaryClosesOnAbort(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	m := newRejoiningManager(cancel)
	if sent, _ := m.sendRejoinIfOwned(2, 7, func() error { return nil }); !sent {
		t.Fatal("owning run must be allowed to send")
	}
	if sent, _ := m.sendRejoinIfOwned(2, 6, func() error { return nil }); sent {
		t.Fatal("stale run token must never send")
	}
	if !m.AbortRejoin(2) {
		t.Fatal("abort must report the run it cancelled")
	}
	if ctx.Err() == nil {
		t.Fatal("abort must cancel the run context")
	}
	called := false
	if sent, _ := m.sendRejoinIfOwned(2, 7, func() error { called = true; return nil }); sent || called {
		t.Fatal("no rejoin write may begin after abort")
	}
	if !m.manualExcluded[2] {
		t.Fatal("aborted aircraft must stay INDIVIDUAL (manualExcluded)")
	}
	if m.AbortRejoin(2) {
		t.Fatal("second abort must be a no-op")
	}
}

func TestRejoiningAircraftIsFormationOwned(t *testing.T) {
	m := newRejoiningManager(func() {})
	if owned, leader := m.FormationOwnsFollower(2); !owned || leader != 1 {
		t.Fatalf("rejoining Drone 2 must refuse manual MOVE (owned=%v leader=%d)", owned, leader)
	}
	if !m.RejoinInProgress(2) || m.RejoinInProgress(3) {
		t.Fatal("RejoinInProgress must track exactly the rejoining aircraft")
	}
	if st := m.State(); !equalIDs(st.RejoiningIds, []uint32{2}) {
		t.Fatalf("State.RejoiningIds = %v", st.RejoiningIds)
	}
}

func TestFinishRejoinSuccessHandsAircraftBackToFollowerLoop(t *testing.T) {
	m := newRejoiningManager(func() {})
	m.finishRejoin(context.Background(), nil, 2, 7, true, "", false)
	if m.manualExcluded[2] || m.rejoin[2] != nil {
		t.Fatal("settled aircraft must be a formation member again")
	}
	if m.formationGen != 6 {
		t.Fatal("handover must invalidate older follower plans")
	}
}

func TestFinishRejoinFailureStaysIndividual(t *testing.T) {
	m := newRejoiningManager(func() {})
	m.finishRejoin(context.Background(), nil, 2, 7, false, "หมดเวลา", false)
	if !m.manualExcluded[2] || m.rejoin[2] != nil {
		t.Fatal("failed REJOIN must release the run and keep the aircraft INDIVIDUAL")
	}
}

func TestStaleFinishCannotTouchNewerRun(t *testing.T) {
	m := newRejoiningManager(func() {})
	m.finishRejoin(context.Background(), nil, 2, 6, true, "", false)
	if !m.manualExcluded[2] || m.rejoin[2] == nil || m.rejoin[2].token != 7 {
		t.Fatal("a stale run's completion must not hand over or clear the current run")
	}
}

func TestFormationStopCancelsRejoinRuns(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	m := newRejoiningManager(cancel)
	wait := m.RevokeFormationNavigation()
	select {
	case <-wait:
	case <-time.After(3 * time.Second):
		t.Fatal("formation stop did not finish")
	}
	if ctx.Err() == nil || m.RejoinInProgress(2) || m.slotOwners != nil {
		t.Fatal("formation stop must cancel every REJOIN run and reset slot ownership")
	}
}
