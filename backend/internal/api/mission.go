package api

import (
	"context"
	"fmt"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
)

// missionObserveHz is the shadow observation rate (F4 Stage A).  The engine only
// judges arrival from telemetry — it issues no command — so this rate only
// affects how promptly the shadow state tracks reality, never flight behavior.
const missionObserveInterval = 200 * time.Millisecond

// ═══════════════════════════════════════════════════════════
//  Run-based Mission RPC boundary (Fast-Track V2, phase F3)
//
//  StartMission / CancelMission / GetMissionState are backed by the SHADOW
//  mission.Engine.  They establish Core-owned run identity + Start/Cancel/Query
//  idempotency, but DO NOT issue flight commands and DO NOT take authority away
//  from the Python executor — that is F4.  None of these handlers touch
//  s.cmd / command.Service, so no command can be sent from here.
// ═══════════════════════════════════════════════════════════

// StartMission validates + freezes a plan and returns a Core-owned run_id.
// Idempotent on operation_id (retry/double-click/reconnect) and plan_id; a
// different plan while a run is active is rejected (contract B4).
func (s *Server) StartMission(ctx context.Context, req *pb.StartMissionRequest) (*pb.StartMissionResponse, error) {
	if req.GetPlan() == nil {
		return &pb.StartMissionResponse{Ok: false, Message: "mission plan required"}, nil
	}
	plan := protoToPlan(req.GetPlan())
	runID, err := s.mission.StartOp(plan, req.GetOperationId())
	if err != nil {
		return &pb.StartMissionResponse{Ok: false, Message: err.Error()}, nil
	}
	snap := s.mission.Snapshot()
	return &pb.StartMissionResponse{
		Ok:      true,
		Message: fmt.Sprintf("mission accepted (run %d)", runID),
		RunId:   runID,
		State:   stateToProto(snap.State),
	}, nil
}

// CancelMission cancels the active run if run_id matches.  A stale/unknown
// run_id is a successful no-op that never touches a newer run (contract B5).
func (s *Server) CancelMission(ctx context.Context, req *pb.CancelMissionRequest) (*pb.CommandResult, error) {
	_ = s.mission.Cancel(req.GetRunId()) // stale-safe + idempotent
	snap := s.mission.Snapshot()
	return &pb.CommandResult{
		Ok:        true,
		Command:   "CancelMission",
		RequestId: req.GetRequestId(),
		Message:   fmt.Sprintf("mission state=%s", snap.State),
	}, nil
}

// GetMissionState returns the authoritative shadow state a reconnecting UI reads
// to rebuild the mission display without re-starting it (contract B6/MUST-7).
func (s *Server) GetMissionState(ctx context.Context, _ *pb.GetMissionStateRequest) (*pb.MissionStateResponse, error) {
	return snapshotToProto(s.mission.Snapshot()), nil
}

// ── shadow observation (F4 Stage A) ────────────────────────
//
// runMissionObserver feeds live fleet telemetry into the SHADOW mission engine
// so its state (current waypoint / WAIT / arrival) tracks reality and can be
// compared against the Python executor.  It is read-only: Observe/Poll issue no
// command.  When no run is active the loop does effectively nothing, so wiring
// it in is inert until a mission is actually started via StartMission.

func (s *Server) runMissionObserver(ctx context.Context) {
	ticker := time.NewTicker(missionObserveInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.observeMissionTick()
		}
	}
}

func (s *Server) observeMissionTick() {
	if s.mission == nil || !s.mission.Active() {
		return // near-zero cost when no mission is running
	}
	if s.mgr != nil {
		s.observeFrom(s.mgr.Snapshot())
	} else {
		s.mission.Poll()
	}
}

// observeFrom feeds a telemetry batch into the shadow engine (arrival) then polls
// WAIT deadlines.  Split out from the fleet manager so it is unit-testable.
func (s *Server) observeFrom(telems []*pb.Telemetry) {
	for _, t := range telems {
		if t == nil {
			continue
		}
		if p := t.GetPosition(); p != nil {
			s.mission.Observe(t.GetDroneId(), p.GetLat(), p.GetLon(), p.GetAltRel())
		}
	}
	s.mission.Poll()
}

// ── proto ↔ domain conversion ──────────────────────────────

func protoToPlan(p *pb.MissionPlan) mission.MissionPlan {
	plan := mission.MissionPlan{
		PlanID:         p.GetPlanId(),
		Mode:           protoToMode(p.GetMode()),
		Participants:   append([]uint32(nil), p.GetParticipants()...),
		LeaderID:       p.GetLeaderId(),
		ArrivalRadiusM: p.GetArrivalRadiusM(),
		RtlAfter:       p.GetRtlAfter(),
	}
	for _, r := range p.GetRoutes() {
		route := mission.Route{DroneID: r.GetDroneId()}
		for _, wp := range r.GetPoints() {
			route.Points = append(route.Points, mission.Waypoint{
				Seq:         int(wp.GetSeq()),
				Lat:         wp.GetLat(),
				Lon:         wp.GetLon(),
				Alt:         wp.GetAlt(),
				WaitSeconds: int(wp.GetWaitSeconds()),
				Action:      protoToAction(wp.GetAction()),
			})
		}
		plan.Routes = append(plan.Routes, route)
	}
	return plan
}

func protoToMode(m pb.MissionMode) mission.Mode {
	switch m {
	case pb.MissionMode_MISSION_MODE_SEPARATE:
		return mission.ModeSeparate
	case pb.MissionMode_MISSION_MODE_SWARM_LEADER:
		return mission.ModeSwarmLeader
	default:
		return mission.ModeGrouped
	}
}

func modeToProto(m mission.Mode) pb.MissionMode {
	switch m {
	case mission.ModeSeparate:
		return pb.MissionMode_MISSION_MODE_SEPARATE
	case mission.ModeSwarmLeader:
		return pb.MissionMode_MISSION_MODE_SWARM_LEADER
	default:
		return pb.MissionMode_MISSION_MODE_GROUPED
	}
}

func protoToAction(a pb.MissionWpAction) mission.Action {
	switch a {
	case pb.MissionWpAction_MISSION_WP_ACTION_SERVO_A:
		return mission.ActionServoA
	case pb.MissionWpAction_MISSION_WP_ACTION_SERVO_B:
		return mission.ActionServoB
	default:
		return mission.ActionNone
	}
}

func stateToProto(s mission.State) pb.MissionRunState {
	switch s {
	case mission.StateValidating:
		return pb.MissionRunState_MISSION_STATE_VALIDATING
	case mission.StateReady:
		return pb.MissionRunState_MISSION_STATE_READY
	case mission.StateRunning:
		return pb.MissionRunState_MISSION_STATE_RUNNING
	case mission.StateWaiting:
		return pb.MissionRunState_MISSION_STATE_WAITING
	case mission.StateCancelling:
		return pb.MissionRunState_MISSION_STATE_CANCELLING
	case mission.StateCancelled:
		return pb.MissionRunState_MISSION_STATE_CANCELLED
	case mission.StateInterrupted:
		return pb.MissionRunState_MISSION_STATE_INTERRUPTED
	case mission.StateFailed:
		return pb.MissionRunState_MISSION_STATE_FAILED
	case mission.StateCompleted:
		return pb.MissionRunState_MISSION_STATE_COMPLETED
	default:
		return pb.MissionRunState_MISSION_STATE_IDLE
	}
}

func snapshotToProto(snap mission.Snapshot) *pb.MissionStateResponse {
	resp := &pb.MissionStateResponse{
		Active:         snap.Active,
		RunId:          snap.RunID,
		PlanId:         snap.PlanID,
		Mode:           modeToProto(snap.Mode),
		State:          stateToProto(snap.State),
		Revision:       snap.Revision,
		Participants:   append([]uint32(nil), snap.Participants...),
		CurrentIndex:   int32(snap.CurrentIndex),
		Arrived:        append([]uint32(nil), snap.Arrived...),
		TerminalReason: snap.TerminalReason,
	}
	if snap.RunID != 0 {
		resp.LastTransition = snap.LastTransition.String()
	}
	if snap.SepIndex != nil {
		resp.SepIndex = make(map[uint32]int32, len(snap.SepIndex))
		for k, v := range snap.SepIndex {
			resp.SepIndex[k] = int32(v)
		}
	}
	for _, w := range snap.Waits {
		resp.Waits = append(resp.Waits, &pb.MissionWait{
			Scope:      w.Scope,
			Index:      int32(w.Index),
			RemainingS: w.RemainingS,
			TotalS:     int32(w.TotalS),
		})
	}
	return resp
}
