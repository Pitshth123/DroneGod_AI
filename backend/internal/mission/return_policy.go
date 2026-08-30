package mission

import (
	"errors"
	"fmt"
)

// ReturnPolicy is normal post-mission navigation ownership. It is not an
// emergency, failsafe, operator takeover, or restart-resumable action.
type ReturnPolicy int

const (
	ReturnNone ReturnPolicy = iota
	ReturnRTLAllAfterMission
	ReturnSwarm
	ReturnWaveManaged
)

func (p ReturnPolicy) String() string {
	switch p {
	case ReturnNone:
		return "NONE"
	case ReturnRTLAllAfterMission:
		return "RTL_ALL_AFTER_MISSION"
	case ReturnSwarm:
		return "SWARM_RETURN"
	case ReturnWaveManaged:
		return "WAVE_MANAGED_RETURN"
	default:
		return fmt.Sprintf("ReturnPolicy(%d)", int(p))
	}
}

func (p ReturnPolicy) valid() bool { return p >= ReturnNone && p <= ReturnWaveManaged }

type SeparateReturnTiming int

const (
	SeparateReturnInvalid              SeparateReturnTiming = -1
	SeparateReturnAllOnMissionComplete SeparateReturnTiming = 0
	SeparateReturnEachOnRouteComplete  SeparateReturnTiming = 1
)

func (p SeparateReturnTiming) String() string {
	switch p {
	case SeparateReturnAllOnMissionComplete:
		return "RETURN_ALL_ON_MISSION_COMPLETE"
	case SeparateReturnEachOnRouteComplete:
		return "RETURN_EACH_ON_ROUTE_COMPLETE"
	default:
		return fmt.Sprintf("SeparateReturnTiming(%d)", int(p))
	}
}

func (p SeparateReturnTiming) valid() bool {
	return p == SeparateReturnAllOnMissionComplete || p == SeparateReturnEachOnRouteComplete
}

type ReturnState int

const (
	ReturnStateInactive ReturnState = iota
	ReturnStatePending
	ReturnStateReturning
	ReturnStateCompleted
	ReturnStateSuppressed
	ReturnStateFailed
	ReturnStateRecoveryRequired
)

func (s ReturnState) String() string {
	switch s {
	case ReturnStateInactive:
		return "INACTIVE"
	case ReturnStatePending:
		return "RETURN_PENDING"
	case ReturnStateReturning:
		return "RETURNING"
	case ReturnStateCompleted:
		return "RETURN_COMPLETED"
	case ReturnStateSuppressed:
		return "RETURN_SUPPRESSED"
	case ReturnStateFailed:
		return "RETURN_FAILED"
	case ReturnStateRecoveryRequired:
		return "RETURN_RECOVERY_REQUIRED"
	default:
		return fmt.Sprintf("ReturnState(%d)", int(s))
	}
}

func (s ReturnState) valid() bool {
	return s >= ReturnStateInactive && s <= ReturnStateRecoveryRequired
}
func (s ReturnState) pendingAuthority() bool {
	return s == ReturnStatePending || s == ReturnStateReturning
}

var (
	ErrReturnPolicyConflict = errors.New("mission: return policy conflicts with mission mode/rtl_after")
	ErrSeparateEarlyReturn  = errors.New("mission: per-route early Return is disabled pending return-corridor safety contract")
)

// resolveReturnPolicy converts the compatibility bool into the explicit domain
// policy and rejects ambiguous/conflicting explicit inputs. The returned plan is
// the immutable representation stored on a run and in durable evidence.
func (p MissionPlan) resolveReturnPolicy() (MissionPlan, error) {
	if !p.ReturnPolicy.valid() {
		return MissionPlan{}, fmt.Errorf("%w: unknown return policy %d", ErrReturnPolicyConflict, p.ReturnPolicy)
	}
	compat := ReturnNone
	if p.RtlAfter {
		switch p.Mode {
		case ModeGrouped, ModeSeparate:
			compat = ReturnRTLAllAfterMission
		case ModeSwarmLeader:
			compat = ReturnSwarm
		default:
			return MissionPlan{}, fmt.Errorf("%w: unknown mission mode %d", ErrReturnPolicyConflict, p.Mode)
		}
	}
	if p.ReturnPolicyExplicit {
		if p.RtlAfter && p.ReturnPolicy != compat {
			return MissionPlan{}, fmt.Errorf("%w: rtl_after maps to %s, explicit policy is %s",
				ErrReturnPolicyConflict, compat, p.ReturnPolicy)
		}
	} else {
		p.ReturnPolicy = compat
	}
	switch p.Mode {
	case ModeGrouped:
		if p.ReturnPolicy != ReturnNone && p.ReturnPolicy != ReturnRTLAllAfterMission {
			return MissionPlan{}, fmt.Errorf("%w: GROUPED cannot use %s", ErrReturnPolicyConflict, p.ReturnPolicy)
		}
	case ModeSeparate:
		if !p.SeparateReturnTiming.valid() {
			return MissionPlan{}, fmt.Errorf("%w: unknown SEPARATE return timing %d",
				ErrReturnPolicyConflict, p.SeparateReturnTiming)
		}
		if p.ReturnPolicy != ReturnNone && p.ReturnPolicy != ReturnRTLAllAfterMission {
			return MissionPlan{}, fmt.Errorf("%w: SEPARATE cannot use %s", ErrReturnPolicyConflict, p.ReturnPolicy)
		}
		if p.SeparateReturnTiming == SeparateReturnEachOnRouteComplete {
			return MissionPlan{}, ErrSeparateEarlyReturn
		}
	case ModeSwarmLeader:
		if p.ReturnPolicy != ReturnNone && p.ReturnPolicy != ReturnSwarm {
			return MissionPlan{}, fmt.Errorf("%w: SWARM_LEADER cannot use %s", ErrReturnPolicyConflict, p.ReturnPolicy)
		}
	}
	p.ReturnPolicyExplicit = true
	if p.Mode == ModeSeparate {
		p.SeparateReturnTiming = SeparateReturnAllOnMissionComplete
	}
	return p, nil
}

// ResolveWaveReturnPolicy documents the single WAVE-owned return lifecycle.
// rtl_after never creates an additional generic RTL sequence.
func ResolveWaveReturnPolicy(_ bool) ReturnPolicy { return ReturnWaveManaged }

type ReturnIntent struct {
	RunID        uint64
	OperationID  string
	Policy       ReturnPolicy
	Participants []uint32
	RequestID    string
}
