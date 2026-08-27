package api

import (
	"context"
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

// F3 exit: the mission RPC boundary must not send any flight command yet.
func TestMissionHandlersIssueNoCommand(t *testing.T) {
	src, err := os.ReadFile("mission.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(src)
	// check actual calls (trailing dot / call parens), not prose in comments
	forbidden := []string{"s.cmd.", "s.swarm.", ".Goto(", ".Hold(", ".Takeoff(", ".Rtl(", ".Idempotent("}
	for _, bad := range forbidden {
		if strings.Contains(text, bad) {
			t.Errorf("mission.go references %q — F3 handlers must not send flight commands", bad)
		}
	}
}
