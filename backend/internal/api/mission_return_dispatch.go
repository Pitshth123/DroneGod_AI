package api

import (
	"context"
	"fmt"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/swarm"
)

// dispatchReturnAuthority performs the explicit mission -> Return ownership
// handoff. It is PRE-FLIP only: no deployment token enables the Engine gate.
func (s *Server) dispatchReturnAuthority() {
	if s == nil || !s.missionAuthority || s.mission == nil || !s.mission.ReturnDispatchPending() {
		return
	}
	if !s.missionDurableForReturnDispatch() {
		return
	}

	s.missionDispatchMu.Lock()
	if s.missionSendActive || len(s.missionGroupSends) != 0 || s.missionReturnBatch != nil {
		s.missionDispatchMu.Unlock()
		return // mission final send must retire before Return claims navigation
	}
	snap := s.mission.Snapshot()
	if snap.ReturnState != mission.ReturnStatePending || s.anyReservedLocked(snap.ReturnParticipants) {
		if snap.ReturnState == mission.ReturnStatePending && s.anyReservedLocked(snap.ReturnParticipants) {
			s.mission.SuppressReturn(snap.RunID, "automatic Return suppressed by existing operator command ownership")
		}
		s.missionDispatchMu.Unlock()
		_ = s.persistMissionState(false)
		return
	}
	for _, id := range snap.ReturnParticipants {
		if s.missionParticipantFailsafeActive(id) {
			s.mission.SuppressReturn(snap.RunID,
				fmt.Sprintf("automatic Return suppressed: Drone %d is failsafe-owned", id))
			s.missionDispatchMu.Unlock()
			_ = s.persistMissionState(false)
			return
		}
	}
	intent, ok := s.mission.ClaimReturnIntent()
	if !ok {
		s.missionDispatchMu.Unlock()
		return
	}

	if intent.Policy == mission.ReturnSwarm {
		// Mission commands neither leader nor followers. The existing swarm
		// manager registers its own revocable Return authority while this short
		// ownership lock is held, then executes asynchronously.
		coordinator := s.missionSwarmReturnCoordinator()
		if coordinator == nil {
			s.mission.FailReturn(intent.RunID, "swarm Return manager unavailable")
			s.missionDispatchMu.Unlock()
			_ = s.persistMissionState(false)
			return
		}
		parent := s.ctx
		if parent == nil {
			parent = context.Background()
		}
		err := coordinator.ReturnAndLand(parent, intent.Participants, 0, 0)
		if err != nil {
			s.mission.FailReturn(intent.RunID, err.Error())
		}
		var results <-chan swarm.ReturnResult
		if err == nil {
			results = coordinator.ReturnResults()
		}
		s.missionDispatchMu.Unlock()
		_ = s.persistMissionState(false)
		if results != nil {
			go func(runID uint64, resultCh <-chan swarm.ReturnResult) {
				result, ok := <-resultCh
				if !ok {
					s.mission.FailReturn(runID, "swarm Return ended without a semantic result")
				} else {
					switch result.Outcome {
					case swarm.ReturnOutcomeSucceeded:
						s.mission.CompleteReturn(runID)
					case swarm.ReturnOutcomeCancelled:
						s.mission.SuppressReturn(runID, result.Reason)
					default:
						s.mission.FailReturn(runID, result.Reason)
					}
				}
				_ = s.persistMissionState(false)
			}(intent.RunID, results)
		}
		return
	}
	if intent.Policy != mission.ReturnRTLAllAfterMission {
		s.mission.FailReturn(intent.RunID, "unsupported generic Return policy")
		s.missionDispatchMu.Unlock()
		_ = s.persistMissionState(false)
		return
	}
	executor, ok := s.missionExec.(missionReturnCommandExecutor)
	if !ok {
		s.mission.FailReturn(intent.RunID, "mission Return command service unavailable")
		s.missionDispatchMu.Unlock()
		_ = s.persistMissionState(false)
		return
	}
	batch := s.prepareReturnBatchLocked(intent)
	s.missionReturnBatch = batch
	s.missionReturnRunID = intent.RunID
	s.missionDispatchMu.Unlock()

	// RETURNING evidence must be durable before the first FC write. A crash after
	// this point restores command-inert recovery, never an automatic retry.
	if err := s.persistMissionState(false); err != nil {
		s.missionDispatchMu.Lock()
		s.invalidateReturnLocked(intent.RunID, "Return persistence failed: "+err.Error())
		s.missionDispatchMu.Unlock()
		_ = s.persistMissionState(false)
		return
	}

	failed := ""
	for _, id := range intent.Participants {
		claim := batch.claims[id]
		result := s.runClaimed("MissionReturnRTL", intent.RequestID, claim,
			func(ctx context.Context, droneID uint32) *pb.CommandResult {
				if err := executor.RTL(ctx, droneID); err != nil {
					return &pb.CommandResult{Ok: false, Command: "MissionReturnRTL", DroneId: droneID,
						RequestId: intent.RequestID, Message: err.Error()}
				}
				return &pb.CommandResult{Ok: true, Command: "MissionReturnRTL", DroneId: droneID,
					RequestId: intent.RequestID, Message: "ACCEPTED"}
			})
		if result == nil || !result.Ok {
			if s.claimSuperseded(claim) {
				failed = "automatic Return preempted by newer operator/emergency authority"
				break
			}
			if result == nil {
				failed = fmt.Sprintf("Drone %d Return returned no result", id)
			} else {
				failed = result.Message
			}
			break
		}
	}

	s.missionDispatchMu.Lock()
	current := s.missionReturnBatch == batch && s.missionReturnRunID == intent.RunID
	if current {
		s.missionReturnBatch = nil
		s.missionReturnRunID = 0
	}
	s.missionDispatchMu.Unlock()
	batch.release(s)
	if current {
		if failed == "" {
			s.mission.CompleteReturn(intent.RunID)
		} else {
			s.mission.FailReturn(intent.RunID, failed)
		}
	}
	_ = s.persistMissionState(false)
}

// prepareReturnBatchLocked establishes whole-batch per-target claims before any
// long FC ACK wait. Caller holds missionDispatchMu and has already checked idle.
func (s *Server) prepareReturnBatchLocked(intent mission.ReturnIntent) *cmdBatch {
	batch := newCmdBatch()
	base := s.ctx
	if base == nil {
		base = context.Background()
	}
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
	for _, id := range intent.Participants {
		execCtx, cancel, guard := newClaimContext(base)
		lease := &cmdLease{ids: []uint32{id}, cancel: cancel, guard: guard}
		s.reservations[id] = lease
		batch.claims[id] = &cmdClaim{id: id, ctx: execCtx, lease: lease}
	}
	return batch
}

// invalidateReturnLocked makes pending/returning intent permanently stale and
// closes every final-write gate before publishing cancellation. Caller holds the
// API ownership lock.
func (s *Server) invalidateReturnLocked(runID uint64, reason string) bool {
	invalidated := s.mission != nil && s.mission.SuppressReturn(runID, reason)
	if s.missionReturnBatch == nil || s.missionReturnRunID != runID {
		return invalidated
	}
	for id, claim := range s.missionReturnBatch.claims {
		if claim == nil || claim.lease == nil {
			continue
		}
		preemptLease(claim.lease)
		if s.reservations[id] == claim.lease {
			delete(s.reservations, id)
		}
	}
	s.missionReturnBatch = nil
	s.missionReturnRunID = 0
	return true
}
