package mission

import "fmt"

// State is the explicit mission state machine (contract B2).  Transitions:
//
//	IDLE → VALIDATING → READY → RUNNING → (WAITING ↔ RUNNING) → COMPLETED
//	                                    ↘ CANCELLING → CANCELLED
//	                                    ↘ INTERRUPTED   (failsafe / Core preempt)
//	                                    ↘ FAILED        (unrecoverable / safety reject)
//
// Every transition carries an owner + reason and bumps the run revision.  A Qt
// timer must never be a transition owner after cutover (V2 MUST-7 / F5); in the
// shadow this is enforced by construction — the engine only transitions from
// Start/Observe/Poll/Cancel/Interrupt calls, never from a wall-clock timer of
// its own.
type State int

const (
	StateIdle State = iota
	StateValidating
	StateReady
	StateRunning
	StateWaiting
	StateCancelling
	StateCancelled
	StateInterrupted
	StateFailed
	StateCompleted
)

func (s State) String() string {
	switch s {
	case StateIdle:
		return "IDLE"
	case StateValidating:
		return "VALIDATING"
	case StateReady:
		return "READY"
	case StateRunning:
		return "RUNNING"
	case StateWaiting:
		return "WAITING"
	case StateCancelling:
		return "CANCELLING"
	case StateCancelled:
		return "CANCELLED"
	case StateInterrupted:
		return "INTERRUPTED"
	case StateFailed:
		return "FAILED"
	case StateCompleted:
		return "COMPLETED"
	default:
		return fmt.Sprintf("State(%d)", int(s))
	}
}

// IsTerminal reports whether no further transitions are expected.  A terminal
// mission never auto-resumes (V2 MUST-8) — the operator must start a new run.
func (s State) IsTerminal() bool {
	switch s {
	case StateCancelled, StateInterrupted, StateFailed, StateCompleted:
		return true
	default:
		return false
	}
}

// IsActive reports whether the mission is progressing or holding (RUNNING/WAITING).
func (s State) IsActive() bool {
	return s == StateRunning || s == StateWaiting
}

// Owner records who caused a transition (contract B2: every transition has an owner).
type Owner int

const (
	OwnerSystem    Owner = iota // engine-internal (start/advance/complete)
	OwnerOperator               // operator intent (start/cancel)
	OwnerTelemetry              // arrival judged from telemetry observation
	OwnerCoreEvent              // a Core event drove the transition
	OwnerFailsafe               // battery/link failsafe preemption
)

func (o Owner) String() string {
	switch o {
	case OwnerSystem:
		return "system"
	case OwnerOperator:
		return "operator"
	case OwnerTelemetry:
		return "telemetry"
	case OwnerCoreEvent:
		return "core-event"
	case OwnerFailsafe:
		return "failsafe"
	default:
		return fmt.Sprintf("Owner(%d)", int(o))
	}
}

// Transition is one recorded state change.  Revision is the run revision after
// the change; UI can use it to detect it is looking at the latest state (B3).
type Transition struct {
	From     State
	To       State
	Event    string
	Owner    Owner
	Reason   string
	Revision uint64
	AtUnixMs int64
}

func (t Transition) String() string {
	return fmt.Sprintf("[rev %d] %s→%s (%s by %s: %s)",
		t.Revision, t.From, t.To, t.Event, t.Owner, t.Reason)
}
