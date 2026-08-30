package api

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
)

// newMissionServer builds a minimal Server exercising only the mission engine
// (the mission handlers touch nothing else — proving F3 "no flight command").
func newMissionServer() *Server {
	return &Server{mission: mission.NewEngine(nil)}
}

type apiMissionCommander struct {
	attempts    int // GOTO attempts including rejected sends
	calls       int // successful GOTO sends
	holdCalls   int
	gotoIDs     []uint32 // drone ids of successful GOTO sends, in send order
	gotoErr     error
	holdErr     error
	rtlCalls    int
	rtlIDs      []uint32
	rtlBlock    chan struct{}
	rtlStarted  chan context.Context
	perDroneErr map[uint32]error     // optional per-drone GOTO error (overrides gotoErr)
	lastCtx     context.Context      // last send's ctx (safe: set synchronously in-caller)
	block       chan struct{}        // if set, a send blocks until closed or ctx done
	started     chan context.Context // if set, receives the send's ctx when it begins
}

func (c *apiMissionCommander) run(ctx context.Context) (blockedErr error, blocked bool) {
	c.lastCtx = ctx
	if c.started != nil {
		c.started <- ctx
	}
	if c.block != nil {
		select {
		case <-c.block:
		case <-ctx.Done():
			return ctx.Err(), true
		}
	}
	return nil, false
}

func (c *apiMissionCommander) Goto(ctx context.Context, droneID uint32, lat, lon, alt float64) error {
	if err, blocked := c.run(ctx); blocked {
		return err
	}
	c.attempts++
	if c.perDroneErr != nil {
		if err, ok := c.perDroneErr[droneID]; ok && err != nil {
			return err // rejected before it counts as a committed send
		}
	}
	c.calls++
	c.gotoIDs = append(c.gotoIDs, droneID)
	return c.gotoErr
}

func (c *apiMissionCommander) Hold(ctx context.Context, droneID uint32) error {
	if err, blocked := c.run(ctx); blocked {
		return err
	}
	c.holdCalls++
	return c.holdErr
}

func (c *apiMissionCommander) RTL(ctx context.Context, droneID uint32) error {
	if c.rtlStarted != nil {
		c.rtlStarted <- ctx
	}
	if c.rtlBlock != nil {
		select {
		case <-c.rtlBlock:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	c.rtlCalls++
	c.rtlIDs = append(c.rtlIDs, droneID)
	return nil
}

func pbGroupedPlan(planID string) *pb.MissionPlan {
	return &pb.MissionPlan{
		PlanId:       planID,
		Mode:         pb.MissionMode_MISSION_MODE_GROUPED,
		Participants: []uint32{1, 2},
		Routes: []*pb.MissionRoute{{DroneId: 0, Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: 14.0, Lon: 100.0},
			{Seq: 1, Lat: 14.001, Lon: 100.0},
		}}},
	}
}

func TestStartMissionCreatesRun(t *testing.T) {
	s := newMissionServer()
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: pbGroupedPlan("p1"), OperationId: "op-1",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !resp.Ok || resp.RunId == 0 || resp.State != pb.MissionRunState_MISSION_STATE_RUNNING {
		t.Fatalf("bad start response: %+v", resp)
	}
	if resp.AuthorityActive {
		t.Fatal("shadow/default server must not claim Core authority")
	}
}

func TestStartMissionReportsAuthorityAndRejectsIneligiblePlan(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("authority")
	plan.Participants = []uint32{1}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan, OperationId: "op-auth"})
	if err != nil || !resp.Ok || !resp.AuthorityActive || resp.RunId == 0 {
		t.Fatalf("eligible authority start = %+v err=%v", resp, err)
	}
	if cmd.calls != 1 {
		t.Fatalf("eligible authority start must dispatch one GOTO, got %d", cmd.calls)
	}
	st := missionState(t, s)
	if !st.AuthorityActive {
		t.Fatal("GetMissionState must report active Core authority")
	}

	s2 := newMissionServer()
	cmd2 := &apiMissionCommander{}
	s2.mission.EnableAuthority()
	s2.missionAuthority = true
	s2.missionExec = cmd2
	bad, _ := s2.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("multi"), OperationId: "op-bad"})
	if bad.Ok || bad.RunId != 0 || cmd2.calls != 0 {
		t.Fatalf("ineligible authority plan must be rejected before command: %+v calls=%d", bad, cmd2.calls)
	}
}

func TestAuthorityWaitDispatchesSingleHoldThroughExecutor(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableWaitAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("wait-authority")
	plan.Participants = []uint32{1}
	plan.Routes[0].Points[0].WaitSeconds = 60
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan, OperationId: "op-wait"})
	if err != nil || !resp.Ok || cmd.calls != 1 {
		t.Fatalf("authority WAIT start: resp=%+v err=%v gotos=%d", resp, err, cmd.calls)
	}
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if cmd.holdCalls != 1 {
		t.Fatalf("arrival at WAIT must dispatch exactly one HOLD, got %d", cmd.holdCalls)
	}
	if st := missionState(t, s); st.State != pb.MissionRunState_MISSION_STATE_WAITING {
		t.Fatalf("state after WAIT arrival = %s", st.State)
	}
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if cmd.holdCalls != 1 {
		t.Fatalf("repeated observation must not duplicate HOLD, got %d", cmd.holdCalls)
	}
}

func TestAuthorityWaitHoldRejectFailsRun(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{holdErr: fmt.Errorf("hold rejected")}
	s.mission.EnableWaitAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("wait-reject")
	plan.Participants = []uint32{1}
	plan.Routes[0].Points[0].WaitSeconds = 60
	_, _ = s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan, OperationId: "op-wait-reject"})
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	st := missionState(t, s)
	if st.State != pb.MissionRunState_MISSION_STATE_FAILED || st.Active {
		t.Fatalf("rejected HOLD must fail closed: %+v", st)
	}
}

func TestManualGotoAndRcMoveRejectWhileCoreOwnsDrone(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("manual-overlap")
	plan.Participants = []uint32{1}
	start, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-manual-overlap",
	})
	if err != nil || !start.Ok || !start.AuthorityActive {
		t.Fatalf("authority start failed: resp=%+v err=%v", start, err)
	}
	if cmd.calls != 1 {
		t.Fatalf("initial authority GOTO calls=%d want 1", cmd.calls)
	}

	gotoRes, err := s.Goto(context.Background(), &pb.GotoRequest{
		DroneId: 1, Lat: 14.5, Lon: 100.5, Alt: 20, RequestId: "manual-goto",
	})
	if err != nil || gotoRes.Ok || !strings.Contains(gotoRes.Message, "Core mission owns navigation") {
		t.Fatalf("manual GOTO must be rejected during Core authority: res=%+v err=%v", gotoRes, err)
	}
	if cmd.calls != 1 {
		t.Fatalf("rejected manual GOTO must not reach mission executor, calls=%d", cmd.calls)
	}

	rcRes, err := s.RcMove(context.Background(), &pb.RcMoveRequest{
		Target: &pb.Target{DroneIds: []uint32{1}}, Speed: 1,
	})
	if err != nil || rcRes.Ok || !strings.Contains(rcRes.Message, "Core mission owns navigation") {
		t.Fatalf("manual RcMove must be rejected during Core authority: res=%+v err=%v", rcRes, err)
	}

	modeRes, err := s.SetMode(context.Background(), &pb.SetModeRequest{
		Target: &pb.Target{DroneIds: []uint32{1}}, RequestId: "manual-mode",
	})
	if err != nil || modeRes.Ok || !strings.Contains(modeRes.Message, "Core mission owns navigation") {
		t.Fatalf("manual SetMode must be rejected during Core authority: res=%+v err=%v", modeRes, err)
	}
	if st := missionState(t, s); !st.Active || !st.AuthorityActive || st.RunId != start.RunId {
		t.Fatalf("manual rejection must leave the Core run authoritative: %+v", st)
	}
}

func TestArmTakeoffAndSwarmStartRejectWhileCoreOwnsNavigation(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("manual-flight-overlap")
	plan.Participants = []uint32{1}
	start, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-manual-flight-overlap",
	})
	if err != nil || !start.Ok || !start.AuthorityActive {
		t.Fatalf("authority start failed: resp=%+v err=%v", start, err)
	}
	target := &pb.Target{DroneIds: []uint32{1}}

	arm, err := s.Arm(context.Background(), &pb.ArmRequest{
		Target: target, RequestId: "manual-arm",
	})
	if err != nil || arm.Ok || !strings.Contains(arm.Message, "Core mission owns navigation") {
		t.Fatalf("ARM must be rejected during Core authority: res=%+v err=%v", arm, err)
	}

	takeoff, err := s.Takeoff(context.Background(), &pb.TakeoffRequest{
		Target: target, Altitude: 20, Confirmed: true, RequestId: "manual-takeoff",
	})
	if err != nil || takeoff.Ok || !strings.Contains(takeoff.Message, "Core mission owns navigation") {
		t.Fatalf("TAKEOFF must be rejected during Core authority: res=%+v err=%v", takeoff, err)
	}

	swarmStart, err := s.SwarmControl(context.Background(), &pb.SwarmControlRequest{
		Action: pb.SwarmControlRequest_START,
	})
	if err != nil || swarmStart.Ok || !strings.Contains(swarmStart.Message, "Core mission owns navigation") {
		t.Fatalf("Swarm START must be rejected during Core authority: res=%+v err=%v", swarmStart, err)
	}
	if st := missionState(t, s); !st.Active || !st.AuthorityActive || st.RunId != start.RunId {
		t.Fatalf("rejected manual commands must leave Core mission authoritative: %+v", st)
	}
}

func TestRejectedManualGotoDoesNotCorruptAuthorityProgression(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("manual-claim-index")
	plan.Participants = []uint32{1}
	start, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-manual-claim-index",
	})
	if err != nil || !start.Ok || cmd.calls != 1 {
		t.Fatalf("authority start failed: resp=%+v err=%v calls=%d", start, err, cmd.calls)
	}

	res, _ := s.Goto(context.Background(), &pb.GotoRequest{
		DroneId: 1, Lat: 14.9, Lon: 100.9, Alt: 25, RequestId: "manual-race",
	})
	if res.Ok {
		t.Fatal("manual GOTO must not take authority from the active mission")
	}
	// Arrival at WP0 must still advance exactly once to WP1. The rejected manual
	// command must not alter authorityClaimedIndex or resurrect a second owner.
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if cmd.calls != 2 {
		t.Fatalf("mission should dispatch exactly one next GOTO after arrival, calls=%d", cmd.calls)
	}
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if cmd.calls != 2 {
		t.Fatalf("repeated observation duplicated authority GOTO, calls=%d", cmd.calls)
	}
}

func TestOperatorTakeoverCancelsOwnedCoreRunBeforeHoldStop(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	plan := pbGroupedPlan("operator-takeover")
	plan.Participants = []uint32{1}
	start, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-operator-takeover",
	})
	if err != nil || !start.Ok {
		t.Fatalf("authority start failed: resp=%+v err=%v", start, err)
	}
	s.missionDispatchMu.Lock()
	cancelled := s.cancelMissionForOperatorTargetsLocked([]uint32{1})
	s.missionDispatchMu.Unlock()
	if !cancelled {
		t.Fatal("operator takeover should cancel an owned Core mission")
	}
	if st := missionState(t, s); st.Active || st.State != pb.MissionRunState_MISSION_STATE_CANCELLED {
		t.Fatalf("operator takeover must make mission terminal before stop/hold: %+v", st)
	}
}

func TestCoreMissionSwarmMutualExclusionIsWired(t *testing.T) {
	missionSrc, err := os.ReadFile("mission.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(missionSrc), "s.swarmNavigationBusy()") {
		t.Fatal("StartMission must reject while swarm/return navigation is busy")
	}
	serverSrc, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(serverSrc)
	for _, required := range []string{
		"s.swarm.NavigationBusy()",
		"s.missionAuthorityActiveLocked()",
		"s.cancelMissionForOperatorTargetsLocked(req.DroneIds)",
	} {
		if !strings.Contains(text, required) {
			t.Fatalf("server ownership boundary missing %q", required)
		}
	}
}

func TestProtoToPlanPreservesParticipantAltitudes(t *testing.T) {
	p := pbGroupedPlan("alts")
	p.ParticipantAltitudes = map[uint32]float64{1: 18.5, 2: 27.0}
	plan := protoToPlan(p)
	if got := plan.AltitudeFor(1, 20); got != 18.5 {
		t.Fatalf("D1 altitude = %v, want 18.5", got)
	}
	if got := plan.AltitudeFor(2, 20); got != 27.0 {
		t.Fatalf("D2 altitude = %v, want 27", got)
	}
}

func TestStartMissionInvalidPlanRejected(t *testing.T) {
	s := newMissionServer()
	resp, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: &pb.MissionPlan{}})
	if resp.Ok {
		t.Fatal("invalid plan should not be accepted")
	}
	if resp.RunId != 0 {
		t.Fatal("no run id for rejected start")
	}
}

func TestStartMissionNilPlan(t *testing.T) {
	s := newMissionServer()
	resp, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{})
	if resp.Ok {
		t.Fatal("nil plan should be rejected")
	}
}

// contract B4: duplicate Start (same operation_id) must not create a second run.
func TestDuplicateStartSameOperationReturnsSameRun(t *testing.T) {
	s := newMissionServer()
	r1, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op-1"})
	r2, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op-1"})
	if !r1.Ok || !r2.Ok || r1.RunId != r2.RunId {
		t.Fatalf("duplicate start should return same run: r1=%d r2=%d", r1.RunId, r2.RunId)
	}
}

// contract B4: a different plan while one is active is rejected (no two missions).
func TestStartSecondDifferentPlanRejected(t *testing.T) {
	s := newMissionServer()
	s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op-1"})
	resp, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p2"), OperationId: "op-2"})
	if resp.Ok {
		t.Fatal("second distinct plan while active must be rejected")
	}
}

// contract B5: stale/unknown run_id cancel is a no-op that never touches the run.
func TestCancelMissionStaleSafeAndIdempotent(t *testing.T) {
	s := newMissionServer()
	start, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op-1"})

	s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: start.RunId + 999})
	if st, _ := s.GetMissionState(context.Background(), &pb.GetMissionStateRequest{}); st.State != pb.MissionRunState_MISSION_STATE_RUNNING {
		t.Fatalf("stale cancel must not affect active run, state=%s", st.State)
	}

	s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: start.RunId})
	if st, _ := s.GetMissionState(context.Background(), &pb.GetMissionStateRequest{}); st.State != pb.MissionRunState_MISSION_STATE_CANCELLED {
		t.Fatalf("cancel should move to CANCELLED, state=%s", st.State)
	}
	// idempotent second cancel
	res, err := s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: start.RunId})
	if err != nil || !res.Ok {
		t.Fatalf("second cancel should be a safe no-op: %v %+v", err, res)
	}
}

// contract B6/MUST-7: a reconnecting UI reads authoritative state via query.
func TestGetMissionStateReconnect(t *testing.T) {
	s := newMissionServer()
	start, _ := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op-1"})

	st, err := s.GetMissionState(context.Background(), &pb.GetMissionStateRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if !st.Active || st.RunId != start.RunId || st.PlanId != "p1" {
		t.Fatalf("query should return active run identity: %+v", st)
	}
	if st.State != pb.MissionRunState_MISSION_STATE_RUNNING {
		t.Fatalf("state = %s, want RUNNING", st.State)
	}
	if len(st.Participants) != 2 {
		t.Fatalf("participants = %v, want 2", st.Participants)
	}
	if st.Plan == nil || st.Plan.PlanId != "p1" || len(st.Plan.Routes) != 1 || len(st.Plan.Routes[0].Points) != 2 {
		t.Fatalf("reconnect query must include frozen plan for UI rebuild: %+v", st.Plan)
	}
}

func TestGetMissionStateNoRun(t *testing.T) {
	s := newMissionServer()
	st, _ := s.GetMissionState(context.Background(), &pb.GetMissionStateRequest{})
	if st.Active || st.State != pb.MissionRunState_MISSION_STATE_IDLE || st.RunId != 0 {
		t.Fatalf("no run → inactive IDLE: %+v", st)
	}
}

// ── F4 Stage A: telemetry → shadow observation (still no command) ──

func telem(id uint32, lat, lon float64) *pb.Telemetry {
	return &pb.Telemetry{DroneId: id, Position: &pb.GeoPoint{Lat: lat, Lon: lon}}
}

func missionState(t *testing.T, s *Server) *pb.MissionStateResponse {
	t.Helper()
	st, err := s.GetMissionState(context.Background(), &pb.GetMissionStateRequest{})
	if err != nil {
		t.Fatal(err)
	}
	return st
}

// The shadow engine progresses from fed telemetry (GROUPED: advance only when
// all participants arrive) — this is the F4 Stage A observation pipeline.
func TestObserveFromDrivesShadowProgression(t *testing.T) {
	s := newMissionServer()
	s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op"})

	// far from WP0 → no advance
	s.observeFrom([]*pb.Telemetry{telem(1, 0, 0), telem(2, 0, 0)})
	if st := missionState(t, s); st.CurrentIndex != 0 {
		t.Fatalf("should not advance while far: index=%d", st.CurrentIndex)
	}
	// only one at WP0 → still waiting for the other (GROUPED)
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0), telem(2, 0, 0)})
	if st := missionState(t, s); st.CurrentIndex != 0 {
		t.Fatalf("GROUPED must wait for all: index=%d", st.CurrentIndex)
	}
	// both at WP0 → advance to WP1
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0), telem(2, 14.0, 100.0)})
	if st := missionState(t, s); st.CurrentIndex != 1 {
		t.Fatalf("should advance to WP1: index=%d", st.CurrentIndex)
	}
	// both at WP1 → COMPLETED
	s.observeFrom([]*pb.Telemetry{telem(1, 14.001, 100.0), telem(2, 14.001, 100.0)})
	if st := missionState(t, s); st.State != pb.MissionRunState_MISSION_STATE_COMPLETED {
		t.Fatalf("should be COMPLETED: state=%s", st.State)
	}
}

// The observation tick is inert (and nil-safe) when no mission is active.
func TestObserveTickInertWhenIdle(t *testing.T) {
	s := newMissionServer() // no fleet manager, no run
	s.observeMissionTick()  // must not panic
	if missionState(t, s).Active {
		t.Fatal("no mission should be active")
	}
}

// A telemetry position with no GeoPoint is skipped without panic.
func TestObserveFromNilPositionSafe(t *testing.T) {
	s := newMissionServer()
	s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: pbGroupedPlan("p1"), OperationId: "op"})
	s.observeFrom([]*pb.Telemetry{{DroneId: 1}, nil}) // nil position + nil entry
	if missionState(t, s).CurrentIndex != 0 {
		t.Fatal("nil position must not advance")
	}
}

// F4 B1: Core-owned battery/link ALARMs must terminate mission progression
// without issuing another flight command.  The fleet manager remains the owner
// of the actual failsafe RTL; this API observer only latches INTERRUPTED.
func TestMissionSafetyAlarmInterruptsParticipant(t *testing.T) {
	for _, category := range []string{"battery", "link"} {
		t.Run(category, func(t *testing.T) {
			s := newMissionServer()
			s.StartMission(context.Background(), &pb.StartMissionRequest{
				Plan: pbGroupedPlan("p1"), OperationId: "op-1",
			})
			msg := category + " failsafe test"
			s.handleMissionSafetyEvent(&pb.Event{
				Level: pb.EventLevel_EVENT_LEVEL_ALARM, DroneId: 1,
				Category: category, Message: msg,
			})

			st := missionState(t, s)
			if st.State != pb.MissionRunState_MISSION_STATE_INTERRUPTED {
				t.Fatalf("%s ALARM should interrupt active participant: state=%s", category, st.State)
			}
			if st.TerminalReason != msg {
				t.Fatalf("terminal reason = %q, want %q", st.TerminalReason, msg)
			}
		})
	}
}

func TestMissionSafetyEventFiltersNonAuthoritySignals(t *testing.T) {
	cases := []struct {
		name string
		ev   *pb.Event
	}{
		{"nil", nil},
		{"warn battery", &pb.Event{Level: pb.EventLevel_EVENT_LEVEL_WARN, DroneId: 1, Category: "battery", Message: "low-ish"}},
		{"alarm other category", &pb.Event{Level: pb.EventLevel_EVENT_LEVEL_ALARM, DroneId: 1, Category: "servo", Message: "servo alarm"}},
		{"alarm nonparticipant", &pb.Event{Level: pb.EventLevel_EVENT_LEVEL_ALARM, DroneId: 99, Category: "link", Message: "other drone link"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := newMissionServer()
			s.StartMission(context.Background(), &pb.StartMissionRequest{
				Plan: pbGroupedPlan("p1"), OperationId: "op-1",
			})
			s.handleMissionSafetyEvent(tc.ev)
			if st := missionState(t, s); st.State != pb.MissionRunState_MISSION_STATE_RUNNING {
				t.Fatalf("non-authority signal must not interrupt mission: state=%s", st.State)
			}
		})
	}
}

// F4/F5 exit: mission handlers may execute only the narrow claimed GOTO/HOLD
// intents. They must never bypass that adapter to command.Service/swarm or add
// unrelated flight commands to the mission boundary.
func TestMissionAuthorityUsesOnlyNarrowExecutor(t *testing.T) {
	src, err := os.ReadFile("mission.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(src)
	forbidden := []string{"s.cmd.", "s.swarm.Start(", "s.swarm.ReturnAndLand(",
		".Takeoff(", ".Rtl(", ".Idempotent(", ".Servo(", ".Land("}
	for _, bad := range forbidden {
		if strings.Contains(text, bad) {
			t.Errorf("mission.go references %q — mission authority must use only the narrow executor", bad)
		}
	}
	for _, required := range []string{"s.mission.ClaimAuthorityGoto()", "s.missionExec.Goto(", "s.mission.ClaimAuthorityHold()", "s.missionExec.Hold("} {
		if !strings.Contains(text, required) {
			t.Errorf("mission.go missing guarded authority path %q", required)
		}
	}
}
