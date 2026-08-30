package mission

import (
	"strings"
	"testing"
	"time"
)

func durableRecord(t *testing.T, e *Engine, session string) DurableMissionRecord {
	t.Helper()
	record, ok := e.DurableRecord(session)
	if !ok || record == nil {
		t.Fatal("expected durable mission record")
	}
	if err := ValidateDurableMissionRecord(*record); err != nil {
		t.Fatalf("durable record invalid: %v", err)
	}
	return *record
}

func TestDurableActiveRunRestoresAsCommandInertRecovery(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	first.EnableAuthority()
	runID, err := first.StartOpWithRunID(
		groupedPlan([]Waypoint{wpA, wpB}, 1), "op-active", 41)
	if err != nil {
		t.Fatal(err)
	}
	record := durableRecord(t, first, "session-old")
	if !record.AuthorityActive || record.State != StateRunning {
		t.Fatalf("active authority not captured: %+v", record)
	}

	clk.advance(time.Second)
	restarted := NewEngine(clk.now)
	restarted.EnableAuthority()
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	snap := restarted.Snapshot()
	if snap.RunID != runID || !snap.RecoveryRequired ||
		snap.RecoveryPreviousState != StateRunning ||
		snap.State != StateInterrupted || snap.Active || snap.Authority {
		t.Fatalf("unfinished run must restore as inert recovery: %+v", snap)
	}
	if _, ok, err := restarted.ClaimAuthorityGoto(); err != nil || ok {
		t.Fatalf("recovery must emit no GOTO intent: ok=%v err=%v", ok, err)
	}
	if restarted.Poll() {
		t.Fatal("recovery must not progress on Poll")
	}
}

func TestDurableWaitRestoresFrozenWithoutTimerOrHold(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	first.EnableWaitAuthority()
	waitWP := wpA
	waitWP.WaitSeconds = 60
	if _, err := first.StartOpWithRunID(
		groupedPlan([]Waypoint{waitWP, wpB}, 1), "op-wait", 51); err != nil {
		t.Fatal(err)
	}
	first.Observe(1, waitWP.Lat, waitWP.Lon, 0)
	record := durableRecord(t, first, "session-wait")
	if record.State != StateWaiting || len(record.Waits) != 1 {
		t.Fatalf("WAIT evidence missing: %+v", record)
	}

	clk.advance(10 * time.Minute)
	restarted := NewEngine(clk.now)
	restarted.EnableWaitAuthority()
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	before := restarted.Snapshot()
	if !before.RecoveryRequired || len(before.Waits) != 1 ||
		before.Waits[0].RemainingS <= 0 {
		t.Fatalf("WAIT must be frozen as recovery evidence: %+v", before)
	}
	if restarted.Poll() {
		t.Fatal("old WAIT deadline must not continue after restart")
	}
	if _, ok, _ := restarted.ClaimAuthorityHold(); ok {
		t.Fatal("recovery must not emit HOLD")
	}
	if _, ok, _ := restarted.ClaimAuthorityGoto(); ok {
		t.Fatal("recovery must not emit next GOTO")
	}
	after := restarted.Snapshot()
	if after.CurrentIndex != before.CurrentIndex ||
		after.Revision != before.Revision {
		t.Fatalf("recovery WAIT progressed: before=%+v after=%+v", before, after)
	}
}

func TestDurableSeparateProgressVisibleButNeverClaimed(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	if _, err := first.StartOpWithRunID(separatePlan(), "op-separate", 61); err != nil {
		t.Fatal(err)
	}
	first.Observe(1, wpA.Lat, wpA.Lon, 0)
	record := durableRecord(t, first, "session-separate")

	restarted := NewEngine(clk.now)
	restarted.EnableSeparateAuthority()
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	snap := restarted.Snapshot()
	if snap.SepIndex[1] != 1 || snap.SepIndex[2] != 0 ||
		!snap.RecoveryRequired || snap.Authority {
		t.Fatalf("SEPARATE recovery indexes/state: %+v", snap)
	}
	if intents, ok, err := restarted.ClaimAuthoritySeparateGotos(); err != nil || ok || len(intents) != 0 {
		t.Fatalf("SEPARATE recovery emitted intent: intents=%v ok=%v err=%v", intents, ok, err)
	}
}

// SEPARATE per-drone progression and a concurrent per-drone WAIT entry both keep
// the run RUNNING while another drone flies, yet each is operator-visible mission
// progress. They must bump Revision so the Server's revision-gated persist writes
// the advanced state instead of skipping it (the API-level restart bug this fixes:
// the moved sepIndex / new WAIT was otherwise lost on Core restart).
func TestSeparateProgressionAndWaitCreateDurableRevisionBoundary(t *testing.T) {
	t.Run("lone-advance-bumps-revision", func(t *testing.T) {
		e := NewEngine(nil)
		if _, err := e.StartOpWithRunID(separatePlan(), "op-sep-adv", 91); err != nil {
			t.Fatal(err)
		}
		before := durableRecord(t, e, "s").Revision
		e.Observe(1, wpA.Lat, wpA.Lon, 0) // D1 advances alone; D2 still transiting
		got := durableRecord(t, e, "s")
		if got.SeparateIndexes[1] != 1 || got.State != StateRunning || got.Revision <= before {
			t.Fatalf("lone SEPARATE advance must bump revision: before=%d %+v", before, got)
		}
	})

	t.Run("concurrent-wait-bumps-revision", func(t *testing.T) {
		clk := newClock()
		e := NewEngine(clk.now)
		p := separatePlan()
		p.Routes[0].Points[0].WaitSeconds = 60 // D1 holds at WP0 while D2 flies
		if _, err := e.StartOpWithRunID(p, "op-sep-wait", 92); err != nil {
			t.Fatal(err)
		}
		before := durableRecord(t, e, "s").Revision
		e.Observe(1, wpA.Lat, wpA.Lon, 0) // D1 enters WAIT; run stays RUNNING (D2 transits)
		got := durableRecord(t, e, "s")
		if got.State != StateRunning || len(got.Waits) != 1 || got.Waits[0].Scope != 1 ||
			got.Revision <= before {
			t.Fatalf("concurrent SEPARATE WAIT must bump revision + persist hold: before=%d %+v",
				before, got)
		}
	})
}

func TestDurableSwarmLeaderRecoveryDoesNotRestoreLeaderClaim(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	first.EnableSwarmLeaderAuthority()
	if _, err := first.StartOpWithRunID(
		swarmLeaderPlan(2, 1, 2, 3), "op-swl", 71); err != nil {
		t.Fatal(err)
	}
	if _, ok, err := first.ClaimAuthoritySwarmLeaderGoto(); err != nil || !ok {
		t.Fatalf("precondition leader claim: ok=%v err=%v", ok, err)
	}
	record := durableRecord(t, first, "session-swl")

	restarted := NewEngine(clk.now)
	restarted.EnableSwarmLeaderAuthority()
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	if snap := restarted.Snapshot(); !snap.RecoveryRequired || snap.Authority ||
		snap.Plan.LeaderID != 2 {
		t.Fatalf("SWARM_LEADER recovery state: %+v", snap)
	}
	if _, ok, err := restarted.ClaimAuthoritySwarmLeaderGoto(); err != nil || ok {
		t.Fatalf("restart must not restore leader GOTO claim: ok=%v err=%v", ok, err)
	}
}

func TestDurablePayloadPlanCannotRestartActionOrServoIntent(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	actionWP := wpA
	actionWP.Action = ActionServoA
	if _, err := first.StartOpWithRunID(
		groupedPlan([]Waypoint{actionWP, wpB}, 1), "op-payload", 81); err != nil {
		t.Fatal(err)
	}
	record := durableRecord(t, first, "session-payload")

	restarted := NewEngine(clk.now)
	restarted.EnableAuthority()
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	snap := restarted.Snapshot()
	if !snap.RecoveryRequired || snap.Plan.Routes[0].Points[0].Action != ActionServoA {
		t.Fatalf("payload evidence not preserved: %+v", snap)
	}
	if _, ok, err := restarted.ClaimAuthorityGoto(); ok || err != nil {
		t.Fatalf("payload recovery must emit no command intent: ok=%v err=%v", ok, err)
	}
}

func TestDurableTerminalRunsNeverBecomeRecoveryRequired(t *testing.T) {
	cases := []struct {
		name string
		end  func(*Engine, uint64)
		want State
	}{
		{name: "completed", want: StateCompleted, end: func(e *Engine, _ uint64) {
			e.Observe(1, wpA.Lat, wpA.Lon, 0)
		}},
		{name: "cancelled", want: StateCancelled, end: func(e *Engine, id uint64) {
			_ = e.Cancel(id)
		}},
		{name: "interrupted", want: StateInterrupted, end: func(e *Engine, _ uint64) {
			e.Interrupt(1, "link", "link failsafe")
		}},
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			clk := newClock()
			first := NewEngine(clk.now)
			id, err := first.StartOpWithRunID(
				groupedPlan([]Waypoint{wpA}, 1), "op-"+tc.name, uint64(90+i))
			if err != nil {
				t.Fatal(err)
			}
			tc.end(first, id)
			record := durableRecord(t, first, "session-terminal")
			restarted := NewEngine(clk.now)
			if err := restarted.RestoreDurableMission(record); err != nil {
				t.Fatal(err)
			}
			snap := restarted.Snapshot()
			if snap.State != tc.want || snap.Active || snap.Authority || snap.RecoveryRequired {
				t.Fatalf("terminal restart changed semantics: %+v", snap)
			}
		})
	}
}

func TestRecoveryClearExplicitIdempotentAndBlocksStaleOperation(t *testing.T) {
	clk := newClock()
	first := NewEngine(clk.now)
	if _, err := first.StartOpWithRunID(
		groupedPlan([]Waypoint{wpA, wpB}, 1), "old-operation", 101); err != nil {
		t.Fatal(err)
	}
	record := durableRecord(t, first, "session-old")
	restarted := NewEngine(clk.now)
	if err := restarted.RestoreDurableMission(record); err != nil {
		t.Fatal(err)
	}
	if cleared, incompatible := restarted.ClearRecovery(101); !cleared || incompatible {
		t.Fatalf("valid recovery clear: cleared=%v incompatible=%v", cleared, incompatible)
	}
	if cleared, _ := restarted.ClearRecovery(101); cleared {
		t.Fatal("recovery clear must be idempotent")
	}
	snap := restarted.Snapshot()
	if snap.RecoveryRequired || snap.State != StateInterrupted {
		t.Fatalf("clear must leave safe terminal evidence: %+v", snap)
	}
	if _, err := restarted.StartOpWithRunID(
		groupedPlan([]Waypoint{wpA}, 1), "old-operation", 102); err != ErrStaleOperation {
		t.Fatalf("old operation retry = %v, want ErrStaleOperation", err)
	}
}

func TestDurableRecordRejectsUnknownSchemaAndInvalidGeometry(t *testing.T) {
	clk := newClock()
	e := NewEngine(clk.now)
	if _, err := e.StartOpWithRunID(
		groupedPlan([]Waypoint{wpA}, 1), "op-invalid", 111); err != nil {
		t.Fatal(err)
	}
	record := durableRecord(t, e, "session-invalid")
	record.SchemaVersion++
	if err := ValidateDurableMissionRecord(record); err == nil ||
		!strings.Contains(err.Error(), "unsupported") {
		t.Fatalf("unknown schema must fail safe: %v", err)
	}
	record.SchemaVersion = DurableMissionSchemaVersion
	record.Plan.Routes[0].Points[0].Lat = 0
	record.Plan.Routes[0].Points[0].Lon = 0
	if err := ValidateDurableMissionRecord(record); err == nil {
		t.Fatal("invalid persisted geometry must fail safe")
	}
}

// S11-E — corrupted-persistence expansion. Beyond the checksum/truncation/unknown-
// schema cases already proven at the store layer, a *structurally valid* durable
// record whose fields contradict each other must also fail closed. Each variant
// below is a distinct semantic inconsistency an aircraft could not have been in;
// none may restore as a live/authoritative run.
//
//	Initial state:       a valid started run's durable record.
//	Injected failure:    one semantic field corrupted (mode/leader, participants,
//	                     index, WAIT scope, authority scope, terminal+authority,
//	                     recovery flag, transition revision).
//	Expected Safe State: ValidateDurableMissionRecord returns an error, so
//	                     RestoreDurableMission refuses and the run is not installed
//	                     (Active=false, Authority=false). The Server-level API test
//	                     TestCorruptPersistenceStartsVisibleIncompatibleRecoveryWithZeroCommands
//	                     proves the same funnel produces visible recovery with zero command.
//	MUST NOT happen:     a semantically corrupt record installing a runnable mission.
func TestDurableRecordFailsClosedOnSemanticCorruption(t *testing.T) {
	grouped := func(t *testing.T) DurableMissionRecord {
		e := NewEngine(newClock().now)
		e.EnableAuthority()
		if _, err := e.StartOpWithRunID(groupedPlan([]Waypoint{wpA, wpB}, 1), "op-g", 201); err != nil {
			t.Fatal(err)
		}
		return durableRecord(t, e, "sess-g")
	}
	separate := func(t *testing.T) DurableMissionRecord {
		e := NewEngine(newClock().now)
		if _, err := e.StartOpWithRunID(separatePlan(), "op-s", 202); err != nil {
			t.Fatal(err)
		}
		return durableRecord(t, e, "sess-s")
	}
	cases := []struct {
		name    string
		base    func(*testing.T) DurableMissionRecord
		corrupt func(*DurableMissionRecord)
	}{
		{"mode-leader-mismatch", grouped, func(r *DurableMissionRecord) { r.LeaderID = 7 }},
		{"participants-do-not-match-plan", grouped, func(r *DurableMissionRecord) { r.Participants = []uint32{1, 2} }},
		{"invalid-authority-scope", grouped, func(r *DurableMissionRecord) { r.AuthorityScope = "core-made-up" }},
		{"terminal-retains-authority", grouped, func(r *DurableMissionRecord) {
			r.State = StateCompleted
			r.AuthorityActive = true
		}},
		{"recovery-required-not-interrupted", grouped, func(r *DurableMissionRecord) { r.RecoveryRequired = true }},
		{"transition-revision-ahead-of-run", grouped, func(r *DurableMissionRecord) {
			r.LastTransition.Revision = r.Revision + 3
		}},
		{"grouped-wait-nonzero-scope", grouped, func(r *DurableMissionRecord) {
			r.Waits = []DurableMissionWait{{Scope: 5, Index: 0, TotalS: 10, RemainingS: 5}}
		}},
		{"separate-index-out-of-range", separate, func(r *DurableMissionRecord) { r.SeparateIndexes[1] = 99 }},
		{"separate-index-incomplete", separate, func(r *DurableMissionRecord) { delete(r.SeparateIndexes, 2) }},
		{"separate-wait-unknown-scope", separate, func(r *DurableMissionRecord) {
			r.Waits = []DurableMissionWait{{Scope: 9, Index: 0, TotalS: 10, RemainingS: 5}}
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := tc.base(t)
			if err := ValidateDurableMissionRecord(r); err != nil {
				t.Fatalf("baseline record must be valid: %v", err)
			}
			tc.corrupt(&r)
			if err := ValidateDurableMissionRecord(r); err == nil {
				t.Fatal("semantically corrupt durable record must fail closed")
			}
			restarted := NewEngine(newClock().now)
			if err := restarted.RestoreDurableMission(r); err == nil {
				t.Fatal("RestoreDurableMission must refuse a corrupt record")
			}
			if snap := restarted.Snapshot(); snap.Active || snap.Authority {
				t.Fatalf("refused corrupt record must not install a live run: %+v", snap)
			}
		})
	}
}
