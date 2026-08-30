package api

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/swarm"
)

type fakeMissionSwarmReturn struct {
	mu        sync.Mutex
	starts    int
	revokes   int
	requested []uint32
	results   chan swarm.ReturnResult
}

func (f *fakeMissionSwarmReturn) ReturnAndLand(_ context.Context, ids []uint32, _, _ float64) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.starts++
	f.requested = append([]uint32(nil), ids...)
	f.results = make(chan swarm.ReturnResult, 1)
	return nil
}

func (f *fakeMissionSwarmReturn) RevokeReturnNavigation() <-chan struct{} {
	f.mu.Lock()
	f.revokes++
	results := f.results
	f.mu.Unlock()
	if results != nil {
		select {
		case results <- swarm.ReturnResult{Outcome: swarm.ReturnOutcomeCancelled, Reason: "operator revocation"}:
		default:
		}
	}
	done := make(chan struct{})
	close(done)
	return done
}

func (f *fakeMissionSwarmReturn) ReturnResults() <-chan swarm.ReturnResult {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.results
}

func (f *fakeMissionSwarmReturn) finish(result swarm.ReturnResult) {
	f.mu.Lock()
	results := f.results
	f.mu.Unlock()
	results <- result
}

func (f *fakeMissionSwarmReturn) snapshot() (starts, revokes int, requested []uint32) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.starts, f.revokes, append([]uint32(nil), f.requested...)
}

func newSwarmReturnDispatchServer(t *testing.T, participants []uint32, exclude uint32,
	failsafe func(uint32) bool) (*Server, *fakeMissionSwarmReturn) {
	t.Helper()
	var clockTick atomic.Int64
	e := mission.NewEngine(func() time.Time { return time.Unix(1000, clockTick.Add(1)) })
	e.EnableSwarmLeaderAuthority()
	e.EnableReturnPolicyPreFlip()
	plan := mission.MissionPlan{
		PlanID: "swarm-return-dispatch", Mode: mission.ModeSwarmLeader,
		Participants: participants, LeaderID: participants[0], RtlAfter: true,
		Routes: []mission.Route{{Points: []mission.Waypoint{{Seq: 0, Lat: 14, Lon: 100}}}},
	}
	if _, err := e.Start(plan); err != nil {
		t.Fatal(err)
	}
	if exclude != 0 {
		transition, ok := e.BeginSwarmOperatorTakeover(exclude, func(uint32) bool { return true })
		if !ok || transition.Duplicate || transition.Interrupted {
			t.Fatalf("exclude precondition failed: %+v ok=%v", transition, ok)
		}
		if !e.CompleteSwarmOperatorTakeover(transition.RunID, transition.Generation, true, "test rebind") {
			t.Fatal("exclude transition did not commit")
		}
	}
	fake := &fakeMissionSwarmReturn{}
	s := &Server{
		mission: e, missionAuthority: true, missionSwarmReturn: fake,
		missionFailsafeActive: failsafe, ctx: context.Background(),
		reservations: make(map[uint32]*cmdLease),
	}
	return s, fake
}

func completeSwarmMission(t *testing.T, s *Server) {
	t.Helper()
	snap := s.mission.Snapshot()
	s.mission.Observe(snap.CurrentLeaderID, 14, 100, 0)
	if got := s.mission.Snapshot(); got.State != mission.StateCompleted || got.ReturnState != mission.ReturnStatePending {
		t.Fatalf("mission did not reach pending Return: %+v", got)
	}
}

func waitReturnState(t *testing.T, s *Server, want mission.ReturnState) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if s.mission.Snapshot().ReturnState == want {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("Return state=%s, want %s", s.mission.Snapshot().ReturnState, want)
}

func newReturnServer(cmd *apiMissionCommander, enable func(*mission.Engine)) *Server {
	e := mission.NewEngine(nil)
	enable(e)
	e.EnableReturnPolicyPreFlip()
	return &Server{
		mission: e, missionAuthority: true, missionExec: cmd,
		ctx: context.Background(), reservations: make(map[uint32]*cmdLease),
	}
}

func singleReturnPlan(id uint32) *pb.MissionPlan {
	return &pb.MissionPlan{
		PlanId: "return-single", Mode: pb.MissionMode_MISSION_MODE_GROUPED,
		Participants: []uint32{id}, RtlAfter: true,
		Routes: []*pb.MissionRoute{{Points: []*pb.MissionWaypoint{{Seq: 0, Lat: 14, Lon: 100}}}},
	}
}

func TestReturnDispatcherNaturalCompletionExactlyOnce(t *testing.T) {
	cmd := &apiMissionCommander{}
	s := newReturnServer(cmd, (*mission.Engine).EnableAuthority)
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singleReturnPlan(1), OperationId: "op-return",
	})
	if err != nil || !resp.Ok || cmd.calls != 1 {
		t.Fatalf("start=%+v err=%v goto=%d", resp, err, cmd.calls)
	}
	s.observeFrom([]*pb.Telemetry{{DroneId: 1, Position: &pb.GeoPoint{Lat: 14, Lon: 100}}})
	if cmd.rtlCalls != 1 || len(cmd.rtlIDs) != 1 || cmd.rtlIDs[0] != 1 {
		t.Fatalf("rtl calls=%d ids=%v", cmd.rtlCalls, cmd.rtlIDs)
	}
	state := s.mission.Snapshot()
	if state.State != mission.StateCompleted || state.ReturnState != mission.ReturnStateCompleted || state.Authority {
		t.Fatalf("handoff state=%+v", state)
	}
	for i := 0; i < 3; i++ {
		s.observeFrom([]*pb.Telemetry{{DroneId: 1, Position: &pb.GeoPoint{Lat: 14, Lon: 100}}})
		s.dispatchReturnAuthority()
	}
	if cmd.rtlCalls != 1 {
		t.Fatalf("duplicate observation/query dispatched %d RTLs", cmd.rtlCalls)
	}
}

func TestLiveAuthorityStillRejectsRTLAfterWithoutPreFlip(t *testing.T) {
	cmd := &apiMissionCommander{}
	e := mission.NewEngine(nil)
	e.EnableAuthority()
	s := &Server{mission: e, missionAuthority: true, missionExec: cmd,
		ctx: context.Background(), reservations: make(map[uint32]*cmdLease)}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singleReturnPlan(1), OperationId: "live-must-reject",
	})
	if err != nil || resp.Ok || resp.RunId != 0 || cmd.calls != 0 || cmd.rtlCalls != 0 {
		t.Fatalf("live gate widened: resp=%+v err=%v goto=%d rtl=%d",
			resp, err, cmd.calls, cmd.rtlCalls)
	}
}

func TestReturnPolicyProtoAmbiguityFailsClosed(t *testing.T) {
	cases := []*pb.MissionPlan{
		func() *pb.MissionPlan {
			p := singleReturnPlan(1)
			p.RtlAfter = false
			p.ReturnPolicy = pb.MissionReturnPolicy(99)
			return p
		}(),
		func() *pb.MissionPlan {
			p := &pb.MissionPlan{
				PlanId: "bad-separate-return", Mode: pb.MissionMode_MISSION_MODE_SEPARATE,
				Participants: []uint32{1, 2}, RtlAfter: true,
				SeparateReturnTiming: pb.MissionSeparateReturnTiming(99),
				Routes: []*pb.MissionRoute{
					{DroneId: 1, Points: []*pb.MissionWaypoint{{Lat: 14, Lon: 100}}},
					{DroneId: 2, Points: []*pb.MissionWaypoint{{Lat: 15, Lon: 101}}},
				},
			}
			return p
		}(),
	}
	for _, plan := range cases {
		s := newMissionServer()
		resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan})
		if err != nil || resp.Ok || resp.RunId != 0 {
			t.Fatalf("ambiguous policy accepted: plan=%+v resp=%+v err=%v", plan, resp, err)
		}
	}
}

func TestCancelPendingReturnSendsZeroRTL(t *testing.T) {
	cmd := &apiMissionCommander{}
	s := newReturnServer(cmd, (*mission.Engine).EnableAuthority)
	resp, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singleReturnPlan(1), OperationId: "cancel-pending",
	})
	// Complete in the engine without invoking the API dispatcher, creating the
	// exact completion-boundary pending state.
	s.mission.Observe(1, 14, 100, 0)
	if s.mission.Snapshot().ReturnState != mission.ReturnStatePending {
		t.Fatal("Return was not pending")
	}
	s.missionDispatchMu.Lock()
	if !s.missionAuthorityActiveLocked() || !s.missionOwnsDroneLocked(1) {
		s.missionDispatchMu.Unlock()
		t.Fatal("pending Return must block competing mission/swarm/manual navigation")
	}
	s.missionDispatchMu.Unlock()
	_, _ = s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: resp.RunId})
	s.dispatchReturnAuthority()
	if cmd.rtlCalls != 0 || s.mission.Snapshot().ReturnState != mission.ReturnStateSuppressed {
		t.Fatalf("cancel revived Return: calls=%d state=%+v", cmd.rtlCalls, s.mission.Snapshot())
	}
}

func TestTakeoverPreemptsDelayedReturnWithoutWaitingForACK(t *testing.T) {
	cmd := &apiMissionCommander{rtlBlock: make(chan struct{}), rtlStarted: make(chan context.Context, 1)}
	s := newReturnServer(cmd, (*mission.Engine).EnableAuthority)
	_, _ = s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singleReturnPlan(1), OperationId: "preempt-return",
	})
	s.mission.Observe(1, 14, 100, 0)
	done := make(chan struct{})
	go func() { s.dispatchReturnAuthority(); close(done) }()
	var sendCtx context.Context
	select {
	case sendCtx = <-cmd.rtlStarted:
	case <-time.After(time.Second):
		t.Fatal("Return did not begin")
	}
	start := time.Now()
	s.missionDispatchMu.Lock()
	accepted := s.cancelMissionForOperatorTargetsLocked([]uint32{1})
	s.missionDispatchMu.Unlock()
	if !accepted || time.Since(start) > 250*time.Millisecond {
		t.Fatalf("takeover accepted=%v latency=%v", accepted, time.Since(start))
	}
	select {
	case <-sendCtx.Done():
	case <-time.After(time.Second):
		t.Fatal("Return context/final-write ownership was not revoked")
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("preempted Return did not unwind")
	}
	if cmd.rtlCalls != 0 || s.mission.Snapshot().ReturnState != mission.ReturnStateSuppressed {
		t.Fatalf("stale Return committed: calls=%d state=%+v", cmd.rtlCalls, s.mission.Snapshot())
	}
}

func TestFailsafeSuppressesPendingReturnAndCommandsNothing(t *testing.T) {
	cmd := &apiMissionCommander{}
	s := newReturnServer(cmd, (*mission.Engine).EnableAuthority)
	_, _ = s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singleReturnPlan(1), OperationId: "failsafe-return",
	})
	s.mission.Observe(1, 14, 100, 0)
	s.handleMissionSafetyEvent(&pb.Event{Level: pb.EventLevel_EVENT_LEVEL_ALARM,
		DroneId: 1, Category: "battery", Message: "critical"})
	s.dispatchReturnAuthority()
	if cmd.rtlCalls != 0 {
		t.Fatalf("mission Return competed with failsafe: %d", cmd.rtlCalls)
	}
	state := s.mission.Snapshot()
	if state.State != mission.StateInterrupted || state.ReturnState != mission.ReturnStateSuppressed {
		t.Fatalf("failsafe state=%+v", state)
	}
}

func groupedReturnDomainPlan(ids ...uint32) mission.MissionPlan {
	return mission.MissionPlan{
		PlanID: "group-return", Mode: mission.ModeGrouped, Participants: ids, RtlAfter: true,
		Routes: []mission.Route{{Points: []mission.Waypoint{{Seq: 0, Lat: 14, Lon: 100}}}},
	}
}

func prepareCompletedGroupedReturn(t *testing.T, s *Server) {
	t.Helper()
	if _, err := s.mission.Start(groupedReturnDomainPlan(1, 2, 3)); err != nil {
		t.Fatal(err)
	}
	intents, ok, err := s.mission.ClaimAuthorityGroupedGotos()
	if err != nil || !ok || len(intents) != 3 {
		t.Fatalf("group claim=%+v ok=%v err=%v", intents, ok, err)
	}
	for _, in := range intents {
		s.mission.Observe(in.DroneID, in.Lat, in.Lon, 0)
	}
}

func TestGroupedReturnSendsExactlyOneRTLPerParticipantAfterBarrier(t *testing.T) {
	cmd := &apiMissionCommander{}
	s := newReturnServer(cmd, (*mission.Engine).EnableGroupedMultiAuthority)
	prepareCompletedGroupedReturn(t, s)
	s.dispatchReturnAuthority()
	if cmd.rtlCalls != 3 || len(cmd.rtlIDs) != 3 {
		t.Fatalf("group Return calls=%d ids=%v", cmd.rtlCalls, cmd.rtlIDs)
	}
	for i := 0; i < 3; i++ {
		s.dispatchReturnAuthority()
	}
	if cmd.rtlCalls != 3 {
		t.Fatalf("group Return duplicated: %d", cmd.rtlCalls)
	}
}

func TestGroupedReturnTakeoverStopsAllLaterTargets(t *testing.T) {
	cmd := &apiMissionCommander{rtlBlock: make(chan struct{}), rtlStarted: make(chan context.Context, 1)}
	s := newReturnServer(cmd, (*mission.Engine).EnableGroupedMultiAuthority)
	prepareCompletedGroupedReturn(t, s)
	done := make(chan struct{})
	go func() { s.dispatchReturnAuthority(); close(done) }()
	select {
	case <-cmd.rtlStarted:
	case <-time.After(time.Second):
		t.Fatal("first group Return did not start")
	}
	s.missionDispatchMu.Lock()
	accepted := s.cancelMissionForOperatorTargetsLocked([]uint32{1})
	s.missionDispatchMu.Unlock()
	if !accepted {
		t.Fatal("takeover did not own group Return target")
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("group Return did not unwind")
	}
	if cmd.rtlCalls != 0 || len(cmd.rtlIDs) != 0 {
		t.Fatalf("stale later-target RTL escaped: calls=%d ids=%v", cmd.rtlCalls, cmd.rtlIDs)
	}
}

func TestSwarmFollowerFailsafeBeforeNaturalCompletionSuppressesReturn(t *testing.T) {
	failsafe := map[uint32]bool{3: true}
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3}, 0,
		func(id uint32) bool { return failsafe[id] })
	if s.mission.Interrupt(3, "battery", "critical") {
		t.Fatal("follower failsafe must not interrupt leader route before natural completion")
	}
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	starts, revokes, requested := fake.snapshot()
	if starts != 0 || revokes != 0 || len(requested) != 0 {
		t.Fatalf("failsafe follower entered SWARM Return: starts=%d revokes=%d requested=%v",
			starts, revokes, requested)
	}
	if got := s.mission.Snapshot(); got.ReturnState != mission.ReturnStateSuppressed {
		t.Fatalf("failsafe Return must fail closed as suppressed: %+v", got)
	}
	for i := 0; i < 3; i++ {
		s.dispatchReturnAuthority()
	}
	starts, _, _ = fake.snapshot()
	if starts != 0 {
		t.Fatal("remaining participants reintroduced the failsafe follower into Return")
	}
}

func TestSwarmFollowerFailsafeAfterReturnRegistrationRevokesReturn(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3}, 0, nil)
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	if got := s.mission.Snapshot().ReturnState; got != mission.ReturnStateReturning {
		t.Fatalf("Return state=%s, want RETURNING", got)
	}
	s.handleMissionSafetyEvent(&pb.Event{
		Level: pb.EventLevel_EVENT_LEVEL_ALARM, DroneId: 3,
		Category: "battery", Message: "critical",
	})
	waitReturnState(t, s, mission.ReturnStateSuppressed)
	starts, revokes, _ := fake.snapshot()
	if starts != 1 || revokes != 1 {
		t.Fatalf("registered Return was not revoked on follower failsafe: starts=%d revokes=%d", starts, revokes)
	}
}

func TestExcludedAndUnrelatedTakeoverDoNotCancelActiveSwarmReturn(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3, 4}, 3, nil)
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	starts, revokes, requested := fake.snapshot()
	if starts != 1 || revokes != 0 || len(requested) != 3 || requested[0] != 1 || requested[1] != 2 || requested[2] != 4 {
		t.Fatalf("active Return registration=%d revokes=%d participants=%v", starts, revokes, requested)
	}

	for _, id := range []uint32{3, 99} {
		batch := s.prepareTakeoverBatch(context.Background(), "Hold", "", []uint32{id}, takeoverPriorityNavigation)
		if batch.rejected[id] != nil || batch.claims[id] == nil {
			t.Fatalf("takeover D%d was not accepted: %+v", id, batch.rejected[id])
		}
		batch.release(s)
		_, revokes, _ = fake.snapshot()
		if revokes != 0 || s.mission.Snapshot().ReturnState != mission.ReturnStateReturning {
			t.Fatalf("takeover D%d disturbed unrelated Return: revokes=%d state=%s",
				id, revokes, s.mission.Snapshot().ReturnState)
		}
	}

	fake.finish(swarm.ReturnResult{Outcome: swarm.ReturnOutcomeSucceeded, Reason: "landed"})
	waitReturnState(t, s, mission.ReturnStateCompleted)
}

func TestRejectedWeakerTakeoverHasZeroActiveSwarmReturnSideEffect(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3, 4}, 3, nil)
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	if got := s.mission.Snapshot().ReturnState; got != mission.ReturnStateReturning {
		t.Fatalf("Return state=%s, want RETURNING", got)
	}

	// A stronger already-accepted KILL owns D2. A later weaker HOLD names a real
	// Return participant, but because HOLD is rejected it must have zero authority
	// side effects: no Return revoke/suppress and no disturbance of KILL ownership.
	s.missionDispatchMu.Lock()
	killLease, ok := s.preemptReserveLocked([]uint32{2}, func() {}, takeoverPriorityKill)
	s.missionDispatchMu.Unlock()
	if !ok || killLease == nil {
		t.Fatal("failed to establish stronger KILL reservation")
	}
	defer s.releaseLease(killLease)

	batch := s.prepareTakeoverBatch(context.Background(), "Hold", "weaker-hold", []uint32{2, 99}, takeoverPriorityNavigation)
	defer batch.release(s)
	if batch.rejected[2] == nil || batch.claims[2] != nil {
		t.Fatalf("weaker takeover must be rejected behind KILL: rejected=%+v claim=%+v",
			batch.rejected[2], batch.claims[2])
	}
	if batch.rejected[99] != nil || batch.claims[99] == nil {
		t.Fatalf("unrelated D99 should still be accepted: rejected=%+v claim=%+v",
			batch.rejected[99], batch.claims[99])
	}
	starts, revokes, requested := fake.snapshot()
	if starts != 1 || revokes != 0 || len(requested) != 3 ||
		requested[0] != 1 || requested[1] != 2 || requested[2] != 4 {
		t.Fatalf("rejected takeover disturbed SWARM Return: starts=%d revokes=%d participants=%v",
			starts, revokes, requested)
	}
	if got := s.mission.Snapshot().ReturnState; got != mission.ReturnStateReturning {
		t.Fatalf("rejected takeover changed Return state to %s", got)
	}
	s.missionDispatchMu.Lock()
	stillKill := s.reservations[2] == killLease
	s.missionDispatchMu.Unlock()
	if !stillKill {
		t.Fatal("rejected weaker takeover disturbed stronger KILL reservation")
	}

	fake.finish(swarm.ReturnResult{Outcome: swarm.ReturnOutcomeSucceeded, Reason: "landed"})
	waitReturnState(t, s, mission.ReturnStateCompleted)
}

func TestMultipleLeaderSuccessionsReturnCurrentMembershipOnly(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3, 4}, 0, nil)
	for _, oldLeader := range []uint32{1, 2} {
		transition, ok := s.mission.BeginSwarmOperatorTakeover(oldLeader, func(uint32) bool { return true })
		if !ok || transition.Duplicate || transition.Interrupted {
			t.Fatalf("leader D%d succession failed: %+v ok=%v", oldLeader, transition, ok)
		}
		if !s.mission.CompleteSwarmOperatorTakeover(
			transition.RunID, transition.Generation, true, "test succession") {
			t.Fatalf("leader D%d succession did not commit", oldLeader)
		}
	}
	if got := s.mission.Snapshot(); got.CurrentLeaderID != 3 || len(got.ActiveParticipants) != 2 {
		t.Fatalf("current succession membership=%+v", got)
	}
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	starts, revokes, requested := fake.snapshot()
	if starts != 1 || revokes != 0 || len(requested) != 2 || requested[0] != 3 || requested[1] != 4 {
		t.Fatalf("post-succession Return starts=%d revokes=%d participants=%v", starts, revokes, requested)
	}
	for _, excluded := range []uint32{1, 2} {
		batch := s.prepareTakeoverBatch(context.Background(), "Hold", "", []uint32{excluded}, takeoverPriorityNavigation)
		batch.release(s)
	}
	_, revokes, _ = fake.snapshot()
	if revokes != 0 || s.mission.Snapshot().ReturnState != mission.ReturnStateReturning {
		t.Fatalf("excluded prior leaders cancelled current Return: revokes=%d state=%s",
			revokes, s.mission.Snapshot().ReturnState)
	}
	fake.finish(swarm.ReturnResult{Outcome: swarm.ReturnOutcomeSucceeded, Reason: "landed"})
	waitReturnState(t, s, mission.ReturnStateCompleted)
}

func TestReturnParticipantTakeoverRevokesAndCannotReportSuccess(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2, 3, 4}, 3, nil)
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()

	batch := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{2}, takeoverPriorityStopAll)
	if batch.rejected[2] != nil || batch.claims[2] == nil {
		t.Fatalf("Return participant takeover rejected: %+v", batch.rejected[2])
	}
	batch.release(s)
	waitReturnState(t, s, mission.ReturnStateSuppressed)
	_, revokes, _ := fake.snapshot()
	if revokes != 1 {
		t.Fatalf("relevant takeover revokes=%d, want 1", revokes)
	}

	duplicate := s.prepareTakeoverBatch(context.Background(), "StopAll", "", []uint32{2}, takeoverPriorityStopAll)
	duplicate.release(s)
	_, revokes, _ = fake.snapshot()
	if revokes != 1 || s.mission.Snapshot().ReturnState != mission.ReturnStateSuppressed {
		t.Fatalf("duplicate takeover corrupted completion: revokes=%d state=%s",
			revokes, s.mission.Snapshot().ReturnState)
	}
}

func TestSwarmReturnFailedResultIsNotCompleted(t *testing.T) {
	s, fake := newSwarmReturnDispatchServer(t, []uint32{1, 2}, 0, nil)
	completeSwarmMission(t, s)
	s.dispatchReturnAuthority()
	fake.finish(swarm.ReturnResult{Outcome: swarm.ReturnOutcomeFailed, Reason: "landing failed"})
	waitReturnState(t, s, mission.ReturnStateFailed)
}
