package mission

import (
	"errors"
	"testing"
	"time"
)

// rawWP reports whether an intent targets the raw shared waypoint (fallback), not
// a formation-offset point.
func isRawWP(in GotoIntent, wp Waypoint) bool {
	return in.Lat == wp.Lat && in.Lon == wp.Lon
}

// V3-S09-A adversarial engine tests — deterministic, no sleeps.  Arrival is driven
// by observing each drone AT the exact frozen offset target the claim returned, so
// the barrier/advance/terminal semantics are exercised without any real FC.

// ── helpers ────────────────────────────────────────────────────────────────

func groupedMultiPlan(points []Waypoint, participants ...uint32) MissionPlan {
	alts := make(map[uint32]float64, len(participants))
	for _, id := range participants {
		alts[id] = 30 + float64(id) // distinct per-drone altitude
	}
	// Copy points so a caller mutating a waypoint (WAIT/action negative cases)
	// cannot leak into the shared gmRoute backing array.
	pts := append([]Waypoint(nil), points...)
	return MissionPlan{
		PlanID:               "gm1",
		Mode:                 ModeGrouped,
		Participants:         participants,
		Routes:               []Route{{DroneID: 0, Points: pts}},
		ParticipantAltitudes: alts,
	}
}

var gmRoute = []Waypoint{
	{Seq: 0, Lat: 14.0050, Lon: 100.0050, Alt: 25},
	{Seq: 1, Lat: 14.0100, Lon: 100.0100, Alt: 25},
}

var gmStart = map[uint32][2]float64{
	1: {14.0000, 100.0000},
	2: {14.0000, 100.0010},
	3: {14.0010, 100.0005},
}

func startGroupedMulti(t *testing.T, e *Engine, participants ...uint32) uint64 {
	t.Helper()
	e.EnableGroupedMultiAuthority()
	id, err := e.StartOp(groupedMultiPlan(gmRoute, participants...), "gm-op")
	if err != nil {
		t.Fatalf("StartOp grouped-multi: %v", err)
	}
	return id
}

func observePositions(e *Engine, pos map[uint32][2]float64) {
	for id, p := range pos {
		e.Observe(id, p[0], p[1], 0)
	}
}

func claimGroup(t *testing.T, e *Engine) []GotoIntent {
	t.Helper()
	intents, ok, err := e.ClaimAuthorityGroupedGotos()
	if err != nil {
		t.Fatalf("ClaimAuthorityGroupedGotos err: %v", err)
	}
	if !ok {
		t.Fatalf("expected a group claim, got none")
	}
	return intents
}

// assertGroupIntents checks exactly one intent per participant, no duplicate
// drone, the expected front-of-travel order, and per-drone frozen altitude.
func assertGroupIntents(t *testing.T, intents []GotoIntent, wantOrder []uint32, index int) {
	t.Helper()
	if len(intents) != len(wantOrder) {
		t.Fatalf("intent count = %d, want %d (%+v)", len(intents), len(wantOrder), intents)
	}
	seen := map[uint32]bool{}
	for i, in := range intents {
		if seen[in.DroneID] {
			t.Fatalf("duplicate GOTO for D%d in one dispatch", in.DroneID)
		}
		seen[in.DroneID] = true
		if in.DroneID != wantOrder[i] {
			t.Fatalf("dispatch order = %v, want %v", intentIDs(intents), wantOrder)
		}
		if in.Index != index {
			t.Fatalf("intent D%d index = %d, want %d", in.DroneID, in.Index, index)
		}
		if in.Alt != 30+float64(in.DroneID) {
			t.Fatalf("intent D%d alt = %v, want per-drone %v", in.DroneID, in.Alt, 30+float64(in.DroneID))
		}
	}
}

func intentIDs(intents []GotoIntent) []uint32 {
	out := make([]uint32, len(intents))
	for i, in := range intents {
		out[i] = in.DroneID
	}
	return out
}

func arriveIntents(e *Engine, intents []GotoIntent) {
	for _, in := range intents {
		e.Observe(in.DroneID, in.Lat, in.Lon, in.Alt)
	}
}

// ── 1 + 20. happy path + exact command count / order / no duplicate ─────────

func TestGroupedMultiHappyPathExactCommands(t *testing.T) {
	e := NewEngine(nil)
	id := startGroupedMulti(t, e, 1, 2, 3)

	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	assertGroupIntents(t, wp0, []uint32{3, 1, 2}, 0)

	// second claim before the group advances must emit nothing (barrier).
	if _, ok, err := e.ClaimAuthorityGroupedGotos(); ok || err != nil {
		t.Fatalf("duplicate claim before advance: ok=%v err=%v", ok, err)
	}

	arriveIntents(e, wp0)
	if s := e.Snapshot(); s.CurrentIndex != 1 || s.State != StateRunning {
		t.Fatalf("all arrived at WP0 should advance to WP1: %+v", s)
	}

	wp1 := claimGroup(t, e)
	if len(wp1) != 3 {
		t.Fatalf("WP1 must dispatch exactly 3, got %+v", wp1)
	}
	arriveIntents(e, wp1)
	if s := e.Snapshot(); s.State != StateCompleted || s.Active || s.RunID != id {
		t.Fatalf("all arrived at final WP should COMPLETE: %+v", s)
	}
	// a completed run emits no further command.
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("completed run must not emit a claim")
	}
}

// ── 2 + 4 + 5. group barrier: early arrival / stale / disconnected wait ─────

func TestGroupedMultiBarrierHoldsUntilAllArrive(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)

	// D1 arrives; D2 sends a STALE far-away sample; D3 never sends (disconnect).
	byID := map[uint32]GotoIntent{}
	for _, in := range wp0 {
		byID[in.DroneID] = in
	}
	e.Observe(1, byID[1].Lat, byID[1].Lon, 0) // D1 at its target
	e.Observe(2, 14.9, 101.9, 0)              // D2 stale/far
	if s := e.Snapshot(); s.CurrentIndex != 0 || s.State != StateRunning {
		t.Fatalf("group must not advance with only D1 arrived: %+v", s)
	}
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("barrier: no re-dispatch while waiting for the rest")
	}
	// D2 then D3 arrive -> advance exactly once.
	e.Observe(2, byID[2].Lat, byID[2].Lon, 0)
	e.Observe(3, byID[3].Lat, byID[3].Lon, 0)
	if s := e.Snapshot(); s.CurrentIndex != 1 {
		t.Fatalf("all arrived -> advance once: %+v", s)
	}
}

// ── 3. simultaneous final-participant arrival -> exactly one transition ─────

func TestGroupedMultiSimultaneousArrivalSingleTransition(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	arriveIntents(e, wp0) // all three "at once"
	if s := e.Snapshot(); s.CurrentIndex != 1 {
		t.Fatalf("simultaneous arrival must advance exactly one step: %+v", s)
	}
	// exactly one advance transition recorded (no double advance).
	advances := 0
	for _, tr := range e.History() {
		if tr.Event == "advance" {
			advances++
		}
	}
	if advances != 1 {
		t.Fatalf("want exactly one advance transition, got %d", advances)
	}
}

// ── 14. crossed / stale telemetry after advance must not regress or re-advance ─

func TestGroupedMultiStaleTelemetryAfterAdvanceIsInert(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	arriveIntents(e, wp0) // advance to WP1
	wp1 := claimGroup(t, e)

	// A late duplicate of an OLD WP0-target sample arrives for D1.  With WP1 now
	// frozen, arrival is judged against the WP1 target, so the old sample is far
	// and cannot regress the index or double-advance.
	for _, in := range wp0 {
		if in.DroneID == 1 {
			e.Observe(1, in.Lat, in.Lon, 0)
		}
	}
	if s := e.Snapshot(); s.CurrentIndex != 1 {
		t.Fatalf("stale WP0 telemetry must not move the index: %+v", s)
	}
	// completing WP1 still works normally.
	arriveIntents(e, wp1)
	if s := e.Snapshot(); s.State != StateCompleted {
		t.Fatalf("WP1 completion should still finish: %+v", s)
	}
}

// ── 6. Partial participant GOTO rejection preserves legacy best-effort: the run
//        does NOT fail, successful drones keep their target, the barrier waits for
//        the rejected drone, no blind retry, and the rejection is observable. ────

func TestGroupedMultiParticipantRejectDoesNotFailRun(t *testing.T) {
	e := NewEngine(nil)
	id := startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	byID := map[uint32]GotoIntent{}
	for _, in := range wp0 {
		byID[in.DroneID] = in
	}

	// D2's GOTO is rejected by the safety envelope.
	if !e.NoteParticipantRejected(id, 0, 2, "geofence rejected D2 GOTO") {
		t.Fatal("rejection for an active participant must record")
	}
	// The run stays RUNNING — one participant reject never terminally fails it.
	if s := e.Snapshot(); s.State != StateRunning || !s.Active {
		t.Fatalf("participant reject must NOT fail the run: %+v", s)
	}
	// Observable / auditable.
	if e.GroupRejections()[2] == "" {
		t.Fatal("rejection must be observable via GroupRejections")
	}
	// The successful participants arrive at their frozen targets, but the barrier
	// keeps waiting for the rejected (never-commanded) D2.
	e.Observe(1, byID[1].Lat, byID[1].Lon, 0)
	e.Observe(3, byID[3].Lat, byID[3].Lon, 0)
	if s := e.Snapshot(); s.CurrentIndex != 0 || s.State != StateRunning {
		t.Fatalf("barrier must keep waiting for the rejected participant: %+v", s)
	}
	// No blind retry: the index stays claimed, so no duplicate D2 GOTO is emitted.
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("rejected participant must not be auto-retried (index stays claimed)")
	}
	// If the operator recovers D2 and it arrives, the group advances normally, and
	// the rejection record is cleared for the next index.
	e.Observe(2, byID[2].Lat, byID[2].Lon, 0)
	if s := e.Snapshot(); s.CurrentIndex != 1 {
		t.Fatalf("group should advance once the rejected drone finally arrives: %+v", s)
	}
	if len(e.GroupRejections()) != 0 {
		t.Fatalf("advance must clear the previous index's rejections: %v", e.GroupRejections())
	}
}

func TestGroupedMultiStaleRejectionCannotAttachAfterAdvanceOrNewRun(t *testing.T) {
	e := NewEngine(nil)
	runA := startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	arriveIntents(e, wp0)
	_ = claimGroup(t, e)
	if e.NoteParticipantRejected(runA, 0, 2, "late wp0 reject") {
		t.Fatal("old waypoint rejection attached after advance")
	}
	// Complete A and start B; both old run and old index identities are stale.
	arriveIntents(e, func() []GotoIntent {
		intents, _, _ := e.ClaimAuthorityGroupedGotos()
		return intents
	}())
	// The previous helper claim was already consumed above, so complete directly
	// from the frozen target snapshot by restarting after cancellation instead.
	if e.Snapshot().Active {
		_ = e.Cancel(runA)
	}
	runB, err := e.StartOp(groupedMultiPlan(gmRoute, 1, 2, 3), "gm-op-b")
	if err != nil {
		t.Fatal(err)
	}
	e.SeedPositions(gmStart)
	_ = claimGroup(t, e)
	if runB == runA {
		t.Fatal("run identity reused")
	}
	if e.NoteParticipantRejected(runA, 1, 2, "late run A reject") {
		t.Fatal("old run rejection attached to run B")
	}
	if len(e.Snapshot().Rejections) != 0 {
		t.Fatalf("new run polluted by stale rejection: %+v", e.Snapshot().Rejections)
	}
}

// ── 7/8/9/13. operator takeover (HOLD/STOP ALL/KILL/cancel) during a waypoint ─

func TestGroupedMultiOperatorTakeoverDuringWaypoint(t *testing.T) {
	for _, name := range []string{"hold", "stopall", "kill", "cancel"} {
		t.Run(name, func(t *testing.T) {
			e := NewEngine(nil)
			id := startGroupedMulti(t, e, 1, 2, 3)
			observePositions(e, gmStart)
			wp0 := claimGroup(t, e)
			// Every operator takeover class maps to a terminal mission Cancel at the
			// engine (the API makes the run terminal before emitting the takeover).
			_ = e.Cancel(id)
			if s := e.Snapshot(); s.State != StateCancelled || s.Active {
				t.Fatalf("%s takeover must make the run terminal: %+v", name, s)
			}
			// A late arrival for the already-commanded WP0 targets must NOT advance
			// or emit a new claim after the takeover won.
			arriveIntents(e, wp0)
			if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
				t.Fatalf("%s: no claim may be emitted after takeover", name)
			}
			if s := e.Snapshot(); s.State != StateCancelled {
				t.Fatalf("%s: late telemetry must not resume: %+v", name, s)
			}
		})
	}
}

// ── 10. takeover exactly during the WP0->WP1 transition emits no WP1 command ─

func TestGroupedMultiTakeoverAtTransitionEmitsNoNextGoto(t *testing.T) {
	e := NewEngine(nil)
	id := startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	arriveIntents(e, wp0) // advanced to WP1, but WP1 not claimed yet
	if e.Snapshot().CurrentIndex != 1 {
		t.Fatal("precondition: advanced to WP1")
	}
	_ = e.Cancel(id) // takeover in the transition window
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("cancel in transition must suppress the WP1 dispatch")
	}
	if e.Snapshot().State != StateCancelled {
		t.Fatal("run must be cancelled")
	}
}

// ── 11 + 12. battery / link failsafe interrupt, no auto-resume ──────────────

func TestGroupedMultiFailsafeInterrupt(t *testing.T) {
	for _, cat := range []string{"battery", "link"} {
		t.Run(cat, func(t *testing.T) {
			e := NewEngine(nil)
			startGroupedMulti(t, e, 1, 2, 3)
			observePositions(e, gmStart)
			wp0 := claimGroup(t, e)
			e.Interrupt(2, cat, cat+" failsafe -> RTL owned by fleet")
			if s := e.Snapshot(); s.State != StateInterrupted || s.Active {
				t.Fatalf("%s failsafe must INTERRUPT the mission: %+v", cat, s)
			}
			arriveIntents(e, wp0)
			if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
				t.Fatalf("%s: interrupted mission must not emit a claim", cat)
			}
			if e.Snapshot().State != StateInterrupted {
				t.Fatalf("%s: interrupted mission must not auto-resume", cat)
			}
		})
	}
}

// non-participant failsafe must not touch a multi-drone run.
func TestGroupedMultiNonParticipantFailsafeIgnored(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	_ = claimGroup(t, e)
	e.Interrupt(99, "battery", "")
	if e.Snapshot().State != StateRunning {
		t.Fatal("non-participant failsafe must not interrupt the group")
	}
}

// ── 15. terminal mission receives later telemetry -> no resume ──────────────

func TestGroupedMultiTerminalIgnoresLaterTelemetry(t *testing.T) {
	e := NewEngine(nil)
	id := startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)
	wp0 := claimGroup(t, e)
	arriveIntents(e, wp0)
	wp1 := claimGroup(t, e)
	arriveIntents(e, wp1) // COMPLETED
	if e.Snapshot().State != StateCompleted {
		t.Fatal("precondition: completed")
	}
	// later telemetry / re-observe must not revive a terminal run.
	observePositions(e, gmStart)
	arriveIntents(e, wp1)
	if s := e.Snapshot(); s.State != StateCompleted {
		t.Fatalf("terminal run must ignore later telemetry: %+v", s)
	}
	_ = id
}

// ── 16. UI restart / duplicate Start must not create a second authority ─────

func TestGroupedMultiDuplicateStartNoDuplicateAuthority(t *testing.T) {
	e := NewEngine(nil)
	p := groupedMultiPlan(gmRoute, 1, 2, 3)
	e.EnableGroupedMultiAuthority()
	id1, err := e.StartOp(p, "gm-op")
	if err != nil {
		t.Fatal(err)
	}
	observePositions(e, gmStart)
	first := claimGroup(t, e)
	assertGroupIntents(t, first, []uint32{3, 1, 2}, 0)

	// same operation_id (UI restart / retry) returns the same run, no re-dispatch.
	id2, err := e.StartOp(p, "gm-op")
	if err != nil || id1 != id2 {
		t.Fatalf("duplicate Start must return same run: id1=%d id2=%d err=%v", id1, id2, err)
	}
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("duplicate Start must not produce a second dispatch for the same index")
	}
}

// ── 17. unsupported plans stay Python-owned (rejected at Start under S09-A) ──

func TestGroupedMultiRejectsUnsupportedPlans(t *testing.T) {
	tests := []struct {
		name string
		plan MissionPlan
	}{
		{"single participant", groupedMultiPlan(gmRoute, 1)},
		{"wait deferred", func() MissionPlan {
			p := groupedMultiPlan(gmRoute, 1, 2)
			p.Routes[0].Points[0].WaitSeconds = 60
			return p
		}()},
		{"payload action deferred", func() MissionPlan {
			p := groupedMultiPlan(gmRoute, 1, 2)
			p.Routes[0].Points[1].Action = ActionServoA
			return p
		}()},
		{"rtl_after deferred", func() MissionPlan {
			p := groupedMultiPlan(gmRoute, 1, 2)
			p.RtlAfter = true
			return p
		}()},
		{"separate mode", MissionPlan{
			PlanID: "sep", Mode: ModeSeparate, Participants: []uint32{1, 2},
			Routes: []Route{{DroneID: 1, Points: gmRoute}, {DroneID: 2, Points: gmRoute}},
		}},
		{"swarm leader mode", MissionPlan{
			PlanID: "swl", Mode: ModeSwarmLeader, Participants: []uint32{1, 2}, LeaderID: 1,
			Routes: []Route{{DroneID: 0, Points: gmRoute}},
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			e.EnableGroupedMultiAuthority()
			if _, err := e.StartOp(tc.plan, "op"); !errors.Is(err, ErrAuthorityUnsupported) {
				t.Fatalf("want ErrAuthorityUnsupported, got %v", err)
			}
			if e.Snapshot().Active {
				t.Fatal("unsupported plan must create no Core run")
			}
		})
	}
}

// the enabled multi scope must NOT widen the single-drone V1/V2 scope, and the
// single-drone scope must still reject multi-drone plans.
func TestSingleDroneAuthorityStillRejectsMultiParticipant(t *testing.T) {
	for _, tc := range []struct {
		name   string
		enable func(*Engine)
	}{
		{"core-single", (*Engine).EnableAuthority},
		{"core-single-wait", (*Engine).EnableWaitAuthority},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			tc.enable(e)
			if _, err := e.StartOp(groupedMultiPlan(gmRoute, 1, 2), "op"); !errors.Is(err, ErrAuthorityUnsupported) {
				t.Fatalf("%s must reject a 2-drone plan, got %v", tc.name, err)
			}
		})
	}
}

// ── 18 + 19. no dual authority — only the group claim emits in multi mode ───

func TestGroupedMultiNoDualAuthority(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	observePositions(e, gmStart)

	// The single-drone claim paths are inert while multi authority is active, so
	// two controllers can never both command the same run.
	if _, ok, err := e.ClaimAuthorityGoto(); ok || err != nil {
		t.Fatalf("single-drone GOTO claim must be inert in multi mode: ok=%v err=%v", ok, err)
	}
	if _, ok, err := e.ClaimAuthorityHold(); ok || err != nil {
		t.Fatalf("single-drone HOLD claim must be inert in multi mode: ok=%v err=%v", ok, err)
	}
	// Only the group claim emits, and only once per index.
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); !ok {
		t.Fatal("group claim should emit once")
	}
	if _, ok, _ := e.ClaimAuthorityGroupedGotos(); ok {
		t.Fatal("group claim must not emit twice for the same index")
	}
}

// ── 3 (canonicalization). duplicate ids never produce a duplicate GOTO ──────

func TestGroupedMultiCanonicalizesDuplicateParticipants(t *testing.T) {
	e := NewEngine(nil)
	e.EnableGroupedMultiAuthority()
	// [1,2,1] is two UNIQUE drones; D1 must be commanded exactly once.
	if _, err := e.StartOp(groupedMultiPlan(gmRoute, 1, 2, 1), "gm-op"); err != nil {
		t.Fatalf("duplicate-id multi plan should canonicalize + start: %v", err)
	}
	if got := e.Snapshot().Participants; len(got) != 2 || got[0] != 1 || got[1] != 2 {
		t.Fatalf("participants must be unique+sorted, got %v", got)
	}
	observePositions(e, map[uint32][2]float64{1: gmStart[1], 2: gmStart[2]})
	intents := claimGroup(t, e)
	if len(intents) != 2 {
		t.Fatalf("two unique participants must yield exactly 2 GOTOs, got %+v", intents)
	}
	seen := map[uint32]int{}
	for _, in := range intents {
		seen[in.DroneID]++
	}
	if seen[1] != 1 || seen[2] != 1 {
		t.Fatalf("each unique drone must be commanded once: %v", seen)
	}
}

func TestGroupedMultiDuplicateOnlyIsNotMultiDrone(t *testing.T) {
	e := NewEngine(nil)
	e.EnableGroupedMultiAuthority()
	// [1,1] collapses to a single unique drone -> not eligible for multi authority.
	if _, err := e.StartOp(groupedMultiPlan(gmRoute, 1, 1), "op"); !errors.Is(err, ErrAuthorityUnsupported) {
		t.Fatalf("[1,1] must be rejected as single-drone, got %v", err)
	}
}

// ── 2 (position source). seeded fleet telemetry lets WP0 use real offsets ───

func TestGroupedMultiSeedPositionsBeforeObserveGivesOffsets(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	// No Observe tick yet — seed trusted fleet positions directly.
	e.SeedPositions(gmStart)
	intents := claimGroup(t, e)
	// With real positions the group must NOT collapse to the raw waypoint.
	rawCount := 0
	for _, in := range intents {
		if isRawWP(in, gmRoute[0]) {
			rawCount++
		}
	}
	if rawCount != 0 {
		t.Fatalf("seeded positions must yield offset targets, got %d raw-waypoint intents", rawCount)
	}
	assertGroupIntents(t, intents, []uint32{3, 1, 2}, 0)
}

func TestGroupedMultiZeroPositionsFallBackToRawWaypoint(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	// Genuinely no positions seeded/observed -> every drone falls back to raw WP.
	intents := claimGroup(t, e)
	for _, in := range intents {
		if !isRawWP(in, gmRoute[0]) {
			t.Fatalf("with no positions, D%d must fall back to the raw waypoint: %+v", in.DroneID, in)
		}
	}
}

func TestGroupedMultiPartialPositionsFallBackPerDrone(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	// Only D1 and D2 have positions; D3 is never seen -> D3 falls back to raw WP
	// and is dispatched last, while D1/D2 get offsets from their 2-drone centroid.
	e.SeedPositions(map[uint32][2]float64{1: gmStart[1], 2: gmStart[2]})
	intents := claimGroup(t, e)
	byID := map[uint32]GotoIntent{}
	for _, in := range intents {
		byID[in.DroneID] = in
	}
	if !isRawWP(byID[3], gmRoute[0]) {
		t.Fatalf("never-seen D3 must fall back to raw waypoint: %+v", byID[3])
	}
	if intents[len(intents)-1].DroneID != 3 {
		t.Fatalf("unknown-position drone must dispatch last, order %v", intentIDs(intents))
	}
}

// ── 6 (real staleness). a position older than the freshness window is unusable ─

func TestGroupedMultiStalePositionTreatedUnavailable(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.SetPositionFreshness(3 * time.Second)
	startGroupedMulti(t, e, 1, 2, 3)
	e.SeedPositions(gmStart) // stamped at clk t0
	clk.advance(10 * time.Second)
	// All positions are now older than the 3s freshness window -> unavailable ->
	// every drone falls back to the raw waypoint (not a stale centroid).
	intents := claimGroup(t, e)
	for _, in := range intents {
		if !isRawWP(in, gmRoute[0]) {
			t.Fatalf("stale position must be unavailable; D%d should fall back to raw WP: %+v", in.DroneID, in)
		}
	}
}

func TestGroupedMultiFreshPositionIsUsed(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.SetPositionFreshness(3 * time.Second)
	startGroupedMulti(t, e, 1, 2, 3)
	e.SeedPositions(gmStart)
	clk.advance(1 * time.Second) // within the freshness window
	intents := claimGroup(t, e)
	for _, in := range intents {
		if isRawWP(in, gmRoute[0]) {
			t.Fatalf("fresh position must be used; D%d should have an offset target: %+v", in.DroneID, in)
		}
	}
}

func TestGroupedMultiProductionSamplesFreshStaleInvalidAndPartial(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.SetPositionFreshness(3 * time.Second)
	startGroupedMulti(t, e, 1, 2, 3)
	e.SeedPositionSamples(map[uint32]PositionSample{
		1: {Lat: gmStart[1][0], Lon: gmStart[1][1], Age: time.Second, Valid: true},
		2: {Lat: gmStart[2][0], Lon: gmStart[2][1], Age: 2 * time.Second, Valid: true},
		3: {Lat: gmStart[3][0], Lon: gmStart[3][1], Age: 10 * time.Second, Valid: true},
	})
	intents := claimGroup(t, e)
	byID := map[uint32]GotoIntent{}
	for _, in := range intents {
		byID[in.DroneID] = in
	}
	if isRawWP(byID[1], gmRoute[0]) || isRawWP(byID[2], gmRoute[0]) {
		t.Fatal("fresh D1/D2 positions must contribute to centroid offsets")
	}
	if !isRawWP(byID[3], gmRoute[0]) {
		t.Fatal("stale D3 must use Legacy raw-waypoint fallback")
	}
}

func TestGroupedMultiZeroNoGPSCannotEnterCentroid(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	e.SeedPositions(gmStart) // D3 previously had a valid cached position.
	e.SeedPositionSamples(map[uint32]PositionSample{
		3: {Lat: 0, Lon: 0, Valid: false}, // current no-GPS invalidates that cache.
	})
	intents := claimGroup(t, e)
	for _, in := range intents {
		if in.DroneID == 3 && !isRawWP(in, gmRoute[0]) {
			t.Fatalf("(0,0) D3 must be unavailable and use raw fallback: %+v", in)
		}
	}
}

func TestGroupedMultiPollingSameStaleSnapshotNeverRefreshesIt(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	e.SetPositionFreshness(3 * time.Second)
	startGroupedMulti(t, e, 1, 2, 3)
	first := make(map[uint32]PositionSample)
	for id, p := range gmStart {
		first[id] = PositionSample{Lat: p[0], Lon: p[1], Age: 10 * time.Second, Valid: true}
	}
	e.SeedPositionSamples(first)
	for i := 1; i <= 5; i++ {
		clk.advance(time.Second)
		polled := make(map[uint32]PositionSample)
		for id, p := range gmStart {
			// SafetyState age grows while the cached coordinate stays unchanged;
			// observedAt therefore remains the original FC message time.
			polled[id] = PositionSample{Lat: p[0], Lon: p[1],
				Age: time.Duration(10+i) * time.Second, Valid: true}
		}
		e.SeedPositionSamples(polled)
	}
	for _, in := range claimGroup(t, e) {
		if !isRawWP(in, gmRoute[0]) {
			t.Fatalf("polling stale cached sample made D%d fresh: %+v", in.DroneID, in)
		}
	}
}

// arrival before dispatch (no frozen targets yet) must never advance the barrier.
func TestGroupedMultiArrivalBeforeDispatchIsInert(t *testing.T) {
	e := NewEngine(nil)
	startGroupedMulti(t, e, 1, 2, 3)
	// telemetry arrives at the raw waypoint before any GOTO was claimed.
	for _, in := range gmRoute[:1] {
		e.Observe(1, in.Lat, in.Lon, 0)
		e.Observe(2, in.Lat, in.Lon, 0)
		e.Observe(3, in.Lat, in.Lon, 0)
	}
	if s := e.Snapshot(); s.CurrentIndex != 0 || s.State != StateRunning {
		t.Fatalf("arrival before dispatch must not advance: %+v", s)
	}
}
