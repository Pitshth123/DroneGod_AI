package api

import (
	"context"
	"sync"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/fleet"
)

// V3-S07 command reservations.
//
// Before S07 every command handler held missionDispatchMu across the whole FC
// ACK wait.  A long normal command (notably Takeoff, whose GUIDED→arm→takeoff
// sequence budget is up to ~90 s) therefore serialized an operator emergency /
// takeover behind it.  This file separates the SHORT ownership critical section
// (mission-ownership check + reservation bookkeeping, under missionDispatchMu)
// from the LONG command execution (the FC send, run with the lock released).
//
// Invariants preserved:
//   - A reservation still blocks StartMission on the same drone, so a Core
//     mission can never appear between a manual command's ownership check and
//     its send (no dual authority).
//   - A takeover/emergency command preempts an in-flight normal op by cancelling
//     its exec context, so it is never blocked behind a long normal FC wait.
//   - releaseLease only clears the slot it still owns, so a preempting takeover's
//     reservation is never removed by the normal op it replaced.

// cmdLease is one command's reservation over a set of drones.  It is created in
// the short critical section and released by the executing handler after the
// (unlocked) send completes.
type takeoverPriority uint8

// Takeover ordering is explicit so two conflicting operator takeovers never run
// concurrently with nondeterministic "last FC write wins" behaviour.  A newer
// command may preempt an equal/lower class, but a weaker command cannot cancel a
// stronger in-flight emergency.  KILL remains the strongest class.
const (
	takeoverPriorityNavigation takeoverPriority = 10 // HOLD / CHANGE ALT / EQUALIZE
	takeoverPriorityRTL        takeoverPriority = 20
	takeoverPriorityLand       takeoverPriority = 30
	takeoverPriorityDisarm     takeoverPriority = 35
	takeoverPriorityStopAll    takeoverPriority = 40 // cockpit E-STOP semantics
	takeoverPriorityKill       takeoverPriority = 50
)

type cmdLease struct {
	ids      []uint32
	takeover bool
	priority takeoverPriority
	cancel   context.CancelFunc
	guard    *commandSendGuard
}

// commandSendGuard makes accepting a newer intent atomic with respect to the
// old intent's final transport write.  Cancellation may wait for conn.Send()
// itself (normally a short enqueue/write), but never for an FC ACK.  Therefore:
// either the old write committed before the newer intent was accepted, or the
// newer intent closes the guard first and every later stale write is refused.
type commandSendGuard struct {
	mu        sync.Mutex
	cancelled bool
}

func (g *commandSendGuard) DoSend(send func() error) error {
	if g == nil {
		return send()
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.cancelled {
		return context.Canceled
	}
	return send()
}

func (g *commandSendGuard) Cancel() {
	if g == nil {
		return
	}
	g.mu.Lock()
	g.cancelled = true
	g.mu.Unlock()
}

func newClaimContext(parent context.Context) (context.Context, context.CancelFunc, *commandSendGuard) {
	ctx, cancel := context.WithCancel(parent)
	guard := &commandSendGuard{}
	return fleet.WithSendGuard(ctx, guard), cancel, guard
}

func preemptLease(lease *cmdLease) {
	if lease == nil {
		return
	}
	// Close the final-write gate before publishing cancellation to the longer
	// command sequence.  Once this returns, no new transport write using the old
	// lease can begin.
	lease.guard.Cancel()
	if lease.cancel != nil {
		lease.cancel()
	}
}

// droneReservedLocked reports whether id currently has an active reservation.
// Caller must hold missionDispatchMu.
func (s *Server) droneReservedLocked(id uint32) bool {
	if s == nil || s.reservations == nil {
		return false
	}
	_, ok := s.reservations[id]
	return ok
}

// anyReservedLocked reports whether any of ids is reserved. Caller holds the lock.
func (s *Server) anyReservedLocked(ids []uint32) bool {
	for _, id := range ids {
		if s.droneReservedLocked(id) {
			return true
		}
	}
	return false
}

// tryReserveLocked reserves every id for a NORMAL command.  It fails without
// side effects if any id is already reserved (busy); normal ops do not preempt
// one another.  Caller must hold missionDispatchMu.
func (s *Server) tryReserveLocked(ids []uint32, cancel context.CancelFunc) (*cmdLease, bool) {
	if s.anyReservedLocked(ids) {
		return nil, false
	}
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
	lease := &cmdLease{ids: append([]uint32(nil), ids...), cancel: cancel, guard: &commandSendGuard{}}
	for _, id := range ids {
		s.reservations[id] = lease
	}
	return lease, true
}

// preemptReserveLocked reserves ids for a TAKEOVER/emergency command.
//
// It first performs an all-target priority check with no side effects.  If any
// target is already owned by a *stronger* takeover, the new weaker command is
// rejected as a whole.  Otherwise every overlapping normal/equal-or-weaker
// lease is cancelled exactly once, then the new lease takes the requested slots.
// This gives deterministic latest-intent ordering without ever making KILL wait
// behind, or get overwritten by, HOLD/RTL/LAND/etc.  Caller holds the lock.
func (s *Server) preemptReserveLocked(ids []uint32, cancel context.CancelFunc,
	priority takeoverPriority) (*cmdLease, bool) {
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
	for _, id := range ids {
		if prev := s.reservations[id]; prev != nil && prev.takeover && prev.priority > priority {
			return nil, false
		}
	}

	cancelled := make(map[*cmdLease]struct{})
	for _, id := range ids {
		prev := s.reservations[id]
		if prev == nil || prev.cancel == nil {
			continue
		}
		if _, seen := cancelled[prev]; seen {
			continue
		}
		preemptLease(prev) // normal or equal/weaker takeover: newer intent wins
		cancelled[prev] = struct{}{}
	}

	lease := &cmdLease{
		ids: append([]uint32(nil), ids...), takeover: true,
		priority: priority, cancel: cancel, guard: &commandSendGuard{},
	}
	for _, id := range ids {
		s.reservations[id] = lease
	}
	return lease, true
}

// releaseLease removes lease from the reservation map wherever it is still the
// current entry (a later takeover may have replaced it).  It acquires the lock.
func (s *Server) releaseLease(lease *cmdLease) {
	if s == nil || lease == nil {
		return
	}
	s.missionDispatchMu.Lock()
	for _, id := range lease.ids {
		if s.reservations[id] == lease {
			delete(s.reservations, id)
		}
	}
	s.missionDispatchMu.Unlock()
}

// missionBusyReject is returned when a normal command cannot start because
// another command is already in flight for the drone.
func commandBusyReject(command string, id uint32, requestID string) *pb.CommandResult {
	return &pb.CommandResult{
		Ok: false, Command: command, DroneId: id, RequestId: requestID,
		Message: "another command is in progress for this drone",
	}
}

func strongerTakeoverReject(command string, id uint32, requestID string) *pb.CommandResult {
	return &pb.CommandResult{
		Ok: false, Command: command, DroneId: id, RequestId: requestID,
		Message: "higher-priority takeover/emergency already in progress for this drone",
	}
}

// swarmNavigationReservationRejectLocked prevents formation/RETURN navigation
// from crossing an already-accepted command intent. START and an unscoped RETURN
// affect the whole swarm, so all=true rejects on any active reservation. Caller
// must hold missionDispatchMu.
func (s *Server) swarmNavigationReservationRejectLocked(command, requestID string,
	ids []uint32, all bool) *pb.CommandResult {
	if s == nil || len(s.reservations) == 0 {
		return nil
	}
	if all {
		var first uint32
		found := false
		for id := range s.reservations {
			if !found || id < first {
				first = id
				found = true
			}
		}
		if found {
			return commandBusyReject(command, first, requestID)
		}
		return nil
	}
	for _, id := range ids {
		if s.droneReservedLocked(id) {
			return commandBusyReject(command, id, requestID)
		}
	}
	return nil
}

// idempotentNormal runs a NORMAL command for one drone under request_id
// idempotency, with the drone reserved INSIDE the idempotent closure.
//
// This ordering is the V3-S07 review fix for request_id × reservation: a same
// request_id retry is deduped by the idem store (wait for / replay the first
// result) BEFORE it can ever reach the reservation, so a retry can never
// busy-reject itself or double-send. Only a genuinely new request_id (or a
// concurrent DIFFERENT command on the same drone) takes/loses the reservation.
func (s *Server) idempotentNormal(ctx context.Context, command, requestID string, id uint32,
	send func(context.Context, uint32) *pb.CommandResult) *pb.CommandResult {
	run := func() *pb.CommandResult {
		execCtx, cancel, guard := newClaimContext(ctx)
		defer cancel()
		s.missionDispatchMu.Lock()
		if s.missionOwnsDroneLocked(id) {
			s.missionDispatchMu.Unlock()
			return missionOwnershipReject(command, id, requestID)
		}
		lease, ok := s.tryReserveLocked([]uint32{id}, cancel)
		if lease != nil {
			lease.guard = guard
		}
		s.missionDispatchMu.Unlock()
		if !ok {
			return commandBusyReject(command, id, requestID)
		}
		defer s.releaseLease(lease)
		return send(execCtx, id)
	}
	if s.cmd == nil { // state-only test server: no idem store, run the guard directly
		return run()
	}
	return s.cmd.Idempotent(requestID, id, run)
}

// idempotentTakeover runs a TAKEOVER command for one drone under request_id
// idempotency, cancelling the Core mission + preempting weaker/normal ops INSIDE
// the idempotent closure. A same request_id retry is deduped (wait/replay)
// before it can re-cancel the mission or preempt the ORIGINAL identical request.
func (s *Server) idempotentTakeover(ctx context.Context, command, requestID string, id uint32,
	priority takeoverPriority, send func(context.Context, uint32) *pb.CommandResult) *pb.CommandResult {
	run := func() *pb.CommandResult {
		execCtx, cancel, guard := newClaimContext(ctx)
		defer cancel()
		defer func() { _ = s.persistMissionState(false) }()
		s.missionDispatchMu.Lock()
		lease, reserved := s.preemptReserveLocked([]uint32{id}, cancel, priority)
		if lease != nil {
			lease.guard = guard
		}
		if reserved {
			// Mission/Return side effects are allowed only for an accepted takeover.
			// A weaker request rejected behind a stronger reservation must be a
			// complete no-op to current mission/Return ownership.
			s.stopSwarmForTargetsLocked([]uint32{id})
			s.cancelMissionForOperatorTargetsLocked([]uint32{id})
		}
		s.missionDispatchMu.Unlock()
		if !reserved {
			return strongerTakeoverReject(command, id, requestID)
		}
		defer s.releaseLease(lease)
		return send(execCtx, id)
	}
	if s.cmd == nil { // state-only test server: no idem store, run the guard directly
		return run()
	}
	return s.cmd.Idempotent(requestID, id, run)
}

// ── Batch intent/reservation model ────────────────────────────────────────
// A multi-drone RPC is one operator intent even when FC sends are sequential.
// Claim every accepted target before any long FC wait so a stale older batch
// cannot resume on a later target after a newer STOP/KILL has completed.

const batchIdempotencyDroneID uint32 = 1<<32 - 1

type cmdClaim struct {
	id    uint32
	ctx   context.Context
	lease *cmdLease
}

type cmdBatch struct {
	claims   map[uint32]*cmdClaim
	rejected map[uint32]*pb.CommandResult
}

func newCmdBatch() *cmdBatch {
	return &cmdBatch{claims: make(map[uint32]*cmdClaim), rejected: make(map[uint32]*pb.CommandResult)}
}

func (b *cmdBatch) release(s *Server) {
	if b == nil || s == nil {
		return
	}
	for _, claim := range b.claims {
		if claim != nil && claim.lease != nil && claim.lease.cancel != nil {
			claim.lease.cancel()
		}
		s.releaseLease(claim.lease)
	}
}

// claimCurrent is the typed stale-intent check. Once a newer command replaces
// this exact lease, releasing the newer lease never restores the old pointer.
func (s *Server) claimCurrent(claim *cmdClaim) bool {
	if s == nil || claim == nil || claim.lease == nil || claim.ctx == nil || claim.ctx.Err() != nil {
		return false
	}
	s.missionDispatchMu.Lock()
	current := s.reservations[claim.id] == claim.lease
	s.missionDispatchMu.Unlock()
	return current && claim.ctx.Err() == nil
}

// claimSuperseded is the typed operator-preemption signal. It checks ownership
// identity only (not human-readable error text and not generic caller ctx
// cancellation). A newer accepted command permanently replaces this lease.
func (s *Server) claimSuperseded(claim *cmdClaim) bool {
	if s == nil || claim == nil || claim.lease == nil {
		return true
	}
	s.missionDispatchMu.Lock()
	superseded := s.reservations[claim.id] != claim.lease
	s.missionDispatchMu.Unlock()
	return superseded
}

func commandPreemptedReject(command string, id uint32, requestID string) *pb.CommandResult {
	return &pb.CommandResult{Ok: false, Command: command, DroneId: id, RequestId: requestID,
		Message: "preempted — superseded by newer operator intent"}
}

// idempotentBatch puts request_id idempotency outside batch reservation so a
// retry waits/replays the original request before it can reserve/preempt itself.
func (s *Server) idempotentBatch(requestID string, run func() *pb.CommandResult) *pb.CommandResult {
	if s == nil || s.cmd == nil || requestID == "" {
		return run()
	}
	return s.cmd.Idempotent(requestID, batchIdempotencyDroneID, run)
}

func (s *Server) prepareNormalBatch(ctx context.Context, command, requestID string, ids []uint32) *cmdBatch {
	b := newCmdBatch()
	s.missionDispatchMu.Lock()
	defer s.missionDispatchMu.Unlock()
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
	for _, id := range ids {
		if s.missionOwnsDroneLocked(id) {
			b.rejected[id] = missionOwnershipReject(command, id, requestID)
			continue
		}
		if s.reservations[id] != nil {
			b.rejected[id] = commandBusyReject(command, id, requestID)
			continue
		}
		execCtx, cancel, guard := newClaimContext(ctx)
		lease := &cmdLease{ids: []uint32{id}, cancel: cancel, guard: guard}
		s.reservations[id] = lease
		b.claims[id] = &cmdClaim{id: id, ctx: execCtx, lease: lease}
	}
	return b
}

func (s *Server) prepareTakeoverBatch(ctx context.Context, command, requestID string,
	ids []uint32, priority takeoverPriority) *cmdBatch {
	b := newCmdBatch()
	s.missionDispatchMu.Lock()
	defer s.missionDispatchMu.Unlock()
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
	cancelled := make(map[*cmdLease]struct{})
	accepted := make([]uint32, 0, len(ids))
	for _, id := range ids {
		prev := s.reservations[id]
		if prev != nil && prev.takeover && prev.priority > priority {
			b.rejected[id] = strongerTakeoverReject(command, id, requestID)
			continue
		}
		if prev != nil {
			if _, seen := cancelled[prev]; !seen {
				preemptLease(prev)
				cancelled[prev] = struct{}{}
			}
		}
		execCtx, cancel, guard := newClaimContext(ctx)
		lease := &cmdLease{ids: []uint32{id}, takeover: true, priority: priority, cancel: cancel, guard: guard}
		s.reservations[id] = lease
		b.claims[id] = &cmdClaim{id: id, ctx: execCtx, lease: lease}
		accepted = append(accepted, id)
	}
	// Only targets that actually won reservation/priority arbitration may alter
	// Mission, formation, or Return ownership. Rejected targets are side-effect
	// free even when they appear in the current Return participant set.
	if len(accepted) != 0 {
		s.stopSwarmForTargetsLocked(accepted)
		s.cancelMissionForOperatorTargetsLocked(accepted)
	}
	return b
}

func (s *Server) runClaimed(command, requestID string, claim *cmdClaim,
	send func(context.Context, uint32) *pb.CommandResult) *pb.CommandResult {
	if claim == nil {
		return commandPreemptedReject(command, 0, requestID)
	}
	run := func() *pb.CommandResult {
		if !s.claimCurrent(claim) {
			return commandPreemptedReject(command, claim.id, requestID)
		}
		return send(claim.ctx, claim.id)
	}
	if s.cmd == nil || requestID == "" {
		return run()
	}
	return s.cmd.Idempotent(requestID, claim.id, run)
}

func (s *Server) runNormalBatch(ctx context.Context, command, requestID string, ids []uint32,
	send func(context.Context, uint32) *pb.CommandResult) *pb.CommandResult {
	return s.idempotentBatch(requestID, func() *pb.CommandResult {
		batch := s.prepareNormalBatch(ctx, command, requestID, ids)
		defer batch.release(s)
		rs := make([]*pb.CommandResult, 0, len(ids))
		for _, id := range ids {
			if rejected := batch.rejected[id]; rejected != nil {
				rs = append(rs, rejected)
				continue
			}
			rs = append(rs, s.runClaimed(command, requestID, batch.claims[id], send))
		}
		return aggregate(command, requestID, rs)
	})
}

func (s *Server) runTakeoverBatch(ctx context.Context, command, requestID string, ids []uint32,
	priority takeoverPriority, send func(context.Context, uint32) *pb.CommandResult) *pb.CommandResult {
	return s.idempotentBatch(requestID, func() *pb.CommandResult {
		defer func() { _ = s.persistMissionState(false) }()
		batch := s.prepareTakeoverBatch(ctx, command, requestID, ids, priority)
		defer batch.release(s)
		rs := make([]*pb.CommandResult, 0, len(ids))
		for _, id := range ids {
			if rejected := batch.rejected[id]; rejected != nil {
				rs = append(rs, rejected)
				continue
			}
			rs = append(rs, s.runClaimed(command, requestID, batch.claims[id], send))
		}
		return aggregate(command, requestID, rs)
	})
}
