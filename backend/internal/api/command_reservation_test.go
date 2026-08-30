package api

import (
	"context"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/command"
)

// newIdemServer builds a Server with a real command.Service (so the request_id
// idem store is live) but no fleet — the reservation helpers are driven with a
// controllable send, proving the request_id × reservation interaction.
func newIdemServer(t *testing.T) *Server {
	t.Helper()
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(aud.Close) // release the audit file before TempDir removal (Windows lock)
	return &Server{
		reservations: make(map[uint32]*cmdLease),
		cmd:          command.NewService(nil, nil, aud),
	}
}

func armOK(id uint32, command string) *pb.CommandResult {
	return &pb.CommandResult{Ok: true, DroneId: id, Command: command,
		Message: "ACCEPTED", Outcome: pb.CommandOutcome_OUTCOME_ACCEPTED}
}

// #1 HIGH: a same request_id retry that arrives while the first is in-flight and
// holding the reservation must be deduped (wait/replay) BEFORE reaching the
// reservation — it must NOT busy-reject itself or double-send.
func TestSameRequestIdNormalReplaysNotBusyRejects(t *testing.T) {
	s := newIdemServer(t)
	var calls atomic.Int32
	entered := make(chan struct{})
	release := make(chan struct{})
	send := func(ctx context.Context, id uint32) *pb.CommandResult {
		if calls.Add(1) == 1 {
			close(entered)
			<-release // first blocks so the retry overlaps it
		}
		return armOK(id, "ARM")
	}
	res1 := make(chan *pb.CommandResult, 1)
	go func() { res1 <- s.idempotentNormal(context.Background(), "ARM", "req-X", 1, send) }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("first command never reached its send")
	}
	res2 := make(chan *pb.CommandResult, 1)
	go func() { res2 <- s.idempotentNormal(context.Background(), "ARM", "req-X", 1, send) }()
	time.Sleep(150 * time.Millisecond) // let the retry reach the idem in-flight wait
	close(release)
	r1 := <-res1
	r2 := <-res2
	if got := calls.Load(); got != 1 {
		t.Fatalf("send executed %d times, want exactly 1 (idempotent, not double-send)", got)
	}
	if r1 == nil || !r1.Ok {
		t.Fatalf("first must succeed: %+v", r1)
	}
	if r2 == nil || !r2.Ok || strings.Contains(r2.Message, "another command is in progress") {
		t.Fatalf("same request_id retry must replay, not busy-reject: %+v", r2)
	}
}

// #1 HIGH: a same request_id takeover retry must NOT cancel/preempt the ORIGINAL
// identical request — it is deduped (wait/replay) before preemptReserveLocked.
func TestSameRequestIdTakeoverDoesNotCancelOriginal(t *testing.T) {
	s := newIdemServer(t)
	var calls atomic.Int32
	var firstCancelled atomic.Bool
	entered := make(chan struct{})
	release := make(chan struct{})
	send := func(ctx context.Context, id uint32) *pb.CommandResult {
		if calls.Add(1) == 1 {
			close(entered)
			select {
			case <-release:
			case <-ctx.Done():
				firstCancelled.Store(true)
			}
		}
		return armOK(id, "LAND")
	}
	res1 := make(chan *pb.CommandResult, 1)
	go func() {
		res1 <- s.idempotentTakeover(context.Background(), "Land", "req-Y", 1, takeoverPriorityLand, send)
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("first takeover never reached its send")
	}
	res2 := make(chan *pb.CommandResult, 1)
	go func() {
		res2 <- s.idempotentTakeover(context.Background(), "Land", "req-Y", 1, takeoverPriorityLand, send)
	}()
	time.Sleep(150 * time.Millisecond)
	close(release)
	<-res1
	<-res2
	if got := calls.Load(); got != 1 {
		t.Fatalf("takeover send executed %d times, want 1 (idempotent replay)", got)
	}
	if firstCancelled.Load() {
		t.Fatal("same request_id takeover retry must NOT cancel/preempt the original identical request")
	}
}

// A COMPLETED same request_id replays without a new send (no double FC command).
func TestCompletedSameRequestIdReplaysWithoutResend(t *testing.T) {
	s := newIdemServer(t)
	var calls atomic.Int32
	send := func(ctx context.Context, id uint32) *pb.CommandResult {
		calls.Add(1)
		return armOK(id, "ARM")
	}
	r1 := s.idempotentNormal(context.Background(), "ARM", "req-Z", 1, send)
	r2 := s.idempotentNormal(context.Background(), "ARM", "req-Z", 1, send)
	if got := calls.Load(); got != 1 {
		t.Fatalf("send executed %d times, want 1 (completed replay)", got)
	}
	if !r1.Ok || !r2.Ok || !strings.Contains(r2.Message, "idempotent replay") {
		t.Fatalf("completed same request_id must replay: r1=%+v r2=%+v", r1, r2)
	}
	// the reservation must be free again (both attempts released their lease)
	s.missionDispatchMu.Lock()
	reserved := s.droneReservedLocked(1)
	s.missionDispatchMu.Unlock()
	if reserved {
		t.Fatal("reservation leaked after idempotent replay")
	}
}

// V3-S07 Part B — command reservation / emergency-contention regression.
//
// The reservation model lets a normal command release missionDispatchMu before
// its long FC ACK wait while still blocking StartMission, and lets an
// emergency/takeover preempt an in-flight normal op instead of serializing
// behind it.  These tests prove the state machine and the no-dual-authority
// invariants without a real MAVLink fleet.

func newReservationServer() *Server {
	return &Server{reservations: make(map[uint32]*cmdLease)}
}

func TestSwarmNavigationCannotCrossAcceptedCommandReservation(t *testing.T) {
	for _, tc := range []struct {
		name string
		req  *pb.SwarmControlRequest
	}{
		{name: "formation start", req: &pb.SwarmControlRequest{
			Action: pb.SwarmControlRequest_START, RequestId: "start-after-kill",
		}},
		{name: "targeted return", req: &pb.SwarmControlRequest{
			Action: pb.SwarmControlRequest_RETURN, DroneIds: []uint32{7}, RequestId: "return-after-kill",
		}},
		{name: "whole-swarm return", req: &pb.SwarmControlRequest{
			Action: pb.SwarmControlRequest_RETURN, RequestId: "all-return-after-kill",
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := newReservationServer()
			s.missionDispatchMu.Lock()
			killLease, ok := s.preemptReserveLocked(
				[]uint32{7}, func() {}, takeoverPriorityKill)
			s.missionDispatchMu.Unlock()
			if !ok || killLease == nil {
				t.Fatal("failed to establish KILL reservation")
			}
			defer s.releaseLease(killLease)

			res, err := s.SwarmControl(context.Background(), tc.req)
			if err != nil {
				t.Fatal(err)
			}
			if res == nil || res.Ok || !strings.Contains(res.Message, "another command is in progress") {
				t.Fatalf("swarm navigation must fail closed behind accepted KILL: %+v", res)
			}
			s.missionDispatchMu.Lock()
			stillKill := s.reservations[7] == killLease
			s.missionDispatchMu.Unlock()
			if !stillKill {
				t.Fatal("rejected swarm navigation disturbed the KILL reservation")
			}
		})
	}
}

func TestTakeoverRevokesReturnOnlyAfterTargetAcceptanceInsideOwnershipCriticalSection(t *testing.T) {
	b, err := os.ReadFile("command_reservation.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)

	checkOrder := func(signature, acceptedMarker string) {
		t.Helper()
		start := strings.Index(src, signature)
		if start < 0 {
			t.Fatalf("missing %s", signature)
		}
		rest := src[start+len(signature):]
		end := strings.Index(rest, "\nfunc ")
		if end < 0 {
			t.Fatalf("could not bound %s", signature)
		}
		body := rest[:end]
		lock := strings.Index(body, "s.missionDispatchMu.Lock()")
		accepted := strings.Index(body, acceptedMarker)
		intersect := strings.Index(body, "s.cancelMissionForOperatorTargetsLocked(")
		if lock < 0 || accepted < lock || intersect < accepted {
			t.Fatalf("%s must accept takeover under missionDispatchMu before Return intersection/revocation", signature)
		}
	}
	checkOrder("func (s *Server) idempotentTakeover(", "s.preemptReserveLocked(")
	checkOrder("func (s *Server) prepareTakeoverBatch(", "accepted = append(accepted, id)")

	serverBytes, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	serverSrc := string(serverBytes)
	start := strings.Index(serverSrc, "func (s *Server) cancelMissionForOperatorTargetsLocked(")
	if start < 0 {
		t.Fatal("missing Return intersection helper")
	}
	body := serverSrc[start:]
	if end := strings.Index(body, "\nfunc "); end >= 0 {
		body = body[:end]
	}
	owned := strings.Index(body, "if !owned")
	revoke := strings.Index(body, "coordinator.RevokeReturnNavigation()")
	if owned < 0 || revoke < owned {
		t.Fatal("Return navigation may be revoked only after accepted-target intersection succeeds")
	}
}

func (s *Server) reserveNormal(t *testing.T, ids []uint32, cancel context.CancelFunc) *cmdLease {
	t.Helper()
	s.missionDispatchMu.Lock()
	lease, ok := s.tryReserveLocked(ids, cancel)
	s.missionDispatchMu.Unlock()
	if !ok {
		t.Fatalf("expected to reserve %v", ids)
	}
	return lease
}

func TestTryReserveRejectsOverlappingNormalOps(t *testing.T) {
	s := newReservationServer()
	s.reserveNormal(t, []uint32{1, 2}, func() {})

	s.missionDispatchMu.Lock()
	_, ok := s.tryReserveLocked([]uint32{2, 3}, func() {}) // 2 overlaps
	s.missionDispatchMu.Unlock()
	if ok {
		t.Fatal("normal op must not reserve a drone already reserved")
	}
	// drone 3 must not have been partially reserved by the failed attempt
	s.missionDispatchMu.Lock()
	three := s.droneReservedLocked(3)
	s.missionDispatchMu.Unlock()
	if three {
		t.Fatal("failed reservation must not leave a partial reservation")
	}
}

func TestPreemptCancelsNormalAndSupersededTakeover(t *testing.T) {
	s := newReservationServer()
	normalCtx, normalCancel := context.WithCancel(context.Background())
	preempted := make(chan struct{})
	go func() { <-normalCtx.Done(); close(preempted) }() // simulates the FC send unblocking
	normalLease := s.reserveNormal(t, []uint32{7}, normalCancel)

	firstCancelled := make(chan struct{})
	s.missionDispatchMu.Lock()
	first, ok := s.preemptReserveLocked(
		[]uint32{7}, func() { close(firstCancelled) }, takeoverPriorityNavigation)
	s.missionDispatchMu.Unlock()
	if !ok || first == nil {
		t.Fatal("takeover should preempt a normal reservation")
	}

	select {
	case <-preempted:
	case <-time.After(2 * time.Second):
		t.Fatal("takeover did not preempt the in-flight normal op")
	}
	// The preempted normal op releasing its lease must NOT remove the takeover's.
	s.releaseLease(normalLease)
	s.missionDispatchMu.Lock()
	stillOwned := s.reservations[7] == first
	s.missionDispatchMu.Unlock()
	if !stillOwned {
		t.Fatal("preempted normal release removed the takeover reservation")
	}

	// Equal-priority newer operator intent wins deterministically: the older
	// takeover is cancelled instead of both racing their FC writes.
	s.missionDispatchMu.Lock()
	second, ok := s.preemptReserveLocked(
		[]uint32{7}, func() {}, takeoverPriorityNavigation)
	s.missionDispatchMu.Unlock()
	if !ok || second == nil {
		t.Fatal("equal-priority newer takeover should supersede the older one")
	}
	select {
	case <-firstCancelled:
	case <-time.After(2 * time.Second):
		t.Fatal("superseded takeover was not cancelled")
	}
}

func TestWeakerTakeoverCannotPreemptKill(t *testing.T) {
	s := newReservationServer()
	killCancelled := make(chan struct{})
	s.missionDispatchMu.Lock()
	killLease, ok := s.preemptReserveLocked(
		[]uint32{9}, func() { close(killCancelled) }, takeoverPriorityKill)
	s.missionDispatchMu.Unlock()
	if !ok || killLease == nil {
		t.Fatal("kill reservation should succeed")
	}

	s.missionDispatchMu.Lock()
	weaker, ok := s.preemptReserveLocked(
		[]uint32{9}, func() {}, takeoverPriorityNavigation)
	stillKill := s.reservations[9] == killLease
	s.missionDispatchMu.Unlock()
	if ok || weaker != nil {
		t.Fatal("weaker takeover must not supersede an in-flight KILL")
	}
	if !stillKill {
		t.Fatal("weaker takeover replaced the KILL reservation")
	}
	select {
	case <-killCancelled:
		t.Fatal("weaker takeover cancelled the KILL context")
	case <-time.After(100 * time.Millisecond):
	}
}

func TestKillPreemptsWeakerTakeover(t *testing.T) {
	s := newReservationServer()
	weakerCancelled := make(chan struct{})
	s.missionDispatchMu.Lock()
	_, ok := s.preemptReserveLocked(
		[]uint32{11}, func() { close(weakerCancelled) }, takeoverPriorityNavigation)
	s.missionDispatchMu.Unlock()
	if !ok {
		t.Fatal("initial navigation takeover should reserve")
	}

	s.missionDispatchMu.Lock()
	killLease, ok := s.preemptReserveLocked(
		[]uint32{11}, func() {}, takeoverPriorityKill)
	isKill := s.reservations[11] == killLease
	s.missionDispatchMu.Unlock()
	if !ok || killLease == nil || !isKill {
		t.Fatal("KILL must supersede a weaker takeover")
	}
	select {
	case <-weakerCancelled:
	case <-time.After(2 * time.Second):
		t.Fatal("KILL did not cancel the weaker takeover")
	}
}

func TestReleaseLeaseOnlyClearsOwnSlot(t *testing.T) {
	s := newReservationServer()
	a := s.reserveNormal(t, []uint32{1}, func() {})
	// Manually replace slot 1 with a different lease (as a takeover would).
	b := &cmdLease{ids: []uint32{1}, takeover: true, cancel: func() {}}
	s.missionDispatchMu.Lock()
	s.reservations[1] = b
	s.missionDispatchMu.Unlock()

	s.releaseLease(a) // stale owner: must be a no-op
	s.missionDispatchMu.Lock()
	still := s.reservations[1] == b
	s.missionDispatchMu.Unlock()
	if !still {
		t.Fatal("releaseLease removed a slot it no longer owned")
	}
	s.releaseLease(b)
	s.missionDispatchMu.Lock()
	gone := !s.droneReservedLocked(1)
	s.missionDispatchMu.Unlock()
	if !gone {
		t.Fatal("releaseLease did not clear its own slot")
	}
}

func TestStartMissionFailsClosedWhileParticipantReserved(t *testing.T) {
	s := newMissionServer()
	s.reservations = make(map[uint32]*cmdLease)
	s.missionAuthority = true
	s.mission.EnableAuthority()
	s.missionExec = &apiMissionCommander{}

	lease := s.reserveNormal(t, []uint32{1}, func() {})
	plan := pbGroupedPlan("reserved-participant")
	plan.Participants = []uint32{1}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-reserved",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok || !strings.Contains(resp.Message, "manual command is in progress") {
		t.Fatalf("StartMission must fail closed while a participant is reserved: %+v", resp)
	}
	// No run may have been created.
	if snap := s.mission.Snapshot(); snap.Active {
		t.Fatal("StartMission created a run despite a reserved participant")
	}
	// After the manual op releases, StartMission may proceed.
	s.releaseLease(lease)
	resp2, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-reserved",
	})
	if !resp2.Ok {
		t.Fatalf("StartMission must succeed once the reservation is released: %+v", resp2)
	}
}

func TestGotoBusyRejectWhileDroneReservedByAnotherOp(t *testing.T) {
	s := newMissionServer()
	s.reservations = make(map[uint32]*cmdLease)
	s.reserveNormal(t, []uint32{1}, func() {})
	// s.cmd is nil; a correct busy reject must return before ever touching it.
	resp, err := s.Goto(context.Background(), &pb.GotoRequest{
		DroneId: 1, Lat: 14, Lon: 100, Alt: 20, RequestId: "g",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok || !strings.Contains(resp.Message, "another command is in progress") {
		t.Fatalf("Goto must busy-reject a drone reserved by another op: %+v", resp)
	}
}

func TestSetModeAndRcMoveBusyRejectWhileReserved(t *testing.T) {
	s := newMissionServer()
	s.reservations = make(map[uint32]*cmdLease)
	s.reserveNormal(t, []uint32{1}, func() {})

	sm, _ := s.SetMode(context.Background(), &pb.SetModeRequest{
		Target: &pb.Target{DroneIds: []uint32{1}}, RequestId: "m",
	})
	if sm.Ok || !strings.Contains(sm.Message, "another command is in progress") {
		t.Fatalf("SetMode must busy-reject a reserved drone: %+v", sm)
	}
	rc, _ := s.RcMove(context.Background(), &pb.RcMoveRequest{
		Target: &pb.Target{DroneIds: []uint32{1}}, Speed: 1,
	})
	if rc.Ok || !strings.Contains(rc.Message, "another command is in progress") {
		t.Fatalf("RcMove must busy-reject a reserved drone: %+v", rc)
	}
}

// ── V3-S07/S08 review fixes ──────────────────────────────────────────────

func newBlockingMissionServer(t *testing.T, planID string) (*Server, *apiMissionCommander, chan struct{}, context.Context) {
	t.Helper()
	s := newMissionServer()
	s.reservations = make(map[uint32]*cmdLease)
	s.missionAuthority = true
	s.mission.EnableAuthority()
	cmd := &apiMissionCommander{block: make(chan struct{}), started: make(chan context.Context, 1)}
	s.missionExec = cmd
	plan := pbGroupedPlan(planID)
	plan.Participants = []uint32{1}
	go s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan, OperationId: "op-" + planID})
	select {
	case sendCtx := <-cmd.started:
		return s, cmd, cmd.block, sendCtx
	case <-time.After(2 * time.Second):
		t.Fatal("initial mission send never started")
		return nil, nil, nil, nil
	}
}

// H2: the mission GOTO/HOLD send must run with missionDispatchMu RELEASED so an
// emergency/takeover is never serialized behind the mission's FC ACK.
func TestMissionSendRunsWithLockReleased(t *testing.T) {
	s, _, block, _ := newBlockingMissionServer(t, "lock-released")
	defer close(block)
	if !s.missionDispatchMu.TryLock() {
		t.Fatal("missionDispatchMu is held during the mission send — emergency would be serialized behind it")
	}
	s.missionDispatchMu.Unlock()
}

// H2: an operator takeover on the mission drone must make the run terminal AND
// cancel the in-flight mission send so it never reaches the FC.
func TestOperatorTakeoverCancelsInFlightMissionSend(t *testing.T) {
	s, _, block, sendCtx := newBlockingMissionServer(t, "takeover-midsend")
	s.missionDispatchMu.Lock()
	ok := s.cancelMissionForOperatorTargetsLocked([]uint32{1})
	s.missionDispatchMu.Unlock()
	if !ok {
		close(block)
		t.Fatal("takeover should cancel the owned mission")
	}
	select {
	case <-sendCtx.Done():
	case <-time.After(2 * time.Second):
		close(block)
		t.Fatal("in-flight mission send was not cancelled by the takeover")
	}
	if st := missionState(t, s); st.State != pb.MissionRunState_MISSION_STATE_CANCELLED {
		t.Fatalf("mission must be terminal after takeover: %+v", st)
	}
}

// H2: CancelMission must also cancel the in-flight mission send.
func TestCancelMissionCancelsInFlightSend(t *testing.T) {
	s, _, block, sendCtx := newBlockingMissionServer(t, "cancel-midsend")
	runID := s.mission.Snapshot().RunID
	_, _ = s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: runID})
	select {
	case <-sendCtx.Done():
	case <-time.After(2 * time.Second):
		close(block)
		t.Fatal("CancelMission did not cancel the in-flight mission send")
	}
}

// Review round 2: there must be exactly one mission send slot.  A second
// observer/dispatch while the first send is still in flight must not claim a new
// intent or overwrite the original cancel/guard handle.
func TestMissionSendIsSingleFlightAndCancelSlotCannotBeOverwritten(t *testing.T) {
	s, _, block, sendCtx := newBlockingMissionServer(t, "single-flight")
	defer close(block)

	s.missionDispatchMu.Lock()
	if !s.missionSendActive {
		s.missionDispatchMu.Unlock()
		t.Fatal("expected initial mission send to be marked active")
	}
	gen := s.missionSendGen
	_, ok := s.claimMissionSendLocked()
	if ok {
		s.missionDispatchMu.Unlock()
		t.Fatal("second mission send was claimed while first send was still active")
	}
	if s.missionSendGen != gen {
		s.missionDispatchMu.Unlock()
		t.Fatal("second claim attempt overwrote the in-flight mission generation")
	}
	s.cancelInflightMissionSendLocked()
	s.missionDispatchMu.Unlock()

	select {
	case <-sendCtx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("original in-flight mission send was no longer cancellable")
	}
}

// A cancelled mission send is harmless (guard tripped + context cancelled, so
// command.Service refuses any FC write). CancelMission must therefore free the
// single-flight mission-send slot immediately so a new mission can start —
// without waiting for the old (cancelled) transport call to finish unwinding.
func TestCancelFreesMissionSendSlotImmediately(t *testing.T) {
	s, _, block, sendCtx := newBlockingMissionServer(t, "cancel-unwind")
	defer close(block)

	s.missionDispatchMu.Lock()
	active := s.missionSendActive
	genBefore := s.missionSendGen
	s.missionDispatchMu.Unlock()
	if !active {
		t.Fatal("expected the initial mission send to hold the single-flight slot")
	}

	oldRunID := s.mission.Snapshot().RunID
	if _, err := s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: oldRunID}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-sendCtx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("CancelMission did not cancel the in-flight mission send")
	}

	s.missionDispatchMu.Lock()
	freed := !s.missionSendActive
	genBumped := s.missionSendGen != genBefore
	s.missionDispatchMu.Unlock()
	if !freed {
		t.Fatal("CancelMission must free the single-flight mission-send slot immediately")
	}
	if !genBumped {
		t.Fatal("CancelMission must bump the send generation so the old send's return is a no-op")
	}
}

// H2: command.Service.Goto/Hold must refuse a preempted send before the FC write.
func TestCommandServiceRefusesPreemptedSend(t *testing.T) {
	b, err := os.ReadFile("../command/service.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	gotoStart := strings.Index(src, "func (s *Service) Goto(")
	holdStart := strings.Index(src, "func (s *Service) Hold(")
	if gotoStart < 0 || holdStart < 0 {
		t.Fatal("could not find Goto/Hold service methods")
	}
	gotoBody := src[gotoStart : gotoStart+2200]
	holdBody := src[holdStart : holdStart+5200]
	if !strings.Contains(gotoBody, "ctx.Err() != nil") ||
		!strings.Contains(gotoBody, "d.GotoContext(ctx") {
		t.Fatal("Goto must precheck cancellation and use the guarded context-aware final FC write")
	}
	if !strings.Contains(holdBody, "ctx.Err() != nil") ||
		!strings.Contains(holdBody, "d.GotoContext(ctx") ||
		!strings.Contains(holdBody, "d.MoveVelocityContext(ctx") {
		t.Fatal("Hold must use guarded context-aware FC writes on every final hold path")
	}

	fleetSrcBytes, err := os.ReadFile("../fleet/drone.go")
	if err != nil {
		t.Fatal(err)
	}
	fleetSrc := string(fleetSrcBytes)
	if !strings.Contains(fleetSrc, "func guardedSend(") ||
		!strings.Contains(fleetSrc, "guard.DoSend(") ||
		!strings.Contains(fleetSrc, "guardedSend(ctx, func() error { return d.conn.Send(msg) })") {
		t.Fatal("fleet transport must serialize cancellation with the actual conn.Send write")
	}
}

// H3: SetMode/RcMove must establish their batch reservations in a short critical
// section and execute FC sends only after that helper has released the lock.
func TestSetModeAndRcMoveSendOutsideLock(t *testing.T) {
	b, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	for _, fn := range []string{"func (s *Server) SetMode(", "func (s *Server) RcMove("} {
		start := strings.Index(src, fn)
		end := strings.Index(src[start+1:], "\nfunc (s *Server) ")
		body := src[start : start+1+end]
		if !strings.Contains(body, "s.runNormalBatch(") {
			t.Fatalf("%s must dispatch through runNormalBatch so every target is claimed before FC send", fn)
		}
		if strings.Contains(body, "defer s.missionDispatchMu.Unlock()") {
			t.Fatalf("%s must not hold missionDispatchMu across its send", fn)
		}
	}
}

// H4: Takeoff preemption must be derived from typed lease ownership, not human
// error text, and rollback LAND must itself stay behind the still-current claim.
func TestTakeoffSkipsRollbackWhenPreempted(t *testing.T) {
	b, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Server) Takeoff(")
	end := strings.Index(src[start:], "func (s *Server) Land(")
	body := src[start : start+end]
	if strings.Contains(body, "isPreemptedResult(") {
		t.Fatal("Takeoff must not classify preemption by parsing a result message")
	}
	if !strings.Contains(body, "s.claimSuperseded(claim)") {
		t.Fatal("Takeoff must use typed lease ownership to identify operator preemption")
	}
	claimBatch := strings.Index(body, "s.prepareNormalBatch(")
	precheck := strings.Index(body, "s.cmd.TakeoffPrecheck(")
	if claimBatch < 0 || precheck < 0 || claimBatch > precheck {
		t.Fatal("Takeoff must claim every target before precheck so an older intent cannot revive after a newer takeover")
	}
	if !strings.Contains(body, "s.runClaimed(\"TakeoffRollbackLand\"") {
		t.Fatal("rollback LAND must execute only through the still-current Takeoff claim")
	}
	if strings.Contains(body, "s.cmd.Land(ctx,") {
		t.Fatal("Takeoff must not emit an unguarded rollback LAND on the parent context")
	}
}

// Emergency contention: a takeover's short ownership critical section must not
// block behind a long normal op. Here a "normal op" holds the reservation and a
// goroutine blocks as if inside its FC send; a concurrent preempt (the takeover
// path) must return promptly and unblock it, not wait for it.
func TestTakeoverShortSectionNotBlockedByInFlightNormalOp(t *testing.T) {
	s := newReservationServer()
	normalCtx, normalCancel := context.WithCancel(context.Background())
	normalDone := make(chan struct{})
	lease := s.reserveNormal(t, []uint32{5}, normalCancel)
	go func() { // simulates the unlocked long FC wait of the normal op
		<-normalCtx.Done()
		s.releaseLease(lease)
		close(normalDone)
	}()

	start := time.Now()
	s.missionDispatchMu.Lock()
	_, ok := s.preemptReserveLocked(
		[]uint32{5}, func() {}, takeoverPriorityStopAll) // takeover short section
	s.missionDispatchMu.Unlock()
	if !ok {
		t.Fatal("takeover reservation unexpectedly rejected")
	}
	if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
		t.Fatalf("takeover short section blocked behind normal op for %v", elapsed)
	}
	select {
	case <-normalDone:
	case <-time.After(2 * time.Second):
		t.Fatal("normal op was not preempted/unblocked by the takeover")
	}
}
