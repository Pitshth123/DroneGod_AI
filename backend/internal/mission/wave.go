package mission

import (
	"errors"
	"sort"
	"sync"
	"time"
)

// V3-S09-D — WAVE Core state model (PRE-FLIP, SHADOW ONLY).
//
// Every async transition uses a monotonically allocated token.  The token changes
// at Start and at every group/phase transition, so callbacks from an older run,
// group, or earlier occurrence of the same phase can never affect current state.
type WavePhase int

const (
	WavePhaseTakeoff WavePhase = iota
	WavePhaseRoute
	WavePhaseWaitingLand
	WavePhaseLanded
)

func (p WavePhase) String() string {
	switch p {
	case WavePhaseTakeoff:
		return "takeoff"
	case WavePhaseRoute:
		return "route"
	case WavePhaseWaitingLand:
		return "waiting_land"
	case WavePhaseLanded:
		return "landed"
	default:
		return "wave_phase?"
	}
}

type WaveState int

const (
	WaveRunning WaveState = iota
	WaveCompleted
	WaveCancelled
	WaveInterrupted
	WaveTimedOut
)

func (s WaveState) String() string {
	switch s {
	case WaveRunning:
		return "RUNNING"
	case WaveCompleted:
		return "COMPLETED"
	case WaveCancelled:
		return "CANCELLED"
	case WaveInterrupted:
		return "INTERRUPTED"
	case WaveTimedOut:
		return "TIMED_OUT"
	default:
		return "WAVE_STATE?"
	}
}

func (s WaveState) IsTerminal() bool { return s != WaveRunning }

type WaveGroup struct {
	ID      int
	Members []uint32
}

const DefaultWaveTimeout = 5 * time.Minute

var (
	ErrWaveNeedsTwoGroups = errors.New("wave: at least 2 groups required")
	ErrWaveEmptyGroup     = errors.New("wave: a group has no members")
	ErrWaveActive         = errors.New("wave: a wave is already active")
)

type waveRun struct {
	groups         []WaveGroup
	index          int
	phase          WavePhase
	state          WaveState
	token          uint64
	groupStartedAt time.Time
	autoNext       bool
	terminalReason string
	returnPolicy   ReturnPolicy
}

type WaveEngine struct {
	clock     Clock
	mu        sync.Mutex
	run       *waveRun
	timeout   time.Duration
	nextToken uint64
}

func NewWaveEngine(clock Clock) *WaveEngine {
	if clock == nil {
		clock = time.Now
	}
	return &WaveEngine{clock: clock, timeout: DefaultWaveTimeout}
}

func (w *WaveEngine) SetTimeout(d time.Duration) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if d > 0 {
		w.timeout = d
	}
}

func (w *WaveEngine) allocateTokenLocked() uint64 {
	w.nextToken++
	if w.nextToken == 0 {
		w.nextToken++
	}
	return w.nextToken
}

func (w *WaveEngine) Start(groups []WaveGroup, autoNext bool) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.run != nil && !w.run.state.IsTerminal() {
		return ErrWaveActive
	}
	if len(groups) < 2 {
		return ErrWaveNeedsTwoGroups
	}
	cp := make([]WaveGroup, len(groups))
	for i, g := range groups {
		if len(g.Members) == 0 {
			return ErrWaveEmptyGroup
		}
		cp[i] = WaveGroup{ID: g.ID, Members: append([]uint32(nil), g.Members...)}
	}
	sort.Slice(cp, func(i, j int) bool { return cp[i].ID < cp[j].ID })
	w.run = &waveRun{
		groups: cp, index: 0, phase: WavePhaseTakeoff, state: WaveRunning,
		token: w.allocateTokenLocked(), groupStartedAt: w.clock(), autoNext: autoNext,
		returnPolicy: ResolveWaveReturnPolicy(false),
	}
	return nil
}

func (w *WaveEngine) Token() uint64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.run == nil {
		return 0
	}
	return w.run.token
}

func (w *WaveEngine) TakeoffComplete(token uint64) bool {
	return w.phaseStep(token, WavePhaseTakeoff, WavePhaseRoute)
}

func (w *WaveEngine) RouteComplete(token uint64) bool {
	return w.phaseStep(token, WavePhaseRoute, WavePhaseWaitingLand)
}

func (w *WaveEngine) LandedDisarmed(token uint64) bool {
	return w.phaseStep(token, WavePhaseWaitingLand, WavePhaseLanded)
}

func (w *WaveEngine) phaseStep(token uint64, from, to WavePhase) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.run
	if r == nil || r.state != WaveRunning || r.token != token || r.phase != from {
		return false
	}
	r.phase = to
	r.token = w.allocateTokenLocked()
	r.groupStartedAt = w.clock()
	return true
}

func (w *WaveEngine) StartNextGroup(token uint64) bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.run
	if r == nil || r.state != WaveRunning || r.token != token || r.phase != WavePhaseLanded {
		return false
	}
	r.index++
	r.token = w.allocateTokenLocked()
	if r.index >= len(r.groups) {
		r.state = WaveCompleted
		r.terminalReason = "all groups complete"
		return true
	}
	r.phase = WavePhaseTakeoff
	r.groupStartedAt = w.clock()
	return true
}

func (w *WaveEngine) Poll() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.run
	if r == nil || r.state != WaveRunning {
		return false
	}
	if w.clock().Sub(r.groupStartedAt) > w.timeout {
		r.state = WaveTimedOut
		r.terminalReason = "wave phase timeout"
		r.token = w.allocateTokenLocked()
		return true
	}
	return false
}

func (w *WaveEngine) Cancel() { w.terminate(WaveCancelled, "operator cancel") }

func (w *WaveEngine) Interrupt(reason string) {
	if reason == "" {
		reason = "failsafe"
	}
	w.terminate(WaveInterrupted, reason)
}

func (w *WaveEngine) terminate(state WaveState, reason string) {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.run
	if r == nil || r.state.IsTerminal() {
		return
	}
	r.state = state
	r.terminalReason = reason
	r.token = w.allocateTokenLocked()
}

type WaveSnapshot struct {
	Active         bool
	State          WaveState
	Phase          WavePhase
	GroupIndex     int
	GroupID        int
	TotalGroups    int
	Gen            uint64
	AutoNext       bool
	TerminalReason string
	ReturnPolicy   ReturnPolicy
}

func (w *WaveEngine) Snapshot() WaveSnapshot {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.run
	if r == nil {
		return WaveSnapshot{Active: false, State: WaveCompleted, GroupIndex: -1}
	}
	snap := WaveSnapshot{
		Active: !r.state.IsTerminal(), State: r.state, Phase: r.phase,
		GroupIndex: r.index, TotalGroups: len(r.groups), Gen: r.token,
		AutoNext: r.autoNext, TerminalReason: r.terminalReason,
		ReturnPolicy: r.returnPolicy,
	}
	if r.index >= 0 && r.index < len(r.groups) {
		snap.GroupID = r.groups[r.index].ID
	}
	return snap
}
