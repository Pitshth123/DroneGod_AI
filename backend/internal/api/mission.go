package api

import (
	"context"
	"fmt"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/safety"
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
	// S09-B future-proofing: even though no live SEPARATE authority token exists,
	// an in-process/harness opt-in must pass the same collision preflight before a
	// Core-owned run can be created. This prevents a later token-only change from
	// accidentally bypassing the Legacy Python preflight.
	if s.mission != nil && s.mission.AuthoritySeparateEnabled() && plan.Mode == mission.ModeSeparate {
		if err := s.validateSeparateAuthorityPreflight(plan); err != nil {
			return &pb.StartMissionResponse{Ok: false, Message: err.Error()}, nil
		}
	}
	var allocatedRunID uint64
	if s.missionStore != nil {
		if operationID := req.GetOperationId(); operationID != "" {
			previousRunID, found, err := s.missionStore.LookupMissionOperation(operationID)
			if err != nil {
				return &pb.StartMissionResponse{Ok: false,
					Message: "mission persistence lookup failed: " + err.Error()}, nil
			}
			if found {
				snap := s.snapshotWithPersistenceStatus()
				if snap.RunID == previousRunID && snap.Active && !snap.RecoveryRequired {
					return &pb.StartMissionResponse{
						Ok: true, Message: fmt.Sprintf("mission accepted (run %d)", previousRunID),
						RunId: previousRunID, State: stateToProto(snap.State),
						AuthorityActive: snap.Authority,
					}, nil
				}
				return &pb.StartMissionResponse{
					Ok: false, RunId: previousRunID, State: stateToProto(snap.State),
					Message: fmt.Sprintf(
						"operation_id belongs to previous run %d; recovery/new operator intent required",
						previousRunID),
				}, nil
			}
		}
		var err error
		allocatedRunID, err = s.missionStore.AllocateMissionRunID()
		if err != nil {
			return &pb.StartMissionResponse{Ok: false,
				Message: "mission run_id allocation failed: " + err.Error()}, nil
		}
	}
	// Serialize the authority handoff with every manual-navigation RPC. This
	// makes Start+initial dispatch one ownership boundary: a manual command is
	// either completely before the Core run or rejected after the run exists.
	s.missionDispatchMu.Lock()
	swarmLeaderAuthority := s.mission != nil && s.mission.AuthoritySwarmLeaderEnabled() &&
		plan.Mode == mission.ModeSwarmLeader
	if swarmLeaderAuthority && !s.swarmLeaderFormationCompatible(plan) {
		s.missionDispatchMu.Unlock()
		return &pb.StartMissionResponse{
			Ok: false, Message: "SWARM_LEADER requires an already-active formation with the same non-zero leader",
		}, nil
	}
	if s.swarmNavigationBusy() && !swarmLeaderAuthority {
		s.missionDispatchMu.Unlock()
		return &pb.StartMissionResponse{
			Ok: false, Message: "swarm/return navigation is active — stop it before Core mission",
		}, nil
	}
	// V3-S07: a manual command send now runs with this lock released while holding
	// a reservation. StartMission must fail closed if any participant is reserved,
	// so a Core mission can never overlap an in-flight manual navigation command.
	if s.anyReservedLocked(plan.Participants) {
		s.missionDispatchMu.Unlock()
		return &pb.StartMissionResponse{
			Ok: false, Message: "a manual command is in progress on a participant — retry after it completes",
		}, nil
	}
	// Atomically bind the already-ready formation leader before the Engine run
	// can acquire authority. This freezes swarm failover and proves form-up can no
	// longer emit its leader-pinning GOTO.
	claimedSwarmLeader := false
	if swarmLeaderAuthority && !s.mission.Snapshot().Active {
		coordinator := s.missionSwarmCoordinator()
		if coordinator == nil || !coordinator.ClaimMissionMembership(plan.LeaderID, plan.Participants) {
			s.missionDispatchMu.Unlock()
			return &pb.StartMissionResponse{Ok: false,
				Message: "SWARM_LEADER formation changed before leader ownership could be claimed"}, nil
		}
		claimedSwarmLeader = true
	}
	// After Cancel/Takeover the old run is terminal and its single mission-send
	// slot is freed immediately; generation checks isolate old transport unwind.
	var runID uint64
	var err error
	if s.missionStore != nil {
		runID, err = s.mission.StartOpWithRunID(plan, req.GetOperationId(), allocatedRunID)
	} else {
		runID, err = s.mission.StartOp(plan, req.GetOperationId())
	}
	if err != nil && claimedSwarmLeader {
		s.missionSwarmCoordinator().ReleaseMissionLeader(plan.LeaderID)
	}
	s.missionDispatchMu.Unlock()
	if err != nil {
		return &pb.StartMissionResponse{Ok: false, Message: err.Error()}, nil
	}
	if err := s.persistMissionState(true); err != nil && claimedSwarmLeader && s.missionSwarmCoordinator() != nil {
		s.missionSwarmCoordinator().ReleaseMissionLeader(plan.LeaderID)
	}
	// The run now owns its participants only after its accepted revision is
	// durable. The dispatcher repeats this durability check before every send.
	s.dispatchMissionAuthority()
	snap := s.snapshotWithPersistenceStatus()
	ok := snap.Active && !snap.RecoveryRequired
	message := fmt.Sprintf("mission accepted (run %d)", runID)
	if !ok {
		message = snap.TerminalReason
	}
	return &pb.StartMissionResponse{
		Ok:              ok,
		Message:         message,
		RunId:           runID,
		State:           stateToProto(snap.State),
		AuthorityActive: snap.Authority,
	}, nil
}

func (s *Server) validateSeparateAuthorityPreflight(plan mission.MissionPlan) error {
	samples := s.missionPositionSamples()
	starts := make(map[uint32][2]float64, len(plan.Participants))
	for _, id := range plan.Participants {
		sample, ok := samples[id]
		if !ok || !sample.Valid || sample.Age < 0 || sample.Age > mission.DefaultPositionFreshness ||
			!mission.ValidNavigationPosition(sample.Lat, sample.Lon) {
			return fmt.Errorf("SEPARATE Core authority requires fresh valid start position for Drone %d", id)
		}
		starts[id] = [2]float64{sample.Lat, sample.Lon}
	}
	return plan.ValidateSeparateRouteConflicts(starts)
}

// CancelMission cancels the active run if run_id matches.  A stale/unknown
// run_id is a successful no-op that never touches a newer run (contract B5).
func (s *Server) CancelMission(ctx context.Context, req *pb.CancelMissionRequest) (*pb.CommandResult, error) {
	// Serialize cancel against the tiny claim+send window so a matching run can
	// never emit after cancellation. A stale run_id must not cancel a newer run's
	// send guard (S10 stale-session invariant).
	s.missionDispatchMu.Lock()
	before := s.mission.Snapshot()
	matches := req.GetRunId() != 0 && before.RunID == req.GetRunId()
	clearedRecovery, incompatible := s.mission.ClearRecovery(req.GetRunId())
	if !clearedRecovery {
		_ = s.mission.Cancel(req.GetRunId())
	}
	if matches {
		s.cancelInflightMissionSendLocked()
		s.invalidateReturnLocked(before.RunID, "operator cancel")
	}
	stopFormation := matches && before.Active && before.Mode == mission.ModeSwarmLeader
	if stopFormation && s.swarm != nil {
		s.swarm.RevokeFormationNavigation()
	}
	s.missionDispatchMu.Unlock()

	var persistErr error
	if clearedRecovery && incompatible && s.missionStore != nil {
		persistErr = s.missionStore.DeleteMissionRecord(req.GetRunId())
		if persistErr == nil {
			s.missionPersistMu.Lock()
			s.missionPersistedRunID = 0
			s.missionPersistedRevision = 0
			s.missionPersistenceFault = ""
			s.missionPersistMu.Unlock()
		} else {
			s.mission.SetIncompatibleRecovery(
				req.GetRunId(), before.PersistenceSchema,
				"recovery clear persistence failed: "+persistErr.Error())
		}
	} else {
		persistErr = s.persistMissionState(false)
	}
	snap := s.snapshotWithPersistenceStatus()
	ok := persistErr == nil
	message := fmt.Sprintf("mission state=%s", snap.State)
	if persistErr != nil {
		message = "mission recovery/cancel persistence failed: " + persistErr.Error()
	}
	return &pb.CommandResult{
		Ok: ok, Command: "CancelMission", RequestId: req.GetRequestId(), Message: message,
	}, nil
}

// GetMissionState returns the authoritative shadow state a reconnecting UI reads
// to rebuild the mission display without re-starting it (contract B6/MUST-7).
func (s *Server) GetMissionState(ctx context.Context, _ *pb.GetMissionStateRequest) (*pb.MissionStateResponse, error) {
	return snapshotToProto(s.snapshotWithPersistenceStatus()), nil
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

// runMissionSafetyObserver mirrors the Core-owned fleet failsafe state into the
// mission state machine.  The fleet manager remains the sole owner of failsafe
// flight action (RTL); this observer only marks the active mission INTERRUPTED
// so no later mission progression can acquire authority after a battery/link
// failsafe.  It has an explicit ctx lifetime and unsubscribes on exit.
func (s *Server) runMissionSafetyObserver(ctx context.Context) {
	if s.events == nil || s.mission == nil {
		return
	}
	id, ch := s.events.Subscribe()
	defer s.events.Unsubscribe(id)
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-ch:
			if !ok {
				return
			}
			s.handleMissionSafetyEvent(ev)
		}
	}
}

// handleMissionSafetyEvent is deliberately command-free.  Only Core ALARM
// events for the two failsafe categories that already own RTL may interrupt a
// run; WARN/recovery/other categories are presentation/audit signals only.
func (s *Server) handleMissionSafetyEvent(ev *pb.Event) {
	if ev == nil || s.mission == nil || ev.GetLevel() != pb.EventLevel_EVENT_LEVEL_ALARM {
		return
	}
	switch ev.GetCategory() {
	case "battery", "link":
		// Serialize state preemption with the mission claim+send window.  The fleet
		// failsafe latch itself is also checked inside command.Service.Goto.  Only
		// cancel in-flight mission sends when the failsafe actually interrupted the
		// run: a non-participant battery/link ALARM must not tear down an unrelated
		// participant's in-flight mission GOTO/HOLD.
		s.missionDispatchMu.Lock()
		before := s.mission.Snapshot()
		interrupted := s.mission.Interrupt(ev.GetDroneId(), ev.GetCategory(), ev.GetMessage())
		if interrupted {
			s.cancelInflightMissionSendLocked() // stop any in-flight mission send on failsafe
			if before.ReturnPolicy == mission.ReturnSwarm &&
				(before.ReturnState == mission.ReturnStatePending || before.ReturnState == mission.ReturnStateReturning) {
				if coordinator := s.missionSwarmReturnCoordinator(); coordinator != nil {
					coordinator.RevokeReturnNavigation()
				}
			}
			s.invalidateReturnLocked(before.RunID, "automatic Return invalidated by fleet/FC failsafe")
		}
		stopFormation := interrupted && before.Mode == mission.ModeSwarmLeader
		if stopFormation && s.swarm != nil {
			s.swarm.RevokeFormationNavigation()
		}
		s.missionDispatchMu.Unlock()
		if interrupted {
			_ = s.persistMissionState(false)
		}
	}
}

// missionSend is one claimed mission GOTO/HOLD to execute with the lock released.
type missionSend struct {
	hold          bool
	runID         uint64
	index         int
	droneID       uint32
	lat, lon, alt float64
	ctx           context.Context
	cancel        context.CancelFunc
	gen           uint64
}

// dispatchMissionAuthority is the only F4/F5 adapter from mission state to flight
// commands. The intent is CLAIMED atomically under missionDispatchMu (exactly
// once per waypoint/WAIT), but the actual GOTO/HOLD is executed with the lock
// RELEASED so an operator emergency/takeover is never serialized behind the
// mission's FC ACK. An in-flight mission send is cancellable via
// s.missionSendCancel; command.Service refuses to write a preempted send to the
// FC, and an operator takeover has already made the run terminal, so no dual
// authority can result.
func (s *Server) dispatchMissionAuthority() {
	if !s.missionAuthority || s.mission == nil || s.missionExec == nil {
		return
	}
	if s.mission.ReturnDispatchPending() {
		s.dispatchReturnAuthority()
		return
	}
	if !s.missionDurableForDispatch() {
		return
	}
	// S09-A (OFF by default): a multi-drone GROUPED run uses the per-drone group
	// dispatcher instead of the single-flight single-send path.  No live profile
	// token enables the opt-in, so production always takes the single path below.
	if s.mission.AuthorityGroupedMultiEnabled() {
		s.dispatchGroupedMultiAuthority()
		return
	}
	s.missionDispatchMu.Lock()
	send, ok := s.claimMissionSendLocked()
	s.missionDispatchMu.Unlock()
	if !ok {
		return
	}
	defer send.cancel()

	var err error
	if send.hold {
		err = s.missionExec.Hold(send.ctx, send.droneID)
	} else {
		err = s.missionExec.Goto(send.ctx, send.droneID, send.lat, send.lon, send.alt)
	}

	s.missionDispatchMu.Lock()
	if s.missionSendGen == send.gen {
		s.missionSendCancel = nil
		s.missionSendGuard = nil
		s.missionSendActive = false
	}
	preempted := send.ctx.Err() != nil
	s.missionDispatchMu.Unlock()

	// A genuine rejection/failure normally fails the guarded single-drone Core run.
	// SWARM_LEADER is different because Legacy `_goto_one` is best-effort: a rejected
	// leader GOTO is logged and the waypoint barrier simply stalls (no retry/rollback,
	// no terminal mission failure). Preserve that behavior while keeping the failure
	// structured and operator-visible. A preemption already made the run terminal.
	if err != nil && !preempted {
		if s.mission.AuthoritySwarmLeaderEnabled() {
			if s.mission.NoteParticipantRejected(send.runID, send.index, send.droneID, err.Error()) &&
				s.events != nil {
				s.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, send.droneID, "mission-participant-rejection",
					fmt.Sprintf("run_id=%d waypoint=%d drone_id=%d reason=%s",
						send.runID, send.index, send.droneID, err.Error()))
			}
		} else {
			s.mission.Fail(send.runID, err.Error())
		}
	}
	_ = s.persistMissionState(true)
	s.dispatchReturnAuthority()
}

// claimMissionSendLocked claims the next mission HOLD (priority) or GOTO exactly
// once and arms a cancellable context for its send. Caller holds missionDispatchMu.
func (s *Server) claimMissionSendLocked() (*missionSend, bool) {
	if !s.missionAuthority || s.mission == nil || s.missionExec == nil {
		return nil, false
	}
	// Single-flight: never claim a second mission intent while the previous
	// GOTO/HOLD is still sending/unwinding.  This keeps one stable cancel/guard
	// handle; a later observer tick will claim the next intent after completion.
	if s.missionSendActive {
		return nil, false
	}
	var send *missionSend
	// WAIT has priority over progression: issue exactly one HOLD before the
	// deadline can later release the next GOTO.
	hold, holdOK, err := s.mission.ClaimAuthorityHold()
	if err != nil {
		if snap := s.mission.Snapshot(); snap.Active {
			s.mission.Fail(snap.RunID, err.Error())
		}
		return nil, false
	}
	if holdOK {
		send = &missionSend{hold: true, runID: hold.RunID, droneID: hold.DroneID}
	} else {
		var intent mission.GotoIntent
		var ok bool
		if s.mission.AuthoritySwarmLeaderEnabled() {
			intent, ok, err = s.mission.ClaimAuthoritySwarmLeaderGoto()
		} else {
			intent, ok, err = s.mission.ClaimAuthorityGoto()
		}
		if err != nil {
			if snap := s.mission.Snapshot(); snap.Active {
				s.mission.Fail(snap.RunID, err.Error())
			}
			return nil, false
		}
		if !ok {
			return nil, false
		}
		send = &missionSend{runID: intent.RunID, index: intent.Index, droneID: intent.DroneID,
			lat: intent.Lat, lon: intent.Lon, alt: intent.Alt}
	}
	base := s.ctx
	if base == nil {
		base = context.Background()
	}
	ctx, cancel := context.WithCancel(base)
	guard := &missionSendGuard{}
	ctx = fleet.WithSendGuard(ctx, guard)
	s.missionSendGen++
	send.gen = s.missionSendGen
	send.ctx = ctx
	send.cancel = cancel
	s.missionSendCancel = cancel
	s.missionSendGuard = guard
	s.missionSendActive = true
	return send, true
}

// cancelInflightMissionSendLocked cancels the in-flight mission GOTO/HOLD send (if
// any) so an operator takeover / CancelMission / failsafe interrupt stops it
// before it can reach the FC. Caller holds missionDispatchMu.
func (s *Server) cancelInflightMissionSendLocked() {
	// Cancel the final-write guard first.  DoSend() holds the same tiny mutex only
	// around conn.Send(), so after this returns no stale mission write can begin.
	if s.missionSendGuard != nil {
		s.missionSendGuard.Cancel()
	}
	if s.missionSendCancel != nil {
		s.missionSendCancel()
		s.missionSendCancel = nil
	}
	s.missionSendGuard = nil
	// Free the single-flight slot immediately: the cancelled send is now harmless
	// (its guard is tripped and its context cancelled, so command.Service refuses
	// any FC write), so a new mission may claim + dispatch without waiting for the
	// old call to finish unwinding.  Bumping the generation makes the old send's
	// eventual return a no-op (its gen no longer matches), so it cannot clear a
	// newer send's slot.
	s.missionSendActive = false
	s.missionSendGen++
	// Also cancel any pending/in-flight multi-drone GROUPED per-drone sends (OFF
	// unless the S09-A opt-in is enabled), so every takeover/cancel/failsafe path
	// that already calls this helper stops the group sends too.
	s.cancelGroupSendsLocked()
}

// releaseCompletedSwarmLeaderBinding releases only the temporary split-ownership
// binding after a SWARM_LEADER route completes naturally. Legacy waypoint finish
// does not stop formation; followers may remain formed, but swarm failover/leader
// selection must no longer be frozen by a mission that has already ended.
//
// The current mission snapshot is re-read while missionDispatchMu is held so a
// stale completion callback can never release a binding belonging to a newer run.
func (s *Server) releaseCompletedSwarmLeaderBinding() {
	if s == nil || s.missionSwarmCoordinator() == nil || s.mission == nil || !s.mission.AuthoritySwarmLeaderEnabled() {
		return
	}
	s.missionDispatchMu.Lock()
	defer s.missionDispatchMu.Unlock()
	snap := s.mission.Snapshot()
	if snap.Active || snap.Mode != mission.ModeSwarmLeader || snap.CurrentLeaderID == 0 ||
		snap.State != mission.StateCompleted {
		return
	}
	s.missionSwarmCoordinator().ReleaseMissionLeader(snap.CurrentLeaderID)
}

func (s *Server) observeMissionTick() {
	if s.mission == nil || (!s.mission.Active() && !s.mission.ReturnDispatchPending()) {
		return // near-zero cost when no mission is running
	}
	if s.mgr != nil && s.mission.Active() {
		// Use the fleet safety state's authoritative last-message age.  Polling a
		// cached telemetry snapshot must not refresh centroid or arrival evidence.
		for _, id := range s.mgr.IDs() {
			d := s.mgr.Drone(id)
			if d == nil {
				continue
			}
			st := d.SafetyState()
			sample := missionPositionSampleFromSafety(st)
			s.mission.ObservePositionSample(id, sample.Lat, sample.Lon, st.AltRel, sample.Age, sample.Valid)
		}
		s.mission.Poll()
		_ = s.persistMissionState(true)
		s.releaseCompletedSwarmLeaderBinding()
		s.dispatchMissionAuthority()
	} else {
		if s.mission.Active() {
			s.mission.Poll()
		}
		_ = s.persistMissionState(true)
		s.releaseCompletedSwarmLeaderBinding()
	}
}

func missionPositionSampleFromSafety(st safety.DroneState) mission.PositionSample {
	// Mission geometry needs the age of the navigation evidence itself, not the
	// age of any MAVLink packet. A fresh HEARTBEAT must never make an old cached
	// GLOBAL_POSITION_INT/GPS fix appear fresh. Require both position and fix
	// samples, and conservatively use the older of the two (the larger age).
	navAge := st.PositionAgeSec
	if st.GpsAgeSec > navAge {
		navAge = st.GpsAgeSec
	}
	return mission.PositionSample{
		Lat: st.Lat, Lon: st.Lon,
		Age: time.Duration(navAge * float64(time.Second)),
		Valid: st.PositionAgeSec >= 0 && st.GpsAgeSec >= 0 && st.GpsFix >= 3 &&
			mission.ValidNavigationPosition(st.Lat, st.Lon),
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
	_ = s.persistMissionState(true)
	s.releaseCompletedSwarmLeaderBinding()
	// If telemetry advanced the Core-owned index, claim/send the next waypoint.
	// Shadow/default mode returns immediately and remains command-free.
	s.dispatchMissionAuthority()
}

// ── proto ↔ domain conversion ──────────────────────────────

func planToProto(p mission.MissionPlan) *pb.MissionPlan {
	out := &pb.MissionPlan{
		PlanId:               p.PlanID,
		Mode:                 modeToProto(p.Mode),
		Participants:         append([]uint32(nil), p.Participants...),
		LeaderId:             p.LeaderID,
		ArrivalRadiusM:       p.ArrivalRadiusM,
		RtlAfter:             p.RtlAfter,
		ReturnPolicy:         returnPolicyToProto(p.ReturnPolicy),
		SeparateReturnTiming: separateReturnTimingToProto(p.SeparateReturnTiming),
		ParticipantAltitudes: make(map[uint32]float64, len(p.ParticipantAltitudes)),
	}
	for id, alt := range p.ParticipantAltitudes {
		out.ParticipantAltitudes[id] = alt
	}
	for _, r := range p.Routes {
		pr := &pb.MissionRoute{DroneId: r.DroneID}
		for _, wp := range r.Points {
			pr.Points = append(pr.Points, &pb.MissionWaypoint{
				Seq: int32(wp.Seq), Lat: wp.Lat, Lon: wp.Lon, Alt: wp.Alt,
				WaitSeconds: int32(wp.WaitSeconds), Action: actionToProto(wp.Action),
			})
		}
		out.Routes = append(out.Routes, pr)
	}
	return out
}

func protoToPlan(p *pb.MissionPlan) mission.MissionPlan {
	plan := mission.MissionPlan{
		PlanID:               p.GetPlanId(),
		Mode:                 protoToMode(p.GetMode()),
		Participants:         append([]uint32(nil), p.GetParticipants()...),
		LeaderID:             p.GetLeaderId(),
		ArrivalRadiusM:       p.GetArrivalRadiusM(),
		RtlAfter:             p.GetRtlAfter(),
		ReturnPolicy:         protoToReturnPolicy(p.GetReturnPolicy()),
		ReturnPolicyExplicit: p.GetReturnPolicy() != pb.MissionReturnPolicy_MISSION_RETURN_POLICY_UNSPECIFIED,
		SeparateReturnTiming: protoToSeparateReturnTiming(p.GetSeparateReturnTiming()),
		ParticipantAltitudes: make(map[uint32]float64, len(p.GetParticipantAltitudes())),
	}
	for id, alt := range p.GetParticipantAltitudes() {
		plan.ParticipantAltitudes[id] = alt
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

func actionToProto(a mission.Action) pb.MissionWpAction {
	switch a {
	case mission.ActionServoA:
		return pb.MissionWpAction_MISSION_WP_ACTION_SERVO_A
	case mission.ActionServoB:
		return pb.MissionWpAction_MISSION_WP_ACTION_SERVO_B
	default:
		return pb.MissionWpAction_MISSION_WP_ACTION_NONE
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

func returnPolicyToProto(p mission.ReturnPolicy) pb.MissionReturnPolicy {
	switch p {
	case mission.ReturnRTLAllAfterMission:
		return pb.MissionReturnPolicy_MISSION_RETURN_POLICY_RTL_ALL_AFTER_MISSION
	case mission.ReturnSwarm:
		return pb.MissionReturnPolicy_MISSION_RETURN_POLICY_SWARM_RETURN
	case mission.ReturnWaveManaged:
		return pb.MissionReturnPolicy_MISSION_RETURN_POLICY_WAVE_MANAGED_RETURN
	default:
		return pb.MissionReturnPolicy_MISSION_RETURN_POLICY_NONE
	}
}

func protoToReturnPolicy(p pb.MissionReturnPolicy) mission.ReturnPolicy {
	switch p {
	case pb.MissionReturnPolicy_MISSION_RETURN_POLICY_UNSPECIFIED,
		pb.MissionReturnPolicy_MISSION_RETURN_POLICY_NONE:
		return mission.ReturnNone
	case pb.MissionReturnPolicy_MISSION_RETURN_POLICY_RTL_ALL_AFTER_MISSION:
		return mission.ReturnRTLAllAfterMission
	case pb.MissionReturnPolicy_MISSION_RETURN_POLICY_SWARM_RETURN:
		return mission.ReturnSwarm
	case pb.MissionReturnPolicy_MISSION_RETURN_POLICY_WAVE_MANAGED_RETURN:
		return mission.ReturnWaveManaged
	default:
		return mission.ReturnPolicy(-1)
	}
}

func separateReturnTimingToProto(p mission.SeparateReturnTiming) pb.MissionSeparateReturnTiming {
	if p == mission.SeparateReturnEachOnRouteComplete {
		return pb.MissionSeparateReturnTiming_MISSION_SEPARATE_RETURN_EACH_ON_ROUTE_COMPLETE
	}
	return pb.MissionSeparateReturnTiming_MISSION_SEPARATE_RETURN_ALL_ON_MISSION_COMPLETE
}

func protoToSeparateReturnTiming(p pb.MissionSeparateReturnTiming) mission.SeparateReturnTiming {
	switch p {
	case pb.MissionSeparateReturnTiming_MISSION_SEPARATE_RETURN_EACH_ON_ROUTE_COMPLETE:
		return mission.SeparateReturnEachOnRouteComplete
	case pb.MissionSeparateReturnTiming_MISSION_SEPARATE_RETURN_TIMING_UNSPECIFIED,
		pb.MissionSeparateReturnTiming_MISSION_SEPARATE_RETURN_ALL_ON_MISSION_COMPLETE:
		return mission.SeparateReturnAllOnMissionComplete
	default:
		return mission.SeparateReturnInvalid
	}
}

func returnStateToProto(s mission.ReturnState) pb.MissionReturnState {
	switch s {
	case mission.ReturnStatePending:
		return pb.MissionReturnState_MISSION_RETURN_STATE_PENDING
	case mission.ReturnStateReturning:
		return pb.MissionReturnState_MISSION_RETURN_STATE_RETURNING
	case mission.ReturnStateCompleted:
		return pb.MissionReturnState_MISSION_RETURN_STATE_COMPLETED
	case mission.ReturnStateSuppressed:
		return pb.MissionReturnState_MISSION_RETURN_STATE_SUPPRESSED
	case mission.ReturnStateFailed:
		return pb.MissionReturnState_MISSION_RETURN_STATE_FAILED
	case mission.ReturnStateRecoveryRequired:
		return pb.MissionReturnState_MISSION_RETURN_STATE_RECOVERY_REQUIRED
	default:
		return pb.MissionReturnState_MISSION_RETURN_STATE_INACTIVE
	}
}

func snapshotToProto(snap mission.Snapshot) *pb.MissionStateResponse {
	resp := &pb.MissionStateResponse{
		Active:                   snap.Active,
		AuthorityActive:          snap.Authority,
		RunId:                    snap.RunID,
		PlanId:                   snap.PlanID,
		Plan:                     planToProto(snap.Plan),
		Mode:                     modeToProto(snap.Mode),
		State:                    stateToProto(snap.State),
		Revision:                 snap.Revision,
		Participants:             append([]uint32(nil), snap.Participants...),
		CurrentIndex:             int32(snap.CurrentIndex),
		Arrived:                  append([]uint32(nil), snap.Arrived...),
		TerminalReason:           snap.TerminalReason,
		RecoveryRequired:         snap.RecoveryRequired,
		RecoveryPreviousState:    stateToProto(snap.RecoveryPreviousState),
		RecoveryReason:           snap.RecoveryReason,
		PersistenceSchemaVersion: snap.PersistenceSchema,
		PersistedRevision:        snap.PersistedRevision,
		AuthorityScope:           snap.AuthorityScope,
		OriginSessionId:          snap.OriginSessionID,
		CoreSessionId:            snap.CoreSessionID,
		PersistenceCompatible:    snap.RecoveryCompatible,
		PersistenceFault:         snap.PersistenceFault,
		OperationId:              snap.OperationID,
		ResolvedReturnPolicy:     returnPolicyToProto(snap.ReturnPolicy),
		ReturnState:              returnStateToProto(snap.ReturnState),
		ReturnParticipants:       append([]uint32(nil), snap.ReturnParticipants...),
		ReturnReason:             snap.ReturnReason,
		CurrentLeaderId:          snap.CurrentLeaderID,
		ActiveParticipants:       append([]uint32(nil), snap.ActiveParticipants...),
		ExcludedParticipants:     append([]uint32(nil), snap.ExcludedParticipants...),
		SuccessionReason:         snap.SuccessionReason,
		SwarmGeneration:          snap.SwarmGeneration,
		SuccessionPending:        snap.SuccessionPending,
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
	for _, rejection := range snap.Rejections {
		resp.ParticipantRejections = append(resp.ParticipantRejections, &pb.MissionParticipantRejection{
			RunId: rejection.RunID, WaypointIndex: int32(rejection.Index),
			DroneId: rejection.DroneID, Reason: rejection.Reason,
		})
	}
	return resp
}
