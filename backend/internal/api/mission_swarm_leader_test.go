package api

import (
	"context"
	"errors"
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
)

func apiSwarmLeaderPlan() mission.MissionPlan {
	return mission.MissionPlan{
		PlanID: "api-swl", Mode: mission.ModeSwarmLeader,
		Participants: []uint32{1, 2, 3}, LeaderID: 1,
		Routes: []mission.Route{{Points: []mission.Waypoint{
			{Seq: 0, Lat: 14, Lon: 100, Alt: 20},
			{Seq: 1, Lat: 14.001, Lon: 100, Alt: 20},
		}}},
	}
}

func newSwarmLeaderOwnershipServer(t *testing.T) *Server {
	t.Helper()
	s := newMissionServer()
	s.mission.EnableSwarmLeaderAuthority()
	s.missionAuthority = true
	if _, err := s.mission.StartOp(apiSwarmLeaderPlan(), "api-swl-op"); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestMissionSwarmCoordinatorsTreatNilConcreteManagerAsUnavailable(t *testing.T) {
	s := newMissionServer() // s.swarm is a nil *swarm.Manager
	if got := s.missionSwarmCoordinator(); got != nil {
		t.Fatalf("missionSwarmCoordinator leaked a typed-nil interface: %#v", got)
	}
	if got := s.missionSwarmReturnCoordinator(); got != nil {
		t.Fatalf("missionSwarmReturnCoordinator leaked a typed-nil interface: %#v", got)
	}
}

func TestSwarmLeaderStartMissionWithoutSwarmManagerFailsClosed(t *testing.T) {
	s := newMissionServer()
	s.mission.EnableSwarmLeaderAuthority() // test-only pre-flip gate
	s.missionAuthority = true
	plan := &pb.MissionPlan{
		PlanId:       "swl-no-manager",
		Mode:         pb.MissionMode_MISSION_MODE_SWARM_LEADER,
		Participants: []uint32{1, 2},
		LeaderId:     1,
		Routes: []*pb.MissionRoute{{Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: 14, Lon: 100, Alt: 20},
			{Seq: 1, Lat: 14.001, Lon: 100, Alt: 20},
		}}},
	}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "swl-no-manager-op",
	})
	if err != nil {
		t.Fatalf("missing swarm manager should fail closed, not return handler error/panic: %v", err)
	}
	if resp == nil || resp.Ok || resp.RunId != 0 || resp.AuthorityActive {
		t.Fatalf("missing swarm manager must reject before authority claim: %+v", resp)
	}
	if snap := s.mission.Snapshot(); snap.Active || snap.Authority {
		t.Fatalf("rejected SWARM_LEADER start claimed mission authority: %+v", snap)
	}
}

func TestSwarmLeaderMissionOwnershipIsLeaderOnly(t *testing.T) {
	s := newSwarmLeaderOwnershipServer(t)
	s.missionDispatchMu.Lock()
	leaderOwned := s.missionOwnsDroneLocked(1)
	follower2Owned := s.missionOwnsDroneLocked(2)
	follower3Owned := s.missionOwnsDroneLocked(3)
	s.missionDispatchMu.Unlock()
	if !leaderOwned || follower2Owned || follower3Owned {
		t.Fatalf("Mission ownership leader=%v follower2=%v follower3=%v",
			leaderOwned, follower2Owned, follower3Owned)
	}
}

// fakeSwarmCoordinator records the split-ownership calls the API makes during an
// S09-C takeover so a test can assert the exact membership handed to formation.
type fakeSwarmCoordinator struct {
	rebindOK      bool
	rebinds       int
	lastOldLeader uint32
	lastNewLeader uint32
	lastActive    []uint32
	lastExcluded  []uint32
	revokes       int
	releases      int
}

func (f *fakeSwarmCoordinator) FollowerAuthorityReadyFor(uint32, []uint32) bool { return true }
func (f *fakeSwarmCoordinator) ClaimMissionMembership(uint32, []uint32) bool    { return true }
func (f *fakeSwarmCoordinator) RebindMissionMembership(oldLeaderID, newLeaderID uint32,
	active, excluded []uint32) bool {
	f.rebinds++
	f.lastOldLeader, f.lastNewLeader = oldLeaderID, newLeaderID
	f.lastActive = append([]uint32(nil), active...)
	f.lastExcluded = append([]uint32(nil), excluded...)
	return f.rebindOK
}
func (f *fakeSwarmCoordinator) ReleaseMissionLeader(uint32) { f.releases++ }
func (f *fakeSwarmCoordinator) RevokeFormationNavigation() <-chan struct{} {
	f.revokes++
	done := make(chan struct{})
	close(done)
	return done
}

// operatorTakeover runs the exact locked sequence the real takeover RPC path uses
// (idempotentTakeover / preemptive batch): selective SWARM membership handling
// first, then the generic mission-ownership cancel.
func operatorTakeover(s *Server, ids ...uint32) {
	s.missionDispatchMu.Lock()
	defer s.missionDispatchMu.Unlock()
	s.stopSwarmForTargetsLocked(ids)
	s.cancelMissionForOperatorTargetsLocked(ids)
}

func newSwarmTakeoverServer(t *testing.T) (*Server, *fakeSwarmCoordinator, *apiMissionCommander) {
	t.Helper()
	s := newSwarmLeaderOwnershipServer(t)
	coordinator := &fakeSwarmCoordinator{rebindOK: true}
	cmd := &apiMissionCommander{}
	s.missionSwarm = coordinator
	s.missionExec = cmd
	s.swarmSuccessorEligible = func(uint32) bool { return true }
	return s, coordinator, cmd
}

// S09-C ratified policy — FOLLOWER takeover.
//
//	Initial state:       SWARM_LEADER run, leader D1, followers D2/D3, RUNNING.
//	Injected failure:    operator takes over follower D3.
//	Expected Safe State: only D3 is excluded; the SAME run continues at the same
//	                     index with leader D1 unchanged; formation is rebound to
//	                     active {1,2}.
//	MUST NOT happen:     whole-mission cancellation; D3 remaining mission/formation
//	                     owned; any mission flight command.
func TestSwarmFollowerTakeoverExcludesOnlyThatFollowerAndKeepsMission(t *testing.T) {
	s, coordinator, cmd := newSwarmTakeoverServer(t)
	before := s.mission.Snapshot()

	operatorTakeover(s, 3)

	snap := s.mission.Snapshot()
	if !snap.Active || snap.RunID != before.RunID || snap.CurrentIndex != before.CurrentIndex ||
		snap.CurrentLeaderID != 1 {
		t.Fatalf("follower takeover disturbed the run: before=%+v after=%+v", before, snap)
	}
	if len(snap.ExcludedParticipants) != 1 || snap.ExcludedParticipants[0] != 3 {
		t.Fatalf("follower exclusion set wrong: %v", snap.ExcludedParticipants)
	}
	if coordinator.rebinds != 1 || coordinator.lastNewLeader != 1 ||
		len(coordinator.lastActive) != 2 || coordinator.lastActive[0] != 1 ||
		coordinator.lastActive[1] != 2 {
		t.Fatalf("formation not rebound to remaining members: %+v", coordinator)
	}
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("takeover emitted a mission flight command: %+v", cmd)
	}
}

// S09-C ratified policy — LEADER takeover promotes the next eligible participant
// in original order and keeps the SAME run/index.
//
//	Expected Safe State: D1 excluded, D2 is current leader, same run_id/index,
//	                     mission still active, mission owns only D2.
//	MUST NOT happen:     mission cancellation; D1 still mission-owned; a new run.
func TestSwarmLeaderTakeoverPromotesSuccessorWithoutCancellingRun(t *testing.T) {
	s, coordinator, cmd := newSwarmTakeoverServer(t)
	before := s.mission.Snapshot()

	operatorTakeover(s, 1)

	snap := s.mission.Snapshot()
	if !snap.Active || snap.RunID != before.RunID || snap.CurrentIndex != before.CurrentIndex ||
		snap.CurrentLeaderID != 2 {
		t.Fatalf("leader takeover did not promote inside the same run: before=%+v after=%+v",
			before, snap)
	}
	if len(snap.ExcludedParticipants) != 1 || snap.ExcludedParticipants[0] != 1 {
		t.Fatalf("old leader not excluded: %v", snap.ExcludedParticipants)
	}
	if coordinator.rebinds != 1 || coordinator.lastOldLeader != 1 || coordinator.lastNewLeader != 2 {
		t.Fatalf("formation not rebound to the successor: %+v", coordinator)
	}
	s.missionDispatchMu.Lock()
	oldLeaderOwned := s.missionOwnsDroneLocked(1)
	newLeaderOwned := s.missionOwnsDroneLocked(2)
	s.missionDispatchMu.Unlock()
	if oldLeaderOwned || !newLeaderOwned {
		t.Fatalf("ownership did not move to the successor: old=%v new=%v",
			oldLeaderOwned, newLeaderOwned)
	}
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("promotion emitted a mission flight command: %+v", cmd)
	}
}

// S09-C — a failed formation rebind must interrupt rather than fly on with
// ambiguous ownership, and must not invent an aircraft action.
func TestSwarmRebindFailureInterruptsWithoutFlightCommand(t *testing.T) {
	s, coordinator, cmd := newSwarmTakeoverServer(t)
	coordinator.rebindOK = false

	operatorTakeover(s, 1)

	snap := s.mission.Snapshot()
	if snap.Active || snap.Authority || snap.State != mission.StateInterrupted {
		t.Fatalf("failed rebind must interrupt: %+v", snap)
	}
	if coordinator.revokes == 0 {
		t.Fatal("failed rebind must revoke formation navigation")
	}
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("failed rebind invented a flight command: %+v", cmd)
	}
}

// S09-C — when no eligible successor remains the run is INTERRUPTED; the mode is
// never silently converted to SINGLE and no RTL/LAND is invented.
func TestSwarmLeaderTakeoverWithoutEligibleSuccessorInterrupts(t *testing.T) {
	s, _, cmd := newSwarmTakeoverServer(t)
	s.swarmSuccessorEligible = func(uint32) bool { return false }

	operatorTakeover(s, 1)

	snap := s.mission.Snapshot()
	if snap.Active || snap.Authority || snap.State != mission.StateInterrupted ||
		snap.Mode != mission.ModeSwarmLeader || snap.CurrentLeaderID != 0 {
		t.Fatalf("no-successor takeover must interrupt without mode conversion: %+v", snap)
	}
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("no-successor interruption invented a flight command: %+v", cmd)
	}
}

func TestSwarmLeaderFollowerFailsafeDoesNotInterruptRoute(t *testing.T) {
	s := newSwarmLeaderOwnershipServer(t)
	if s.mission.Interrupt(2, "battery", "battery critical") {
		t.Fatal("follower failsafe is handled by swarm exclusion, not Mission route interruption")
	}
	if !s.mission.Snapshot().Active {
		t.Fatal("leader route must continue after follower exclusion")
	}
	if !s.mission.Interrupt(1, "link", "leader link lost") {
		t.Fatal("leader failsafe must interrupt route")
	}
	if s.mission.Snapshot().Active {
		t.Fatal("leader route must be terminal after leader failsafe")
	}
}

func TestSwarmLeaderGotoRejectMatchesLegacyBestEffortStall(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{perDroneErr: map[uint32]error{1: errors.New("leader goto rejected")}}
	s.mission.EnableSwarmLeaderAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	runID, err := s.mission.StartOp(apiSwarmLeaderPlan(), "api-swl-reject")
	if err != nil {
		t.Fatal(err)
	}

	s.dispatchMissionAuthority()
	if cmd.attempts != 1 || cmd.calls != 0 {
		t.Fatalf("leader GOTO should be attempted once and rejected: attempts=%d successful=%d",
			cmd.attempts, cmd.calls)
	}
	snap := s.mission.Snapshot()
	if !snap.Active || snap.State != mission.StateRunning {
		t.Fatalf("Legacy best-effort rejection must stall, not fail the route: %+v", snap)
	}
	if len(snap.Rejections) != 1 || snap.Rejections[0].RunID != runID ||
		snap.Rejections[0].Index != 0 || snap.Rejections[0].DroneID != 1 ||
		snap.Rejections[0].Reason == "" {
		t.Fatalf("rejection must remain structured and bound to run/index/leader: %+v", snap.Rejections)
	}

	// The index was already claimed; an observer/dispatch retry must not blindly
	// resend the rejected GOTO. Legacy waits at the barrier until operator action.
	s.dispatchMissionAuthority()
	if cmd.attempts != 1 {
		t.Fatalf("rejected leader GOTO must not be retried blindly, attempts=%d", cmd.attempts)
	}
}
