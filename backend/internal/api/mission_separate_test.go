package api

import (
	"context"
	"math"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
)

// V3-S09-B API-level tests.  The SEPARATE per-drone dispatcher is deferred to the
// authority flip (its per-drone-independent single-flight differs from GROUPED), so
// a SEPARATE authority run is modeled + owned by Core but emits no flight command
// yet.  These tests pin that contract plus the mode-agnostic takeover/failsafe
// terminality.  No live profile token enables the SEPARATE opt-in.

func pbSeparatePlan(planID string, participants ...uint32) *pb.MissionPlan {
	if len(participants) == 0 {
		participants = []uint32{1, 2, 3}
	}
	routes := make([]*pb.MissionRoute, 0, len(participants))
	alts := make(map[uint32]float64, len(participants))
	for _, id := range participants {
		lat := 14.0 + float64(id)*0.001 // >100m parallel separation: preflight-safe
		routes = append(routes, &pb.MissionRoute{DroneId: id, Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: lat, Lon: 100.0000, Alt: 20},
			{Seq: 1, Lat: lat, Lon: 100.0100, Alt: 20},
		}})
		alts[id] = 20
	}
	return &pb.MissionPlan{
		PlanId: planID, Mode: pb.MissionMode_MISSION_MODE_SEPARATE,
		Participants: participants, Routes: routes, ParticipantAltitudes: alts,
	}
}

func newSeparateServer(t *testing.T) (*Server, *apiMissionCommander) {
	t.Helper()
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.mission.EnableSeparateAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	s.missionPosSampleProvider = func() map[uint32]mission.PositionSample {
		return map[uint32]mission.PositionSample{
			1: {Lat: 14.001, Lon: 99.999, Valid: true},
			2: {Lat: 14.002, Lon: 99.999, Valid: true},
			3: {Lat: 14.003, Lon: 99.999, Valid: true},
		}
	}
	return s, cmd
}

func startSeparate(t *testing.T, s *Server) uint64 {
	t.Helper()
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: pbSeparatePlan("sep"), OperationId: "op-sep",
	})
	if err != nil || !resp.Ok || resp.RunId == 0 || !resp.AuthorityActive {
		t.Fatalf("SEPARATE StartMission = %+v err=%v", resp, err)
	}
	return resp.RunId
}

func TestSeparateApiRunOwnedNotYetDispatched(t *testing.T) {
	s, cmd := newSeparateServer(t)
	startSeparate(t, s)
	if cmd.calls != 0 {
		t.Fatalf("SEPARATE dispatcher is deferred; want 0 sends, got %d", cmd.calls)
	}
	s.missionDispatchMu.Lock()
	for _, id := range []uint32{1, 2, 3} {
		if !s.missionOwnsDroneLocked(id) {
			s.missionDispatchMu.Unlock()
			t.Fatalf("Core must own SEPARATE participant D%d", id)
		}
	}
	s.missionDispatchMu.Unlock()
}

func TestSeparateApiOperatorTakeoverTerminal(t *testing.T) {
	for _, target := range []uint32{1, 2, 3} {
		s, _ := newSeparateServer(t)
		startSeparate(t, s)
		s.missionDispatchMu.Lock()
		took := s.cancelMissionForOperatorTargetsLocked([]uint32{target})
		s.missionDispatchMu.Unlock()
		if !took {
			t.Fatalf("takeover on SEPARATE participant D%d must cancel the run", target)
		}
		if st := s.mission.Snapshot(); st.Active || st.State != mission.StateCancelled {
			t.Fatalf("run must be terminal after takeover: %+v", st)
		}
	}
}

func TestSeparateApiNonParticipantDoesNotCancel(t *testing.T) {
	s, _ := newSeparateServer(t)
	startSeparate(t, s)
	s.missionDispatchMu.Lock()
	took := s.cancelMissionForOperatorTargetsLocked([]uint32{99})
	s.missionDispatchMu.Unlock()
	if took || !s.mission.Snapshot().Active {
		t.Fatal("non-participant takeover must not cancel the SEPARATE run")
	}
}

func TestSeparateApiFailsafeInterrupts(t *testing.T) {
	for _, cat := range []string{"battery", "link"} {
		s, _ := newSeparateServer(t)
		startSeparate(t, s)
		s.handleMissionSafetyEvent(&pb.Event{
			Level: pb.EventLevel_EVENT_LEVEL_ALARM, Category: cat, DroneId: 2,
			Message: cat + " critical",
		})
		if s.mission.Snapshot().Active {
			t.Fatalf("%s failsafe must interrupt the SEPARATE run", cat)
		}
	}
}

func TestSeparateApiAuthorityRequiresFreshStartPositions(t *testing.T) {
	s := newMissionServer()
	s.mission.EnableSeparateAuthority()
	s.missionAuthority = true
	s.missionExec = &apiMissionCommander{}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: pbSeparatePlan("sep-no-start", 1, 2), OperationId: "op-no-start",
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok || resp.RunId != 0 {
		t.Fatalf("SEPARATE authority must fail closed without fresh current positions: %+v", resp)
	}
}

func TestSeparateApiAuthorityRejectsInvalidStartCoordinatesBeforeOwnership(t *testing.T) {
	for name, bad := range map[string]mission.PositionSample{
		"invalid evidence": {Lat: 14.002, Lon: 99.999, Valid: false},
		"stale evidence": {
			Lat: 14.002, Lon: 99.999, Valid: true,
			Age: mission.DefaultPositionFreshness + time.Nanosecond,
		},
		"negative age":    {Lat: 14.002, Lon: 99.999, Valid: true, Age: -time.Nanosecond},
		"zero coordinate": {Lat: 0, Lon: 0, Valid: true},
		"NaN latitude":    {Lat: math.NaN(), Lon: 99.999, Valid: true},
		"Inf longitude":   {Lat: 14.002, Lon: math.Inf(1), Valid: true},
		"latitude range":  {Lat: 90.001, Lon: 99.999, Valid: true},
		"longitude range": {Lat: 14.002, Lon: -180.001, Valid: true},
	} {
		t.Run(name, func(t *testing.T) {
			s := newMissionServer()
			cmd := &apiMissionCommander{}
			s.mission.EnableSeparateAuthority()
			s.missionAuthority = true
			s.missionExec = cmd
			s.missionPosSampleProvider = func() map[uint32]mission.PositionSample {
				return map[uint32]mission.PositionSample{
					1: {Lat: 14.001, Lon: 99.999, Valid: true},
					2: bad,
				}
			}
			resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
				Plan: pbSeparatePlan("sep-invalid-start", 1, 2), OperationId: "op-invalid-start",
			})
			if err != nil {
				t.Fatal(err)
			}
			if resp.Ok || resp.RunId != 0 {
				t.Fatalf("invalid SEPARATE start must fail before run creation: %+v", resp)
			}
			if snap := s.mission.Snapshot(); snap.Active {
				t.Fatalf("invalid start created an authoritative run: %+v", snap)
			}
			s.missionDispatchMu.Lock()
			owns := s.missionOwnsDroneLocked(1) || s.missionOwnsDroneLocked(2)
			s.missionDispatchMu.Unlock()
			if owns {
				t.Fatal("invalid start acquired participant ownership")
			}
			if cmd.attempts != 0 || cmd.calls != 0 {
				t.Fatalf("invalid start reached GOTO dispatch: attempts=%d calls=%d", cmd.attempts, cmd.calls)
			}
		})
	}
}

func TestSeparateApiAuthorityRejectsCrossingInitialLegs(t *testing.T) {
	s := newMissionServer()
	s.mission.EnableSeparateAuthority()
	s.missionAuthority = true
	s.missionExec = &apiMissionCommander{}
	s.missionPosSampleProvider = func() map[uint32]mission.PositionSample {
		return map[uint32]mission.PositionSample{
			1: {Lat: 14.0000, Lon: 100.0100, Valid: true},
			2: {Lat: 14.0000, Lon: 100.0000, Valid: true},
		}
	}
	plan := &pb.MissionPlan{
		PlanId: "sep-cross-start", Mode: pb.MissionMode_MISSION_MODE_SEPARATE,
		Participants: []uint32{1, 2}, ParticipantAltitudes: map[uint32]float64{1: 20, 2: 20},
		Routes: []*pb.MissionRoute{
			{DroneId: 1, Points: []*pb.MissionWaypoint{{Seq: 0, Lat: 14.0100, Lon: 100.0000, Alt: 20}}},
			{DroneId: 2, Points: []*pb.MissionWaypoint{{Seq: 0, Lat: 14.0100, Lon: 100.0100, Alt: 20}}},
		},
	}
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{Plan: plan, OperationId: "op-cross"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.Ok || resp.RunId != 0 {
		t.Fatalf("crossing current->WP1 legs must be blocked before Core run creation: %+v", resp)
	}
}
