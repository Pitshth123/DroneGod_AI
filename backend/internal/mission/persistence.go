package mission

import (
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"
	"time"
)

const DurableMissionSchemaVersion uint32 = 1

var (
	ErrNoDurableMission   = errors.New("mission: no durable mission record")
	ErrStaleDurableRecord = errors.New("mission: stale durable record write")
)

// DurableStore is implemented by the existing Core SQLite store. Mission state
// remains command-free and depends only on this small persistence contract.
type DurableStore interface {
	AllocateMissionRunID() (uint64, error)
	SaveMissionRecord(DurableMissionRecord) error
	LoadMissionRecord() (*DurableMissionRecord, error)
	DeleteMissionRecord(runID uint64) error
	LookupMissionOperation(operationID string) (runID uint64, found bool, err error)
}

// PersistenceLoadError retains safe envelope metadata even when the payload is
// truncated, corrupt or otherwise undecodable.
type PersistenceLoadError struct {
	RunID         uint64
	SchemaVersion uint32
	Err           error
}

func (e *PersistenceLoadError) Error() string {
	if e == nil {
		return "mission persistence load error"
	}
	return fmt.Sprintf("mission persistence load failed (run_id=%d schema=%d): %v",
		e.RunID, e.SchemaVersion, e.Err)
}

func (e *PersistenceLoadError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

type DurableMissionWait struct {
	Scope      uint32  `json:"scope"`
	Index      int     `json:"index"`
	RemainingS float64 `json:"remaining_s"`
	TotalS     int     `json:"total_s"`
}

// DurableMissionRecord is the versioned, deterministic state evidence stored in
// SQLite. It deliberately excludes contexts, goroutines, deadlines, command
// claims, send guards and S09 generation tokens.
type DurableMissionRecord struct {
	SchemaVersion         uint32                 `json:"schema_version"`
	RunID                 uint64                 `json:"run_id"`
	OperationID           string                 `json:"operation_id,omitempty"`
	Plan                  MissionPlan            `json:"plan"`
	Mode                  Mode                   `json:"mode"`
	Participants          []uint32               `json:"participants"`
	LeaderID              uint32                 `json:"leader_id,omitempty"`
	ActiveParticipants    []uint32               `json:"active_participants,omitempty"`
	ExcludedParticipants  []uint32               `json:"excluded_participants,omitempty"`
	SwarmGeneration       uint64                 `json:"swarm_generation,omitempty"`
	SuccessionPending     bool                   `json:"succession_pending,omitempty"`
	SuccessionReason      string                 `json:"succession_reason,omitempty"`
	AuthorityActive       bool                   `json:"authority_active"`
	AuthorityScope        string                 `json:"authority_scope,omitempty"`
	State                 State                  `json:"state"`
	CurrentIndex          int                    `json:"current_index"`
	SeparateIndexes       map[uint32]int         `json:"separate_indexes,omitempty"`
	Arrived               []uint32               `json:"arrived,omitempty"`
	Waits                 []DurableMissionWait   `json:"waits,omitempty"`
	ParticipantRejections []ParticipantRejection `json:"participant_rejections,omitempty"`
	LastTransition        Transition             `json:"last_transition"`
	TerminalReason        string                 `json:"terminal_reason,omitempty"`
	CreatedAtUnixMs       int64                  `json:"created_at_unix_ms"`
	UpdatedAtUnixMs       int64                  `json:"updated_at_unix_ms"`
	Revision              uint64                 `json:"revision"`
	OriginSessionID       string                 `json:"origin_session_id"`
	WriterSessionID       string                 `json:"writer_session_id"`
	RecoveryRequired      bool                   `json:"recovery_required"`
	RecoveryPreviousState State                  `json:"recovery_previous_state"`
	RecoveryReason        string                 `json:"recovery_reason,omitempty"`
	ReturnPolicy          ReturnPolicy           `json:"return_policy"`
	ReturnState           ReturnState            `json:"return_state"`
	ReturnParticipants    []uint32               `json:"return_participants,omitempty"`
	ReturnReason          string                 `json:"return_reason,omitempty"`
	ReturnUpdatedAtUnixMs int64                  `json:"return_updated_at_unix_ms,omitempty"`
}

// RecoveryIssue is an operator-visible fail-safe state for a record that cannot
// be trusted enough to reconstruct a MissionPlan.
type RecoveryIssue struct {
	RunID         uint64
	SchemaVersion uint32
	Reason        string
}

func validDurableState(s State) bool {
	return s >= StateIdle && s <= StateCompleted
}

func validAuthorityScope(scope string) bool {
	switch scope {
	case "", "core-single", "core-single-wait", "core-grouped-multi",
		"core-separate", "core-swarm-leader", "core-wave", "core-payload":
		return true
	default:
		return false
	}
}

// ValidateDurableMissionRecord rejects every inconsistency instead of guessing
// what an aircraft may have been doing.
func ValidateDurableMissionRecord(r DurableMissionRecord) error {
	if r.SchemaVersion != DurableMissionSchemaVersion {
		return fmt.Errorf("unsupported mission persistence schema %d (want %d)",
			r.SchemaVersion, DurableMissionSchemaVersion)
	}
	if r.RunID == 0 || r.Revision == 0 {
		return errors.New("durable mission requires non-zero run_id and revision")
	}
	if r.OriginSessionID == "" || r.WriterSessionID == "" {
		return errors.New("durable mission requires origin and writer session ids")
	}
	if !validDurableState(r.State) || !validDurableState(r.RecoveryPreviousState) {
		return errors.New("durable mission contains invalid state enum")
	}
	if err := r.Plan.Validate(); err != nil {
		return fmt.Errorf("durable mission plan: %w", err)
	}
	resolvedPlan, err := r.Plan.resolveReturnPolicy()
	if err != nil {
		return fmt.Errorf("durable mission return policy: %w", err)
	}
	if resolvedPlan.ReturnPolicy != r.ReturnPolicy || !r.ReturnPolicy.valid() || !r.ReturnState.valid() {
		return errors.New("durable mission return policy/state is inconsistent")
	}
	if r.Mode != r.Plan.Mode {
		return errors.New("durable mission mode does not match plan")
	}
	if !reflect.DeepEqual(r.Participants, canonicalParticipants(r.Plan.Participants)) {
		return errors.New("durable mission participants are not canonical or do not match plan")
	}
	if r.Mode == ModeSwarmLeader {
		original := orderedParticipants(r.Plan.Participants)
		active := r.ActiveParticipants
		if len(active) == 0 && len(r.ExcludedParticipants) == 0 && r.SwarmGeneration == 0 {
			active = original // backwards-compatible pre-succession record
		}
		seen := make(map[uint32]bool, len(original))
		for _, id := range active {
			if seen[id] || !containsParticipant(original, id) {
				return errors.New("durable SWARM active membership is invalid")
			}
			seen[id] = true
		}
		for _, id := range r.ExcludedParticipants {
			if seen[id] || !containsParticipant(original, id) {
				return errors.New("durable SWARM excluded membership is invalid")
			}
			seen[id] = true
		}
		if len(seen) != len(original) {
			return errors.New("durable SWARM membership does not partition original participants")
		}
		if !r.State.IsTerminal() && (len(active) < 2 || r.LeaderID == 0 || !containsParticipant(active, r.LeaderID)) {
			return errors.New("active durable SWARM requires eligible leader and minimum membership")
		}
		if r.SuccessionPending && r.State.IsTerminal() {
			return errors.New("terminal durable SWARM cannot retain pending succession")
		}
	} else if r.LeaderID != r.Plan.LeaderID || len(r.ActiveParticipants) != 0 ||
		len(r.ExcludedParticipants) != 0 || r.SwarmGeneration != 0 || r.SuccessionPending {
		return errors.New("non-SWARM durable mission contains succession state")
	}
	if !validAuthorityScope(r.AuthorityScope) {
		return fmt.Errorf("durable mission authority scope %q is invalid", r.AuthorityScope)
	}
	if r.State.IsTerminal() && r.AuthorityActive {
		return errors.New("terminal durable mission cannot retain authority")
	}
	if r.RecoveryRequired && r.State != StateInterrupted {
		return errors.New("recovery-required durable mission must be INTERRUPTED")
	}
	if r.ReturnState.pendingAuthority() && r.State != StateCompleted {
		return errors.New("pending/returning Return requires naturally COMPLETED mission")
	}
	if r.ReturnPolicy == ReturnNone && r.ReturnState != ReturnStateInactive {
		return errors.New("NONE return policy cannot have Return lifecycle state")
	}
	if r.ReturnState != ReturnStateInactive {
		expectedReturnParticipants := r.Participants
		if r.Mode == ModeSwarmLeader && len(r.ActiveParticipants) != 0 {
			expectedReturnParticipants = r.ActiveParticipants
		}
		if !reflect.DeepEqual(r.ReturnParticipants, expectedReturnParticipants) || r.ReturnUpdatedAtUnixMs <= 0 {
			return errors.New("durable Return participants/timestamp are inconsistent")
		}
	}
	if r.RecoveryRequired && r.ReturnState.pendingAuthority() {
		return errors.New("recovery-required mission cannot retain live Return authority")
	}
	if r.CreatedAtUnixMs <= 0 || r.UpdatedAtUnixMs < r.CreatedAtUnixMs {
		return errors.New("durable mission timestamps are invalid")
	}
	if r.LastTransition.Revision == 0 || r.LastTransition.Revision > r.Revision ||
		!validDurableState(r.LastTransition.From) || !validDurableState(r.LastTransition.To) {
		return errors.New("durable mission last transition is inconsistent")
	}
	for _, route := range r.Plan.Routes {
		for _, wp := range route.Points {
			if !validPosition(wp.Lat, wp.Lon) || math.IsNaN(wp.Alt) || math.IsInf(wp.Alt, 0) ||
				wp.Action < ActionNone || wp.Action > ActionServoB {
				return errors.New("durable mission contains invalid waypoint geometry/action")
			}
		}
	}
	for id, alt := range r.Plan.ParticipantAltitudes {
		if !containsParticipant(r.Participants, id) || math.IsNaN(alt) || math.IsInf(alt, 0) || alt <= 0 {
			return errors.New("durable mission contains invalid participant altitude")
		}
	}
	switch r.Mode {
	case ModeGrouped, ModeSwarmLeader:
		n := len(r.Plan.sharedRoute().Points)
		if r.CurrentIndex < 0 || r.CurrentIndex > n ||
			(!r.State.IsTerminal() && r.CurrentIndex >= n) {
			return errors.New("durable mission shared index is out of range")
		}
	case ModeSeparate:
		if len(r.SeparateIndexes) != len(r.Participants) {
			return errors.New("durable SEPARATE indexes are incomplete")
		}
		for _, id := range r.Participants {
			route, _ := r.Plan.routeFor(id)
			idx, ok := r.SeparateIndexes[id]
			if !ok || idx < 0 || idx > len(route.Points) {
				return fmt.Errorf("durable SEPARATE index for drone %d is invalid", id)
			}
		}
	}
	if r.State.IsTerminal() && !r.RecoveryRequired && len(r.Waits) != 0 {
		return errors.New("terminal durable mission cannot retain an active WAIT")
	}
	for _, w := range r.Waits {
		if w.Index < 0 || w.TotalS < 0 || w.RemainingS < 0 ||
			math.IsNaN(w.RemainingS) || math.IsInf(w.RemainingS, 0) {
			return errors.New("durable mission WAIT is invalid")
		}
		if r.Mode != ModeSeparate && w.Scope != 0 {
			return errors.New("grouped durable WAIT must use scope 0")
		}
		if r.Mode == ModeSeparate && !containsParticipant(r.Participants, w.Scope) {
			return errors.New("durable SEPARATE WAIT has unknown scope")
		}
	}
	for _, rejection := range r.ParticipantRejections {
		if rejection.RunID != r.RunID || !containsParticipant(r.Participants, rejection.DroneID) ||
			rejection.Index < 0 || rejection.Reason == "" {
			return errors.New("durable participant rejection is inconsistent")
		}
	}
	return nil
}

func containsParticipant(ids []uint32, id uint32) bool {
	for _, candidate := range ids {
		if candidate == id {
			return true
		}
	}
	return false
}

// DurableRecord returns one immutable persistence snapshot. Repeated telemetry
// reads do not change Revision, so the Server can skip unnecessary SQLite writes.
func (e *Engine) DurableRecord(writerSessionID string) (*DurableMissionRecord, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil {
		return nil, false
	}
	origin := run.originSessionID
	if origin == "" {
		origin = writerSessionID
	}
	updated := run.lastTransition.AtUnixMs
	if run.returnUpdatedAtMs > updated {
		updated = run.returnUpdatedAtMs
	}
	if updated < run.createdUnixMs {
		updated = run.createdUnixMs
	}
	record := &DurableMissionRecord{
		SchemaVersion: DurableMissionSchemaVersion,
		RunID:         run.RunID, OperationID: run.OperationID, Plan: run.Plan.clone(),
		Mode: run.Plan.Mode, Participants: append([]uint32(nil), run.Participants...),
		LeaderID:             run.leaderID(),
		ActiveParticipants:   append([]uint32(nil), run.activeParticipants...),
		ExcludedParticipants: append([]uint32(nil), run.excludedParticipants...),
		SwarmGeneration:      run.swarmGeneration,
		SuccessionPending:    run.successionPending,
		SuccessionReason:     run.successionReason,
		AuthorityActive:      e.authority && !run.State.IsTerminal() && !run.recoveryRequired,
		AuthorityScope:       e.authorityScope,
		State:                run.State, CurrentIndex: run.currentIndex,
		Arrived:        make([]uint32, 0, len(run.arrived)),
		LastTransition: run.lastTransition, TerminalReason: run.terminalReason,
		CreatedAtUnixMs: run.createdUnixMs, UpdatedAtUnixMs: updated,
		Revision: run.Revision, OriginSessionID: origin, WriterSessionID: writerSessionID,
		RecoveryRequired:      run.recoveryRequired,
		RecoveryPreviousState: run.recoveryPreviousState,
		RecoveryReason:        run.recoveryReason,
		ReturnPolicy:          run.returnPolicy,
		ReturnState:           run.returnState,
		ReturnParticipants:    append([]uint32(nil), run.returnParticipants...),
		ReturnReason:          run.returnReason,
		ReturnUpdatedAtUnixMs: run.returnUpdatedAtMs,
	}
	if run.Plan.Mode == ModeSeparate {
		record.SeparateIndexes = make(map[uint32]int, len(run.sepIndex))
		for id, index := range run.sepIndex {
			record.SeparateIndexes[id] = index
		}
	}
	for id, arrived := range run.arrived {
		if arrived {
			record.Arrived = append(record.Arrived, id)
		}
	}
	sort.Slice(record.Arrived, func(i, j int) bool { return record.Arrived[i] < record.Arrived[j] })
	if run.recoveryRequired {
		for _, w := range run.recoveryWaits {
			record.Waits = append(record.Waits, DurableMissionWait{
				Scope: w.Scope, Index: w.Index, RemainingS: w.RemainingS, TotalS: w.TotalS,
			})
		}
	} else {
		now := e.clock()
		for _, scope := range sortedScopes(run.waits) {
			w := run.waits[scope]
			remaining := w.Deadline.Sub(now).Seconds()
			if remaining < 0 {
				remaining = 0
			}
			record.Waits = append(record.Waits, DurableMissionWait{
				Scope: scope, Index: w.Index, RemainingS: remaining, TotalS: w.TotalS,
			})
		}
	}
	for id, reason := range run.groupRejects {
		record.ParticipantRejections = append(record.ParticipantRejections, ParticipantRejection{
			RunID: run.RunID, Index: run.currentIndex, DroneID: id, Reason: reason,
		})
	}
	sort.Slice(record.ParticipantRejections, func(i, j int) bool {
		return record.ParticipantRejections[i].DroneID < record.ParticipantRejections[j].DroneID
	})
	return record, true
}

// RestoreDurableMission validates before installing any state. A non-terminal
// record is converted to command-inert INTERRUPTED recovery evidence.
func (e *Engine) RestoreDurableMission(record DurableMissionRecord) error {
	if err := ValidateDurableMissionRecord(record); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	plan := record.Plan.clone()
	run := &Run{
		RunID: record.RunID, Plan: plan, OperationID: record.OperationID,
		State: record.State, Revision: record.Revision,
		Participants:          append([]uint32(nil), record.Participants...),
		createdUnixMs:         record.CreatedAtUnixMs,
		originSessionID:       record.OriginSessionID,
		currentIndex:          record.CurrentIndex,
		arrived:               make(map[uint32]bool),
		authorityClaimedIndex: -1, authorityClaimedWaitIndex: -1,
		groupClaimedIndex: -1, lastPos: make(map[uint32][2]float64),
		lastPosAt: make(map[uint32]time.Time), groupRejects: make(map[uint32]string),
		sepIndex: make(map[uint32]int), sepClaimedIndex: make(map[uint32]int),
		waits:          make(map[uint32]*WaitState),
		lastTransition: record.LastTransition, terminalReason: record.TerminalReason,
		history:      []Transition{record.LastTransition},
		returnPolicy: record.ReturnPolicy, returnState: record.ReturnState,
		returnParticipants: append([]uint32(nil), record.ReturnParticipants...),
		returnReason:       record.ReturnReason, returnUpdatedAtMs: record.ReturnUpdatedAtUnixMs,
		currentLeaderID:           record.LeaderID,
		swarmOriginalParticipants: orderedParticipants(plan.Participants),
		activeParticipants:        append([]uint32(nil), record.ActiveParticipants...),
		excludedParticipants:      append([]uint32(nil), record.ExcludedParticipants...),
		swarmGeneration:           record.SwarmGeneration,
		successionPending:         record.SuccessionPending,
		successionReason:          record.SuccessionReason,
	}
	if plan.Mode == ModeSwarmLeader && len(run.activeParticipants) == 0 &&
		len(run.excludedParticipants) == 0 && record.SwarmGeneration == 0 {
		run.activeParticipants = append([]uint32(nil), run.swarmOriginalParticipants...)
	}
	for _, id := range record.Arrived {
		run.arrived[id] = true
	}
	for id, index := range record.SeparateIndexes {
		run.sepIndex[id] = index
		run.sepClaimedIndex[id] = -1
	}
	for _, rejection := range record.ParticipantRejections {
		run.groupRejects[rejection.DroneID] = rejection.Reason
	}
	for _, w := range record.Waits {
		run.recoveryWaits = append(run.recoveryWaits, WaitSnapshot{
			Scope: w.Scope, Index: w.Index, RemainingS: w.RemainingS, TotalS: w.TotalS,
		})
	}
	if !record.State.IsTerminal() || record.ReturnState.pendingAuthority() {
		run.recoveryRequired = true
		run.recoveryPreviousState = record.State
		run.recoveryReason = fmt.Sprintf(
			"Core restarted with unfinished mission/Return (previous mission=%s return=%s); operator acknowledgement required",
			record.State, record.ReturnState)
		run.waits = make(map[uint32]*WaitState)
		run.successionPending = false
		if record.ReturnState.pendingAuthority() {
			e.transitionReturnLocked(run, ReturnStateRecoveryRequired, run.recoveryReason)
		}
		e.transition(run, StateInterrupted, "core-restart-recovery", OwnerCoreEvent, run.recoveryReason)
	} else if record.RecoveryRequired {
		run.recoveryRequired = true
		run.recoveryPreviousState = record.RecoveryPreviousState
		run.recoveryReason = record.RecoveryReason
	}
	e.run = run
	e.recoveryIssue = nil
	if record.RunID > e.nextRunID {
		e.nextRunID = record.RunID
	}
	return nil
}

func (e *Engine) SetIncompatibleRecovery(runID uint64, schemaVersion uint32, reason string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if reason == "" {
		reason = "mission persistence is corrupt or incompatible"
	}
	e.run = nil
	e.recoveryIssue = &RecoveryIssue{RunID: runID, SchemaVersion: schemaVersion, Reason: reason}
	if runID > e.nextRunID {
		e.nextRunID = runID
	}
}

// ClearRecovery is the command-free mutation used by CancelMission for an
// explicit operator acknowledgement.
func (e *Engine) ClearRecovery(runID uint64) (cleared bool, incompatible bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.recoveryIssue != nil {
		if runID != e.recoveryIssue.RunID {
			return false, false
		}
		e.recoveryIssue = nil
		return true, true
	}
	run := e.run
	if run == nil || run.RunID != runID || !run.recoveryRequired {
		return false, false
	}
	run.recoveryRequired = false
	run.recoveryReason = ""
	run.recoveryWaits = nil
	if run.returnState == ReturnStateRecoveryRequired {
		e.transitionReturnLocked(run, ReturnStateSuppressed, "restart recovery acknowledged by operator")
	}
	e.transition(run, StateInterrupted, "recovery-cleared", OwnerOperator,
		"restart recovery acknowledged by operator")
	return true, false
}

// PersistenceFault terminally suppresses new progression without inventing an
// aircraft action. Existing emergency/failsafe ownership remains untouched.
func (e *Engine) PersistenceFault(runID, revision uint64, reason string) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	run := e.run
	if run == nil || run.RunID != runID || run.Revision != revision ||
		(run.State.IsTerminal() && !run.returnState.pendingAuthority()) {
		return false
	}
	if reason == "" {
		reason = "mission persistence update failed"
	}
	run.waits = make(map[uint32]*WaitState)
	if run.returnState.pendingAuthority() {
		e.transitionReturnLocked(run, ReturnStateFailed, reason)
	}
	e.transition(run, StateInterrupted, "persistence-fault", OwnerCoreEvent, reason)
	return true
}

func (e *Engine) RecoveryRequired() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.recoveryIssue != nil || (e.run != nil && e.run.recoveryRequired)
}
