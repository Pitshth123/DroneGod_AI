package mission

import (
	"errors"
	"sort"
	"sync"
	"time"
)

// V3-S09-E — waypoint payload action model (PRE-FLIP, SHADOW ONLY).
//
// Legacy _wp_run_action.finish() launches servo_release for every participant
// before checking whether the mission generation may advance. Core therefore
// keeps a conservative release-all cleanup contract once the action sequence has
// entered HOLD/servo handling: every participant is a cleanup target exactly once.
// This deliberately covers lost/uncertain SET results so a payload cannot remain
// engaged merely because its ACK/result never arrived. Cancellation/failsafe
// blocks new SET and progression while cleanup RELEASE remains valid.
type ActionPhase int

const (
	ActionHold ActionPhase = iota
	ActionServo
	ActionDwell
	ActionRelease
	ActionDone
)

func (p ActionPhase) String() string {
	switch p {
	case ActionHold:
		return "hold"
	case ActionServo:
		return "servo"
	case ActionDwell:
		return "dwell"
	case ActionRelease:
		return "release"
	case ActionDone:
		return "done"
	default:
		return "action_phase?"
	}
}

const DefaultActionDwell = 2 * time.Second

var ErrActionActive = errors.New("payload action: an action is already active")

type actionRun struct {
	ids             []uint32
	channel         int
	pwm             int
	gen             uint64
	phase           ActionPhase
	attempted       map[uint32]bool
	set             map[uint32]bool
	releaseRequired map[uint32]bool
	released        map[uint32]bool
	failure         string
	cleanupFailure  string
	dwellUntil      time.Time
	cancelled       bool
	reason          string
}

type ActionEngine struct {
	clock   Clock
	mu      sync.Mutex
	run     *actionRun
	dwell   time.Duration
	nextGen uint64
}

func NewActionEngine(clock Clock) *ActionEngine {
	if clock == nil {
		clock = time.Now
	}
	return &ActionEngine{clock: clock, dwell: DefaultActionDwell}
}

func (a *ActionEngine) SetDwell(d time.Duration) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if d > 0 {
		a.dwell = d
	}
}

func (a *ActionEngine) Start(ids []uint32, channel, pwm int) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.run != nil && a.run.phase != ActionDone {
		return ErrActionActive
	}
	a.nextGen++
	if a.nextGen == 0 {
		a.nextGen++
	}
	a.run = &actionRun{
		ids: canonicalParticipants(ids), channel: channel, pwm: pwm,
		gen: a.nextGen, phase: ActionHold,
		attempted: make(map[uint32]bool), set: make(map[uint32]bool),
		releaseRequired: make(map[uint32]bool), released: make(map[uint32]bool),
	}
	return nil
}

func (a *ActionEngine) Token() uint64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.run == nil {
		return 0
	}
	return a.run.gen
}

func (a *ActionEngine) HoldResult(token uint64, ok bool) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil || r.cancelled || r.gen != token || r.phase != ActionHold {
		return false
	}
	// Legacy finish() releases every participant on any error after the action
	// sequence starts. Pre-register all cleanup targets so an uncertain/lost SET
	// result can never remove the corresponding release obligation.
	for _, id := range r.ids {
		r.releaseRequired[id] = true
	}
	if !ok {
		r.failure = "HOLD rejected"
		r.phase = ActionRelease
		return true
	}
	r.phase = ActionServo
	return true
}

func (a *ActionEngine) ServoSetResult(token uint64, droneID uint32, ok bool) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil || r.cancelled || r.gen != token || r.phase != ActionServo {
		return false
	}
	if !r.isMember(droneID) || r.attempted[droneID] {
		return false
	}
	r.attempted[droneID] = true
	if ok {
		r.set[droneID] = true
	} else {
		if r.failure == "" {
			r.failure = "servo set rejected"
		}
		r.phase = ActionRelease
		return true
	}
	if len(r.attempted) == len(r.ids) {
		r.phase = ActionDwell
		r.dwellUntil = a.clock().Add(a.dwell)
	}
	return true
}

func (a *ActionEngine) Poll() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil || r.phase != ActionDwell {
		return false
	}
	if !a.clock().Before(r.dwellUntil) {
		r.phase = ActionRelease
		return true
	}
	return false
}

// ReleaseResult records cleanup exactly once.  ok is optional for compatibility
// with the original shadow API; false records an operator-visible cleanup failure.
func (a *ActionEngine) ReleaseResult(token uint64, droneID uint32, okResult ...bool) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil || r.gen != token || r.phase != ActionRelease {
		return false
	}
	if !r.releaseRequired[droneID] || r.released[droneID] {
		return false
	}
	ok := true
	if len(okResult) > 0 {
		ok = okResult[0]
	}
	r.released[droneID] = true
	if !ok && r.cleanupFailure == "" {
		r.cleanupFailure = "servo release rejected"
	}
	if len(r.released) == len(r.releaseRequired) {
		r.phase = ActionDone
	}
	return true
}

func (a *ActionEngine) Cancel() { a.terminate("operator cancel") }

func (a *ActionEngine) Interrupt(reason string) {
	if reason == "" {
		reason = "failsafe"
	}
	a.terminate(reason)
}

func (a *ActionEngine) terminate(reason string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil || r.phase == ActionDone || r.cancelled {
		return
	}
	r.cancelled = true
	r.reason = reason
	if len(r.releaseRequired) == len(r.released) {
		r.phase = ActionDone
	} else {
		r.phase = ActionRelease
	}
}

func (a *ActionEngine) ShouldAdvance() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.run != nil && a.run.phase == ActionDone && !a.run.cancelled
}

func (r *actionRun) isMember(id uint32) bool {
	for _, m := range r.ids {
		if m == id {
			return true
		}
	}
	return false
}

type ActionSnapshot struct {
	Phase          ActionPhase
	Channel        int
	Token          uint64
	SetCount       int
	RelCount       int
	Failed         bool
	Failure        string
	CleanupFailed  bool
	CleanupFailure string
	Cancelled      bool
	Reason         string
	SetDrones      []uint32
	ReleaseDrones  []uint32
}

func (a *ActionEngine) Snapshot() ActionSnapshot {
	a.mu.Lock()
	defer a.mu.Unlock()
	r := a.run
	if r == nil {
		return ActionSnapshot{Phase: ActionDone}
	}
	snap := ActionSnapshot{
		Phase: r.phase, Channel: r.channel, Token: r.gen,
		SetCount: len(r.set), RelCount: len(r.released),
		Failed: r.failure != "", Failure: r.failure,
		CleanupFailed: r.cleanupFailure != "", CleanupFailure: r.cleanupFailure,
		Cancelled: r.cancelled, Reason: r.reason,
	}
	for id := range r.set {
		snap.SetDrones = append(snap.SetDrones, id)
	}
	for id := range r.releaseRequired {
		snap.ReleaseDrones = append(snap.ReleaseDrones, id)
	}
	sort.Slice(snap.SetDrones, func(i, j int) bool { return snap.SetDrones[i] < snap.SetDrones[j] })
	sort.Slice(snap.ReleaseDrones, func(i, j int) bool { return snap.ReleaseDrones[i] < snap.ReleaseDrones[j] })
	return snap
}
