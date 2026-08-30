package mission

import (
	"errors"
	"go/parser"
	"go/token"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeClock is an injectable, deterministic clock (no real waiting).
type fakeClock struct{ t time.Time }

func newClock() *fakeClock { return &fakeClock{t: time.Unix(1_700_000_000, 0)} }
func (c *fakeClock) now() time.Time         { return c.t }
func (c *fakeClock) advance(d time.Duration) { c.t = c.t.Add(d) }

// two well-separated waypoints (~111 m apart) so exact-position observations
// unambiguously hit one target at a time.
var (
	wpA = Waypoint{Seq: 0, Lat: 14.000000, Lon: 100.000000}
	wpB = Waypoint{Seq: 1, Lat: 14.001000, Lon: 100.000000}
)

func groupedPlan(points []Waypoint, participants ...uint32) MissionPlan {
	return MissionPlan{
		PlanID: "p1", Mode: ModeGrouped, Participants: participants,
		Routes: []Route{{DroneID: 0, Points: points}},
	}
}

func mustStart(t *testing.T, e *Engine, p MissionPlan) uint64 {
	t.Helper()
	id, err := e.Start(p)
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	return id
}

type commandedGoto struct {
	id       uint32
	lat, lon float64
	alt      float64
}

type fakeCommander struct {
	calls     []commandedGoto
	holdCalls []uint32
	err       error
	holdErr   error
}

func (f *fakeCommander) Goto(id uint32, lat, lon, alt float64) error {
	f.calls = append(f.calls, commandedGoto{id: id, lat: lat, lon: lon, alt: alt})
	return f.err
}

func (f *fakeCommander) Hold(id uint32) error {
	f.holdCalls = append(f.holdCalls, id)
	return f.holdErr
}

// ---- lifecycle ------------------------------------------------------------

func TestStartRunsAndSnapshots(t *testing.T) {
	e := NewEngine(newClock().now)
	id := mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1, 2))
	if id == 0 {
		t.Fatal("run id must be non-zero")
	}
	s := e.Snapshot()
	if !s.Active || s.State != StateRunning || s.RunID != id {
		t.Fatalf("snapshot after start: %+v", s)
	}
	if s.CurrentIndex != 0 {
		t.Fatalf("current index = %d, want 0", s.CurrentIndex)
	}
}

func TestStartRejectsInvalidPlan(t *testing.T) {
	e := NewEngine(nil)
	if _, err := e.Start(MissionPlan{PlanID: ""}); err == nil {
		t.Fatal("invalid plan should be rejected")
	}
	if e.Snapshot().Active {
		t.Fatal("no run should exist after rejected start")
	}
}

func TestStartSamePlanIdempotent(t *testing.T) {
	e := NewEngine(nil)
	p := groupedPlan([]Waypoint{wpA, wpB}, 1)
	id1 := mustStart(t, e, p)
	id2, err := e.Start(p) // same PlanID while active → same run
	if err != nil || id1 != id2 {
		t.Fatalf("re-start same plan: id1=%d id2=%d err=%v", id1, id2, err)
	}
}

func TestStartSecondDifferentPlanRejected(t *testing.T) {
	e := NewEngine(nil)
	mustStart(t, e, groupedPlan([]Waypoint{wpA}, 1))
	other := groupedPlan([]Waypoint{wpB}, 1)
	other.PlanID = "p2"
	if _, err := e.Start(other); !errors.Is(err, ErrActiveRun) {
		t.Fatalf("second plan while active should reject with ErrActiveRun, got %v", err)
	}
}

func TestAuthorityDispatchesInitialAndNextGotoExactlyOnce(t *testing.T) {
	e := NewEngine(nil)
	e.EnableAuthority()
	p := groupedPlan([]Waypoint{
		{Seq: 0, Lat: 14.0, Lon: 100.0, Alt: 20},
		{Seq: 1, Lat: 14.001, Lon: 100.0, Alt: 21},
	}, 7)
	p.ParticipantAltitudes = map[uint32]float64{7: 27.5}
	id, err := e.StartOp(p, "authority-op")
	if err != nil {
		t.Fatal(err)
	}
	first, ok, err := e.ClaimAuthorityGoto()
	if err != nil || !ok || first.DroneID != 7 || first.Lat != 14.0 || first.Alt != 27.5 {
		t.Fatalf("initial authority intent = %+v ok=%v err=%v", first, ok, err)
	}
	if gotID, err := e.StartOp(p, "authority-op"); err != nil || gotID != id {
		t.Fatalf("duplicate authority Start: id=%d err=%v", gotID, err)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("duplicate Start must not duplicate GOTO intent: ok=%v err=%v", ok, err)
	}
	e.Observe(7, 14.0, 100.0, 27.5)
	second, ok, err := e.ClaimAuthorityGoto()
	if err != nil || !ok || second.Index != 1 || second.Lat != 14.001 || second.Alt != 27.5 {
		t.Fatalf("next authority intent = %+v ok=%v err=%v", second, ok, err)
	}
}

func TestAuthorityRejectFailsMissionAndNeverRetries(t *testing.T) {
	e := NewEngine(nil)
	e.EnableAuthority()
	p := groupedPlan([]Waypoint{{Seq: 0, Lat: 14, Lon: 100, Alt: 20}}, 1)
	id, err := e.StartOp(p, "reject-op")
	if err != nil || id == 0 {
		t.Fatalf("run start: id=%d err=%v", id, err)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || !ok {
		t.Fatalf("expected one GOTO intent before simulated reject: ok=%v err=%v", ok, err)
	}
	e.Fail(id, "safety rejected")
	s := e.Snapshot()
	if s.State != StateFailed || s.Active {
		t.Fatalf("command reject must terminally FAIL mission: %+v", s)
	}
	e.Observe(1, 14, 100, 20)
	e.Poll()
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok || e.Snapshot().State != StateFailed {
		t.Fatalf("failed authority mission must never emit another command: ok=%v err=%v", ok, err)
	}
}

func TestAuthorityRefusesIneligiblePlanBeforeCommand(t *testing.T) {
	e := NewEngine(nil)
	e.EnableAuthority()
	p := groupedPlan([]Waypoint{wpA}, 1, 2)
	if _, err := e.StartOp(p, "bad-authority"); !errors.Is(err, ErrAuthorityUnsupported) {
		t.Fatalf("want ErrAuthorityUnsupported, got %v", err)
	}
	if e.Snapshot().Active {
		t.Fatal("ineligible authority plan must create no run")
	}
}

func TestStartOpIdempotentByOperationID(t *testing.T) {
	e := NewEngine(nil)
	// Two different plan_ids but the SAME operation_id (a retried Start) must not
	// create a second run — the operation is the idempotency key.
	p1 := groupedPlan([]Waypoint{wpA, wpB}, 1)
	id1, err := e.StartOp(p1, "op-1")
	if err != nil {
		t.Fatal(err)
	}
	p2 := groupedPlan([]Waypoint{wpA, wpB}, 1)
	p2.PlanID = "different"
	id2, err := e.StartOp(p2, "op-1")
	if err != nil || id1 != id2 {
		t.Fatalf("same operation_id must return same run: id1=%d id2=%d err=%v", id1, id2, err)
	}
	// A different operation_id with a different plan while active is rejected.
	if _, err := e.StartOp(p2, "op-2"); !errors.Is(err, ErrActiveRun) {
		t.Fatalf("different op+plan while active should reject, got %v", err)
	}
}

// ---- GROUPED progression --------------------------------------------------

func TestGroupedAdvancesOnlyWhenAllArrive(t *testing.T) {
	e := NewEngine(newClock().now)
	mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1, 2))

	e.Observe(1, wpA.Lat, wpA.Lon, 0) // only D1 arrives at WP0
	if s := e.Snapshot(); s.CurrentIndex != 0 || s.State != StateRunning {
		t.Fatalf("should still be at WP0 waiting for D2: %+v", s)
	}
	e.Observe(2, wpA.Lat, wpA.Lon, 0) // D2 arrives → advance to WP1
	if s := e.Snapshot(); s.CurrentIndex != 1 || s.State != StateRunning {
		t.Fatalf("should have advanced to WP1: %+v", s)
	}
	e.Observe(1, wpB.Lat, wpB.Lon, 0)
	e.Observe(2, wpB.Lat, wpB.Lon, 0) // all arrive at final → COMPLETED
	if s := e.Snapshot(); s.State != StateCompleted || s.Active {
		t.Fatalf("should be COMPLETED: %+v", s)
	}
}

func TestGroupedFarPositionDoesNotArrive(t *testing.T) {
	e := NewEngine(nil)
	mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1))
	e.Observe(1, wpB.Lat, wpB.Lon, 0) // far from WP0 target
	if s := e.Snapshot(); s.CurrentIndex != 0 || s.State != StateRunning {
		t.Fatalf("far position must not count as arrival: %+v", s)
	}
}

// ---- WAIT -----------------------------------------------------------------

func TestGroupedWaitHoldsThenAdvances(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	wpWait := wpA
	wpWait.WaitSeconds = 60
	mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpB}, 1))

	e.Observe(1, wpWait.Lat, wpWait.Lon, 0) // arrive → WAITING
	s := e.Snapshot()
	if s.State != StateWaiting || len(s.Waits) != 1 {
		t.Fatalf("should be WAITING with one wait: %+v", s)
	}
	if s.Waits[0].RemainingS < 59 || s.Waits[0].RemainingS > 60 {
		t.Fatalf("remaining = %v, want ~60", s.Waits[0].RemainingS)
	}

	clk.advance(30 * time.Second)
	if e.Poll() {
		t.Fatal("Poll before deadline should not advance")
	}
	if e.Snapshot().State != StateWaiting {
		t.Fatal("still WAITING before deadline")
	}

	clk.advance(31 * time.Second) // now past 60s deadline
	if !e.Poll() {
		t.Fatal("Poll after deadline should advance")
	}
	if s := e.Snapshot(); s.State != StateRunning || s.CurrentIndex != 1 || len(s.Waits) != 0 {
		t.Fatalf("after WAIT should run to WP1: %+v", s)
	}
}

func TestAuthorityWaitSendsHoldThenNextGoto(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.EnableWaitAuthority()
	wpWait := Waypoint{Seq: 0, Lat: 14.0, Lon: 100.0, Alt: 20, WaitSeconds: 30}
	wpNext := Waypoint{Seq: 1, Lat: 14.001, Lon: 100.0, Alt: 20}
	mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpNext}, 1))
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || !ok {
		t.Fatalf("start should expose WP0 GOTO: ok=%v err=%v", ok, err)
	}
	e.Observe(1, wpWait.Lat, wpWait.Lon, 20)
	if s := e.Snapshot(); s.State != StateWaiting || len(s.Waits) != 1 {
		t.Fatalf("authority arrival should enter WAITING: %+v", s)
	}
	hold, ok, err := e.ClaimAuthorityHold()
	if err != nil || !ok || hold.DroneID != 1 || hold.Index != 0 {
		t.Fatalf("WAIT must expose one HOLD intent: %+v ok=%v err=%v", hold, ok, err)
	}
	if _, ok, err := e.ClaimAuthorityHold(); err != nil || ok {
		t.Fatalf("same WAIT must not expose duplicate HOLD: ok=%v err=%v", ok, err)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("WAIT entry must not expose next GOTO early: ok=%v err=%v", ok, err)
	}
	clk.advance(31 * time.Second)
	if !e.Poll() {
		t.Fatal("WAIT deadline should advance")
	}
	next, ok, err := e.ClaimAuthorityGoto()
	if err != nil || !ok || next.Index != 1 {
		t.Fatalf("WAIT completion must expose next GOTO once: %+v ok=%v err=%v", next, ok, err)
	}
	if s := e.Snapshot(); s.State != StateRunning || s.CurrentIndex != 1 {
		t.Fatalf("after WAIT should run WP1: %+v", s)
	}
}

func TestAuthorityWaitHoldRejectFailsClosed(t *testing.T) {
	e := NewEngine(nil)
	e.EnableWaitAuthority()
	wpWait := Waypoint{Seq: 0, Lat: 14, Lon: 100, Alt: 20, WaitSeconds: 30}
	runID := mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpB}, 1))
	_, _, _ = e.ClaimAuthorityGoto()
	e.Observe(1, wpWait.Lat, wpWait.Lon, 20)
	if _, ok, err := e.ClaimAuthorityHold(); err != nil || !ok {
		t.Fatalf("expected HOLD intent before simulated reject: ok=%v err=%v", ok, err)
	}
	e.Fail(runID, "hold safety rejected")
	if s := e.Snapshot(); s.State != StateFailed || len(s.Waits) != 0 {
		t.Fatalf("HOLD reject must fail closed and clear WAIT: %+v", s)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("failed WAIT must not expose GOTO: ok=%v err=%v", ok, err)
	}
}

func TestAuthorityCancelDuringWaitPreventsPostCancelGoto(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.EnableWaitAuthority()
	wpWait := Waypoint{Seq: 0, Lat: 14, Lon: 100, Alt: 20, WaitSeconds: 30}
	runID := mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpB}, 1))
	_, _, _ = e.ClaimAuthorityGoto()
	e.Observe(1, wpWait.Lat, wpWait.Lon, 20)
	_ = e.Cancel(runID)
	clk.advance(time.Minute)
	e.Poll()
	if _, ok, err := e.ClaimAuthorityHold(); err != nil || ok {
		t.Fatalf("cancel during WAIT must suppress late HOLD: ok=%v err=%v", ok, err)
	}
	if _, ok, err := e.ClaimAuthorityGoto(); err != nil || ok || e.Snapshot().State != StateCancelled {
		t.Fatalf("cancel during WAIT must suppress later GOTO: ok=%v err=%v state=%s", ok, err, e.Snapshot().State)
	}
}

func TestObserveIgnoredDuringWait(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	wpWait := wpA
	wpWait.WaitSeconds = 60
	mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpB}, 1))
	e.Observe(1, wpWait.Lat, wpWait.Lon, 0) // WAITING
	// arriving at the *next* target during WAIT must not advance — only the
	// deadline (Poll) may (contract B2: no timer/observation shortcut past WAIT).
	e.Observe(1, wpB.Lat, wpB.Lon, 0)
	if s := e.Snapshot(); s.State != StateWaiting || s.CurrentIndex != 0 {
		t.Fatalf("observation during WAIT must not advance: %+v", s)
	}
}

// ---- SEPARATE -------------------------------------------------------------

func separatePlan() MissionPlan {
	return MissionPlan{
		PlanID: "sep", Mode: ModeSeparate, Participants: []uint32{1, 2},
		Routes: []Route{
			{DroneID: 1, Points: []Waypoint{wpA, wpB}},
			{DroneID: 2, Points: []Waypoint{wpA, wpB}},
		},
	}
}

func TestSeparateDronesAdvanceIndependently(t *testing.T) {
	e := NewEngine(nil)
	mustStart(t, e, separatePlan())
	e.Observe(1, wpA.Lat, wpA.Lon, 0) // D1 → WP1, D2 still WP0
	s := e.Snapshot()
	if s.SepIndex[1] != 1 || s.SepIndex[2] != 0 {
		t.Fatalf("D1 should advance alone: %+v", s.SepIndex)
	}
	if s.State != StateRunning {
		t.Fatalf("still running (D2 transiting): %s", s.State)
	}
	e.Observe(1, wpB.Lat, wpB.Lon, 0) // D1 finishes
	e.Observe(2, wpA.Lat, wpA.Lon, 0)
	e.Observe(2, wpB.Lat, wpB.Lon, 0) // D2 finishes
	if s := e.Snapshot(); s.State != StateCompleted {
		t.Fatalf("both finished → COMPLETED: %+v", s)
	}
}

func TestSeparatePerDroneWait(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	p := separatePlan()
	p.Routes[0].Points[0].WaitSeconds = 30 // D1 waits at its WP0
	mustStart(t, e, p)

	e.Observe(1, wpA.Lat, wpA.Lon, 0) // D1 arrives → holds; D2 still transiting
	if s := e.Snapshot(); s.State != StateRunning {
		t.Fatalf("mission still RUNNING while D2 transits: %s", s.State)
	}
	e.Observe(2, wpA.Lat, wpA.Lon, 0)
	e.Observe(2, wpB.Lat, wpB.Lon, 0) // D2 done; only D1 holding → WAITING
	if s := e.Snapshot(); s.State != StateWaiting {
		t.Fatalf("only D1 holding → WAITING: %+v", s)
	}
	clk.advance(31 * time.Second)
	e.Poll() // D1 wait done → advance D1 to WP1
	if s := e.Snapshot(); s.SepIndex[1] != 1 || s.State != StateRunning {
		t.Fatalf("D1 should resume after WAIT: %+v", s)
	}
	e.Observe(1, wpB.Lat, wpB.Lon, 0) // D1 finishes → COMPLETED
	if s := e.Snapshot(); s.State != StateCompleted {
		t.Fatalf("all done → COMPLETED: %+v", s)
	}
}

// ---- SWARM_LEADER ---------------------------------------------------------

func TestSwarmLeaderOnlyLeaderDrivesProgression(t *testing.T) {
	e := NewEngine(nil)
	p := MissionPlan{
		PlanID: "swarm", Mode: ModeSwarmLeader, Participants: []uint32{1, 2, 3}, LeaderID: 1,
		Routes: []Route{{Points: []Waypoint{wpA, wpB}}},
	}
	mustStart(t, e, p)
	e.Observe(2, wpA.Lat, wpA.Lon, 0) // follower at WP0 — must be ignored
	if e.Snapshot().CurrentIndex != 0 {
		t.Fatal("follower arrival must not advance a SWARM_LEADER mission")
	}
	e.Observe(1, wpA.Lat, wpA.Lon, 0) // leader arrives → advance
	if e.Snapshot().CurrentIndex != 1 {
		t.Fatal("leader arrival should advance")
	}
}

// ---- cancel / interrupt (safety) ------------------------------------------

func TestCancelStaleSafeAndIdempotent(t *testing.T) {
	e := NewEngine(nil)
	id := mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1))
	if err := e.Cancel(id + 999); err != nil { // stale/unknown run id
		t.Fatalf("stale cancel should be a no-op: %v", err)
	}
	if s := e.Snapshot(); s.State != StateRunning {
		t.Fatalf("stale cancel must not touch active run: %s", s.State)
	}
	if err := e.Cancel(id); err != nil {
		t.Fatalf("cancel: %v", err)
	}
	if e.Snapshot().State != StateCancelled {
		t.Fatal("run should be CANCELLED")
	}
	if err := e.Cancel(id); err != nil { // idempotent
		t.Fatalf("second cancel should be safe: %v", err)
	}
}

func TestNoAdvanceAfterCancel(t *testing.T) {
	e := NewEngine(nil)
	id := mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1))
	e.Cancel(id)
	e.Observe(1, wpA.Lat, wpA.Lon, 0) // stale observation after cancel
	if s := e.Snapshot(); s.State != StateCancelled || s.CurrentIndex != 0 {
		t.Fatalf("observation after cancel must not advance: %+v", s)
	}
}

func TestCancelInvalidatesWait(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	wpWait := wpA
	wpWait.WaitSeconds = 60
	id := mustStart(t, e, groupedPlan([]Waypoint{wpWait, wpB}, 1))
	e.Observe(1, wpWait.Lat, wpWait.Lon, 0) // WAITING
	e.Cancel(id)
	clk.advance(120 * time.Second)
	if e.Poll() {
		t.Fatal("Poll after cancel must not advance a stale WAIT")
	}
	if e.Snapshot().State != StateCancelled {
		t.Fatal("should remain CANCELLED")
	}
}

func TestInterruptFailsafeFailClosed(t *testing.T) {
	e := NewEngine(nil)
	mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1, 2))
	e.Interrupt(9, "battery", "") // non-participant — ignored
	if e.Snapshot().State != StateRunning {
		t.Fatal("non-participant failsafe must not interrupt")
	}
	e.Interrupt(1, "battery", "battery failsafe D1 → RTL")
	s := e.Snapshot()
	if s.State != StateInterrupted || s.Active {
		t.Fatalf("participant failsafe should INTERRUPT: %+v", s)
	}
	if s.TerminalReason == "" {
		t.Fatal("terminal reason should be recorded")
	}
}

func TestInterruptNoAutoResume(t *testing.T) {
	e := NewEngine(nil)
	mustStart(t, e, groupedPlan([]Waypoint{wpA, wpB}, 1))
	e.Interrupt(1, "link", "link lost")
	// after interrupt, neither observation nor poll may resume the mission
	e.Observe(1, wpA.Lat, wpA.Lon, 0)
	e.Poll()
	if e.Snapshot().State != StateInterrupted {
		t.Fatal("interrupted mission must not auto-resume")
	}
}

// ---- F2 exit criterion: SHADOW ONLY, no command dependency ----------------

func TestNoFlightCommandImports(t *testing.T) {
	// The shadow engine must not import the fleet/command send path (V2 F2).
	fset := token.NewFileSet()
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatal(err)
	}
	forbidden := []string{"internal/fleet", "internal/command", "internal/swarm", "internal/mavlink"}
	for _, f := range files {
		af, err := parser.ParseFile(fset, f, nil, parser.ImportsOnly)
		if err != nil {
			t.Fatalf("parse %s: %v", f, err)
		}
		for _, imp := range af.Imports {
			path := strings.Trim(imp.Path.Value, `"`)
			for _, bad := range forbidden {
				if strings.Contains(path, bad) {
					t.Errorf("%s imports %q — shadow engine must not touch command path", f, path)
				}
			}
		}
	}
}
