package api

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
	corestore "github.com/swarmgod/backend/internal/store"
)

func singlePersistentPlan(planID string) *pb.MissionPlan {
	plan := pbGroupedPlan(planID)
	plan.Participants = []uint32{1}
	return plan
}

func attachPersistentSingleServer(s *Server, durable mission.DurableStore,
	cmd *apiMissionCommander, session string) {
	s.missionStore = durable
	s.missionSessionID = session
	s.missionAuthority = true
	s.missionExec = cmd
	s.mission.EnableWaitAuthority()
	if s.reservations == nil {
		s.reservations = make(map[uint32]*cmdLease)
	}
}

func TestCoreRestartActiveMissionBecomesRecoveryWithZeroCommands(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mission-restart.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first := newMissionServer()
	firstCmd := &apiMissionCommander{}
	attachPersistentSingleServer(first, firstStore, firstCmd, "core-session-1")
	start, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("persist-active"), OperationId: "op-persist-active",
	})
	if err != nil || !start.Ok || firstCmd.calls != 1 {
		t.Fatalf("first StartMission: resp=%+v err=%v calls=%d", start, err, firstCmd.calls)
	}
	oldRunID := start.RunId
	if err := firstStore.Close(); err != nil {
		t.Fatal(err)
	}

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	restartedCmd := &apiMissionCommander{}
	attachPersistentSingleServer(restarted, secondStore, restartedCmd, "core-session-2")
	restarted.restoreDurableMission()

	state, err := restarted.GetMissionState(context.Background(), &pb.GetMissionStateRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if state.Active || state.AuthorityActive || !state.RecoveryRequired ||
		state.State != pb.MissionRunState_MISSION_STATE_INTERRUPTED ||
		state.RecoveryPreviousState != pb.MissionRunState_MISSION_STATE_RUNNING ||
		state.RunId != oldRunID || state.CoreSessionId != "core-session-2" {
		t.Fatalf("restart recovery state: %+v", state)
	}
	if state.PersistedRevision != state.Revision || !state.PersistenceCompatible {
		t.Fatalf("recovery conversion was not durable: %+v", state)
	}
	restarted.dispatchMissionAuthority()
	restarted.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if restartedCmd.calls != 0 || restartedCmd.holdCalls != 0 {
		t.Fatalf("startup recovery emitted commands: goto=%d hold=%d",
			restartedCmd.calls, restartedCmd.holdCalls)
	}
}

func TestCoreRestartPendingOrReturningReturnBecomesRecoveryWithZeroCommands(t *testing.T) {
	for _, returning := range []bool{false, true} {
		name := "pending"
		if returning {
			name = "returning"
		}
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "mission-return-restart.db")
			firstStore, err := corestore.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			firstClosed := false
			defer func() {
				if !firstClosed {
					_ = firstStore.Close()
				}
			}()
			first := newMissionServer()
			firstCmd := &apiMissionCommander{}
			attachPersistentSingleServer(first, firstStore, firstCmd, "return-session-1")
			first.mission.EnableReturnPolicyPreFlip()
			plan := singlePersistentPlan("persist-return-" + name)
			plan.RtlAfter = true
			start, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
				Plan: plan, OperationId: "op-persist-return-" + name,
			})
			if err != nil || !start.Ok {
				t.Fatalf("start: %+v err=%v", start, err)
			}
			first.mission.Observe(1, 14.0, 100.0, 0)
			first.mission.Observe(1, 14.001, 100.0, 0)
			if returning {
				if _, ok := first.mission.ClaimReturnIntent(); !ok {
					t.Fatal("failed to claim returning state")
				}
			}
			if err := first.persistMissionState(false); err != nil {
				t.Fatal(err)
			}
			if err := firstStore.Close(); err != nil {
				t.Fatal(err)
			}
			firstClosed = true

			secondStore, err := corestore.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer secondStore.Close()
			restarted := newMissionServer()
			cmd := &apiMissionCommander{}
			attachPersistentSingleServer(restarted, secondStore, cmd, "return-session-2")
			restarted.mission.EnableReturnPolicyPreFlip()
			restarted.restoreDurableMission()
			restarted.dispatchMissionAuthority()
			restarted.dispatchReturnAuthority()
			snap := restarted.mission.Snapshot()
			if snap.Active || snap.Authority || !snap.RecoveryRequired ||
				snap.State != mission.StateInterrupted || snap.ReturnState != mission.ReturnStateRecoveryRequired ||
				cmd.calls != 0 || cmd.holdCalls != 0 || cmd.rtlCalls != 0 {
				t.Fatalf("restart auto-commanded Return: snap=%+v cmd=%+v", snap, cmd)
			}
		})
	}
}

func TestCoreRestartWaitDoesNotContinueTimerHoldOrWaypoint(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mission-wait.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first := newMissionServer()
	firstCmd := &apiMissionCommander{}
	attachPersistentSingleServer(first, firstStore, firstCmd, "wait-session-1")
	plan := singlePersistentPlan("persist-wait")
	plan.Routes[0].Points[0].WaitSeconds = 60
	start, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: plan, OperationId: "op-persist-wait",
	})
	if err != nil || !start.Ok {
		t.Fatalf("start WAIT: %+v err=%v", start, err)
	}
	first.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)})
	if firstCmd.holdCalls != 1 {
		t.Fatalf("precondition HOLD calls=%d", firstCmd.holdCalls)
	}
	firstStore.Close()

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(restarted, secondStore, cmd, "wait-session-2")
	restarted.restoreDurableMission()
	before := restarted.snapshotWithPersistenceStatus()
	if !before.RecoveryRequired || before.RecoveryPreviousState != mission.StateWaiting ||
		len(before.Waits) != 1 {
		t.Fatalf("WAIT recovery evidence: %+v", before)
	}
	restarted.mission.Poll()
	restarted.dispatchMissionAuthority()
	restarted.observeFrom([]*pb.Telemetry{telem(1, 14.001, 100.0)})
	after := restarted.snapshotWithPersistenceStatus()
	if cmd.calls != 0 || cmd.holdCalls != 0 ||
		after.CurrentIndex != before.CurrentIndex {
		t.Fatalf("restarted WAIT progressed or commanded: before=%+v after=%+v cmd=%+v",
			before, after, cmd)
	}
}

func TestCoreRestartSeparateIndexesVisibleWithZeroDispatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mission-separate.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first := newMissionServer()
	first.mission.EnableSeparateAuthority()
	first.missionAuthority = true
	first.missionExec = &apiMissionCommander{}
	first.missionStore = firstStore
	first.missionSessionID = "separate-session-1"
	first.missionPosSampleProvider = func() map[uint32]mission.PositionSample {
		return map[uint32]mission.PositionSample{
			1: {Lat: 14.001, Lon: 99.999, Valid: true},
			2: {Lat: 14.002, Lon: 99.999, Valid: true},
		}
	}
	start, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: pbSeparatePlan("persist-separate", 1, 2), OperationId: "op-persist-separate",
	})
	if err != nil || !start.Ok {
		t.Fatalf("start SEPARATE: %+v err=%v", start, err)
	}
	first.observeFrom([]*pb.Telemetry{telem(1, 14.001, 100.0)})
	expected := first.mission.Snapshot().SepIndex
	firstStore.Close()

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	restarted.mission.EnableSeparateAuthority()
	restarted.missionAuthority = true
	cmd := &apiMissionCommander{}
	restarted.missionExec = cmd
	restarted.missionStore = secondStore
	restarted.missionSessionID = "separate-session-2"
	restarted.restoreDurableMission()
	snap := restarted.mission.Snapshot()
	if !snap.RecoveryRequired || snap.Authority ||
		snap.SepIndex[1] != expected[1] || snap.SepIndex[2] != expected[2] {
		t.Fatalf("SEPARATE recovery: got=%+v expected=%v", snap, expected)
	}
	restarted.dispatchMissionAuthority()
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("SEPARATE recovery dispatched: %+v", cmd)
	}
}

func TestCoreRestartSwarmLeaderAndPayloadNeverStartCommands(t *testing.T) {
	cases := []struct {
		name   string
		engine func() *mission.Engine
		plan   mission.MissionPlan
	}{
		{name: "swarm-leader", engine: func() *mission.Engine {
			e := mission.NewEngine(nil)
			e.EnableSwarmLeaderAuthority()
			return e
		}, plan: mission.MissionPlan{
			PlanID: "persist-swl", Mode: mission.ModeSwarmLeader,
			Participants: []uint32{1, 2, 3}, LeaderID: 2,
			Routes: []mission.Route{{DroneID: 0, Points: []mission.Waypoint{
				{Seq: 0, Lat: 14, Lon: 100, Alt: 20},
			}}},
		}},
		{name: "payload", engine: func() *mission.Engine {
			return mission.NewEngine(nil)
		}, plan: mission.MissionPlan{
			PlanID: "persist-payload", Mode: mission.ModeGrouped,
			Participants: []uint32{1},
			Routes: []mission.Route{{DroneID: 0, Points: []mission.Waypoint{
				{Seq: 0, Lat: 14, Lon: 100, Alt: 20, Action: mission.ActionServoA},
			}}},
		}},
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), tc.name+".db")
			st, err := corestore.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			engine := tc.engine()
			runID, err := st.AllocateMissionRunID()
			if err != nil {
				t.Fatal(err)
			}
			if _, err := engine.StartOpWithRunID(tc.plan, "op-"+tc.name, runID); err != nil {
				t.Fatal(err)
			}
			record, _ := engine.DurableRecord("old-session")
			if err := st.SaveMissionRecord(*record); err != nil {
				t.Fatal(err)
			}
			st.Close()

			st2, err := corestore.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer st2.Close()
			restarted := newMissionServer()
			if i == 0 {
				restarted.mission.EnableSwarmLeaderAuthority()
			} else {
				restarted.mission.EnableAuthority()
			}
			restarted.missionAuthority = true
			cmd := &apiMissionCommander{}
			restarted.missionExec = cmd
			restarted.missionStore = st2
			restarted.missionSessionID = "new-session"
			restarted.restoreDurableMission()
			restarted.dispatchMissionAuthority()
			snap := restarted.mission.Snapshot()
			if !snap.RecoveryRequired || snap.Authority ||
				cmd.calls != 0 || cmd.holdCalls != 0 {
				t.Fatalf("%s recovery commanded or gained authority: snap=%+v cmd=%+v",
					tc.name, snap, cmd)
			}
		})
	}
}

func TestRecoveryClearAndLostStartRetryAcrossRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lost-start.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first := newMissionServer()
	attachPersistentSingleServer(first, firstStore, &apiMissionCommander{}, "lost-session-1")
	initial, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("lost-plan"), OperationId: "lost-operation",
	})
	if err != nil || !initial.Ok {
		t.Fatalf("initial start: %+v err=%v", initial, err)
	}
	firstStore.Close()

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(restarted, secondStore, cmd, "lost-session-2")
	restarted.restoreDurableMission()

	retry, err := restarted.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("lost-plan"), OperationId: "lost-operation",
	})
	if err != nil || retry.Ok || retry.RunId != initial.RunId {
		t.Fatalf("lost Start reply retry must not create authority: %+v err=%v", retry, err)
	}
	clear, err := restarted.CancelMission(context.Background(), &pb.CancelMissionRequest{
		RunId: initial.RunId, RequestId: "clear-recovery",
	})
	if err != nil || !clear.Ok {
		t.Fatalf("recovery clear: %+v err=%v", clear, err)
	}
	clear2, _ := restarted.CancelMission(context.Background(), &pb.CancelMissionRequest{
		RunId: initial.RunId, RequestId: "clear-recovery-retry",
	})
	if !clear2.Ok {
		t.Fatalf("recovery clear must be idempotent: %+v", clear2)
	}
	staleRetry, _ := restarted.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("lost-plan"), OperationId: "lost-operation",
	})
	if staleRetry.Ok {
		t.Fatalf("cleared old operation_id must remain retired: %+v", staleRetry)
	}
	newStart, err := restarted.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("new-plan"), OperationId: "new-operation",
	})
	if err != nil || !newStart.Ok || newStart.RunId <= initial.RunId {
		t.Fatalf("new operator intent did not get monotonic run id: %+v err=%v", newStart, err)
	}
	if cmd.calls != 1 {
		t.Fatalf("only the explicit new start may GOTO, calls=%d", cmd.calls)
	}
}

func TestCorruptPersistenceStartsVisibleIncompatibleRecoveryWithZeroCommands(t *testing.T) {
	path := filepath.Join(t.TempDir(), "corrupt.db")
	st, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	engine := mission.NewEngine(nil)
	runID, _ := st.AllocateMissionRunID()
	if _, err := engine.StartOpWithRunID(
		mission.MissionPlan{
			PlanID: "corrupt-plan", Mode: mission.ModeGrouped, Participants: []uint32{1},
			Routes: []mission.Route{{Points: []mission.Waypoint{{Seq: 0, Lat: 14, Lon: 100}}}},
		}, "corrupt-op", runID); err != nil {
		t.Fatal(err)
	}
	record, _ := engine.DurableRecord("corrupt-session")
	if err := st.SaveMissionRecord(*record); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`UPDATE mission_state SET record_json='{' WHERE singleton_id=1`); err != nil {
		t.Fatal(err)
	}
	st.Close()

	st2, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer st2.Close()
	restarted := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(restarted, st2, cmd, "corrupt-session-2")
	restarted.restoreDurableMission()
	state := restarted.snapshotWithPersistenceStatus()
	if !state.RecoveryRequired || state.RecoveryCompatible || state.Authority ||
		state.RunID != runID || !strings.Contains(state.RecoveryReason, "checksum") {
		t.Fatalf("corrupt recovery not visible/fail-safe: %+v", state)
	}
	restarted.dispatchMissionAuthority()
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("corrupt recovery emitted command: %+v", cmd)
	}
}

type failingDurableStore struct {
	next uint64
	err  error
}

func (f *failingDurableStore) AllocateMissionRunID() (uint64, error) {
	f.next++
	return f.next, nil
}
func (f *failingDurableStore) SaveMissionRecord(mission.DurableMissionRecord) error {
	return f.err
}
func (f *failingDurableStore) LoadMissionRecord() (*mission.DurableMissionRecord, error) {
	return nil, mission.ErrNoDurableMission
}
func (f *failingDurableStore) DeleteMissionRecord(uint64) error { return f.err }
func (f *failingDurableStore) LookupMissionOperation(string) (uint64, bool, error) {
	return 0, false, nil
}

func TestPersistenceWriteFailureStopsAuthorityWithoutInventingFlightAction(t *testing.T) {
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	s.missionAuthority = true
	s.mission.EnableAuthority()
	s.missionExec = cmd
	s.missionStore = &failingDurableStore{err: errors.New("disk unavailable")}
	s.missionSessionID = "fault-session"

	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("fault-plan"), OperationId: "fault-operation",
	})
	if err != nil || resp.Ok {
		t.Fatalf("persistence fault must reject authority: resp=%+v err=%v", resp, err)
	}
	snap := s.snapshotWithPersistenceStatus()
	if snap.Active || snap.Authority || snap.State != mission.StateInterrupted ||
		!strings.Contains(snap.TerminalReason, "persistence fault") {
		t.Fatalf("persistence fault state: %+v", snap)
	}
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("persistence failure emitted flight command: %+v", cmd)
	}
}

func TestStaleCancelCannotCancelNewerRunSend(t *testing.T) {
	s, _, block, sendCtx := newBlockingMissionServer(t, "stale-cancel-session")
	defer close(block)
	current := s.mission.Snapshot().RunID
	res, err := s.CancelMission(context.Background(), &pb.CancelMissionRequest{
		RunId: current - 1, RequestId: "stale-cancel",
	})
	if err != nil || !res.Ok {
		t.Fatalf("stale cancel response: %+v err=%v", res, err)
	}
	select {
	case <-sendCtx.Done():
		t.Fatal("stale CancelMission cancelled the newer run's in-flight send")
	case <-time.After(100 * time.Millisecond):
	}
	if snap := s.mission.Snapshot(); !snap.Active || snap.RunID != current {
		t.Fatalf("stale cancel changed current run: %+v", snap)
	}
}

// countingDurableStore delegates to a real store but counts durable writes, so a
// test can prove the Server's revision-gate suppresses persistence churn.
type countingDurableStore struct {
	inner mission.DurableStore
	saves int
}

func (c *countingDurableStore) AllocateMissionRunID() (uint64, error) {
	return c.inner.AllocateMissionRunID()
}
func (c *countingDurableStore) SaveMissionRecord(r mission.DurableMissionRecord) error {
	c.saves++
	return c.inner.SaveMissionRecord(r)
}
func (c *countingDurableStore) LoadMissionRecord() (*mission.DurableMissionRecord, error) {
	return c.inner.LoadMissionRecord()
}
func (c *countingDurableStore) DeleteMissionRecord(runID uint64) error {
	return c.inner.DeleteMissionRecord(runID)
}
func (c *countingDurableStore) LookupMissionOperation(op string) (uint64, bool, error) {
	return c.inner.LookupMissionOperation(op)
}

// S11-G — telemetry churn. Repeated telemetry that produces no mission progression
// must NOT create a SQLite write (S10 revision-gate), while a single real
// progression writes exactly once. This keeps durable evidence accurate without
// turning every packet into a write.
//
//	Initial state:       an active core-single authority run persisted at start.
//	Injected failure:    a burst of non-progressing (far-from-waypoint) telemetry,
//	                     then one arrival that advances the waypoint.
//	Expected Safe State: zero extra writes for the burst; exactly one write for the
//	                     progression. Revision/durable evidence stays accurate.
//	MUST NOT happen:     persistence write churn on inert telemetry.
func TestNoPersistenceWriteChurnWithoutMissionProgression(t *testing.T) {
	path := filepath.Join(t.TempDir(), "churn.db")
	inner, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer inner.Close()
	store := &countingDurableStore{inner: inner}
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(s, store, cmd, "churn-session")
	start, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("churn"), OperationId: "op-churn",
	})
	if err != nil || !start.Ok {
		t.Fatalf("start churn mission: %+v err=%v", start, err)
	}
	afterStart := store.saves
	if afterStart < 1 {
		t.Fatalf("start must persist at least once, saves=%d", afterStart)
	}
	for i := 0; i < 10; i++ {
		// Far from WP0 (14.0,100.0): no arrival, no progression, no revision change.
		s.observeFrom([]*pb.Telemetry{telem(1, 13.5, 99.0)})
	}
	if store.saves != afterStart {
		t.Fatalf("non-progressing telemetry churned persistence: start=%d after=%d",
			afterStart, store.saves)
	}
	s.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)}) // arrive WP0 → advance to WP1
	if s.mission.Snapshot().CurrentIndex != 1 {
		t.Fatalf("precondition: arrival must advance to WP1: idx=%d",
			s.mission.Snapshot().CurrentIndex)
	}
	if store.saves != afterStart+1 {
		t.Fatalf("single progression must persist exactly once: before=%d after=%d",
			afterStart, store.saves)
	}
}

// S11-D — GROUPED (the live core-single mode) durable progression must survive a
// Core restart as accurate command-inert recovery evidence, mirroring the SEPARATE
// guarantee. Crash immediately after a waypoint-advance transition.
//
//	Initial state:       core-single run advanced past WP0 to WP1 (current_index=1).
//	Injected failure:    Core process restart (store close/reopen, fresh Server).
//	Expected Safe State: recovery_required, Active=false, Authority=false,
//	                     current_index preserved at 1, previous state RUNNING.
//	MUST NOT happen:     any GOTO/HOLD on restart; a guessed continuation.
func TestCoreRestartGroupedProgressionSurvivesAsAccurateRecovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "grouped-progress.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first := newMissionServer()
	firstCmd := &apiMissionCommander{}
	attachPersistentSingleServer(first, firstStore, firstCmd, "grouped-progress-1")
	start, err := first.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: singlePersistentPlan("grouped-progress"), OperationId: "op-grouped-progress",
	})
	if err != nil || !start.Ok || firstCmd.calls != 1 {
		t.Fatalf("start grouped-progress: %+v err=%v calls=%d", start, err, firstCmd.calls)
	}
	first.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0)}) // arrive WP0 → advance WP1
	if first.mission.Snapshot().CurrentIndex != 1 || firstCmd.calls != 2 {
		t.Fatalf("precondition: advance to WP1 + next GOTO: idx=%d calls=%d",
			first.mission.Snapshot().CurrentIndex, firstCmd.calls)
	}
	firstStore.Close()

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(restarted, secondStore, cmd, "grouped-progress-2")
	restarted.restoreDurableMission()
	snap := restarted.mission.Snapshot()
	if !snap.RecoveryRequired || snap.Authority || snap.Active ||
		snap.CurrentIndex != 1 || snap.RecoveryPreviousState != mission.StateRunning {
		t.Fatalf("GROUPED progression recovery inaccurate: %+v", snap)
	}
	restarted.dispatchMissionAuthority()
	restarted.observeFrom([]*pb.Telemetry{telem(1, 14.001, 100.0)})
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("GROUPED restart recovery dispatched: %+v", cmd)
	}
}

// S11-D — recovery-clear DELETE boundary. When an operator clears an INCOMPATIBLE
// recovery, the durable record is deleted; a subsequent Core restart must find no
// mission and stay IDLE — never resurrect the retired run.
//
//	Initial state:       corrupt persisted record → visible incompatible recovery.
//	Injected failure:    operator CancelMission (clear) deletes the record, then Core
//	                     restarts.
//	Expected Safe State: IDLE, run_id 0, no recovery, no command.
//	MUST NOT happen:     the deleted mission reappearing as recovery or authority.
func TestRecoveryClearDeleteBoundaryStaysIdleAfterRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "clear-delete.db")
	st, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	engine := mission.NewEngine(nil)
	runID, _ := st.AllocateMissionRunID()
	if _, err := engine.StartOpWithRunID(mission.MissionPlan{
		PlanID: "clear-delete-plan", Mode: mission.ModeGrouped, Participants: []uint32{1},
		Routes: []mission.Route{{Points: []mission.Waypoint{{Seq: 0, Lat: 14, Lon: 100}}}},
	}, "clear-delete-op", runID); err != nil {
		t.Fatal(err)
	}
	record, _ := engine.DurableRecord("clear-delete-session")
	if err := st.SaveMissionRecord(*record); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`UPDATE mission_state SET record_json='{' WHERE singleton_id=1`); err != nil {
		t.Fatal(err)
	}
	st.Close()

	st2, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	recovered := newMissionServer()
	attachPersistentSingleServer(recovered, st2, &apiMissionCommander{}, "clear-delete-2")
	recovered.restoreDurableMission()
	if snap := recovered.snapshotWithPersistenceStatus(); !snap.RecoveryRequired ||
		snap.RecoveryCompatible {
		t.Fatalf("precondition: incompatible recovery expected: %+v", snap)
	}
	clear, err := recovered.CancelMission(context.Background(), &pb.CancelMissionRequest{
		RunId: runID, RequestId: "clear-incompatible",
	})
	if err != nil || !clear.Ok {
		t.Fatalf("recovery clear/delete: %+v err=%v", clear, err)
	}
	st2.Close()

	st3, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer st3.Close()
	final := newMissionServer()
	cmd := &apiMissionCommander{}
	attachPersistentSingleServer(final, st3, cmd, "clear-delete-3")
	final.restoreDurableMission()
	snap := final.mission.Snapshot()
	if snap.RecoveryRequired || snap.Active || snap.Authority ||
		snap.State != mission.StateIdle || snap.RunID != 0 {
		t.Fatalf("deleted recovery resurrected after restart: %+v", snap)
	}
	final.dispatchMissionAuthority()
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("post-delete restart dispatched: %+v", cmd)
	}
}

// V3-S09-C × S10 — a promoted leader and its exclusion set are durable EVIDENCE,
// never resumability. Restart after a succession must reconstruct exactly who was
// leading and who was excluded, and still command nothing.
//
//	Initial state:       SWARM_LEADER run, leader D1, followers D2/D3; operator takes
//	                     over D1 so D2 is promoted inside the same run.
//	Injected failure:    Core restart (store close/reopen, fresh Server).
//	Expected Safe State: recovery_required, Active=false, Authority=false,
//	                     current leader D2, excluded {1}, active {2,3}, same run_id.
//	MUST NOT happen:     any flight command; the excluded old leader auto-rejoining;
//	                     the original plan leader being restored as current leader.
func TestCoreRestartAfterSwarmSuccessionKeepsEvidenceWithoutRejoinOrCommand(t *testing.T) {
	path := filepath.Join(t.TempDir(), "swarm-succession.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first, coordinator, firstCmd := newSwarmTakeoverServer(t)
	first.missionStore = firstStore
	first.missionSessionID = "succession-session-1"

	operatorTakeover(first, 1)
	before := first.mission.Snapshot()
	if before.CurrentLeaderID != 2 || !before.Active || coordinator.rebinds != 1 {
		t.Fatalf("precondition: succession did not promote D2: %+v", before)
	}
	if err := first.persistMissionState(false); err != nil {
		t.Fatalf("persist succession: %v", err)
	}
	if firstCmd.calls != 0 || firstCmd.holdCalls != 0 {
		t.Fatalf("succession emitted a flight command: %+v", firstCmd)
	}
	if err := firstStore.Close(); err != nil {
		t.Fatal(err)
	}

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	restarted.mission.EnableSwarmLeaderAuthority()
	restarted.missionAuthority = true
	cmd := &apiMissionCommander{}
	restarted.missionExec = cmd
	restarted.missionStore = secondStore
	restarted.missionSessionID = "succession-session-2"
	restarted.restoreDurableMission()

	snap := restarted.mission.Snapshot()
	if !snap.RecoveryRequired || snap.Active || snap.Authority ||
		snap.State != mission.StateInterrupted || snap.RunID != before.RunID ||
		snap.CurrentIndex != before.CurrentIndex {
		t.Fatalf("succession restart is not command-inert recovery: %+v", snap)
	}
	if snap.CurrentLeaderID != 2 {
		t.Fatalf("restart lost the promoted leader (got D%d, plan leader is D1)", snap.CurrentLeaderID)
	}
	if len(snap.ExcludedParticipants) != 1 || snap.ExcludedParticipants[0] != 1 ||
		len(snap.ActiveParticipants) != 2 || snap.ActiveParticipants[0] != 2 ||
		snap.ActiveParticipants[1] != 3 {
		t.Fatalf("restart lost membership evidence: active=%v excluded=%v",
			snap.ActiveParticipants, snap.ExcludedParticipants)
	}

	// Recovery is evidence only: neither dispatch nor excluded-drone telemetry may
	// rejoin the run or produce a command.
	restarted.dispatchMissionAuthority()
	restarted.observeFrom([]*pb.Telemetry{telem(1, 14.0, 100.0), telem(2, 14.0, 100.0)})
	after := restarted.mission.Snapshot()
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("recovery emitted a flight command: %+v", cmd)
	}
	if len(after.ExcludedParticipants) != 1 || after.ExcludedParticipants[0] != 1 ||
		after.CurrentIndex != before.CurrentIndex || after.Active {
		t.Fatalf("excluded drone rejoined or run progressed during recovery: %+v", after)
	}
}

// V3-S09-C × S10 — crash DURING succession (membership committed in the mission
// domain, formation rebind not yet reconciled). A pending handoff cannot survive a
// restart: the formation it was waiting on is gone.
//
//	Expected Safe State: recovery_required, Active=false, Authority=false, zero
//	                     command, and no half-applied succession left pending.
func TestCoreRestartDuringSwarmSuccessionRecoversWithoutCommand(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mid-succession.db")
	firstStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	first, _, _ := newSwarmTakeoverServer(t)
	first.missionStore = firstStore
	first.missionSessionID = "mid-succession-1"

	transition, ok := first.mission.BeginSwarmOperatorTakeover(1, func(uint32) bool { return true })
	if !ok || transition.NewLeaderID != 2 {
		t.Fatalf("precondition: begin succession: %+v ok=%v", transition, ok)
	}
	// Core dies here: CompleteSwarmOperatorTakeover never runs.
	if err := first.persistMissionState(false); err != nil {
		t.Fatalf("persist mid-succession: %v", err)
	}
	if err := firstStore.Close(); err != nil {
		t.Fatal(err)
	}

	secondStore, err := corestore.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer secondStore.Close()
	restarted := newMissionServer()
	restarted.mission.EnableSwarmLeaderAuthority()
	restarted.missionAuthority = true
	cmd := &apiMissionCommander{}
	restarted.missionExec = cmd
	restarted.missionStore = secondStore
	restarted.missionSessionID = "mid-succession-2"
	restarted.restoreDurableMission()

	snap := restarted.snapshotWithPersistenceStatus()
	if !snap.RecoveryRequired || snap.Active || snap.Authority ||
		snap.State != mission.StateInterrupted {
		t.Fatalf("mid-succession restart is not command-inert recovery: %+v", snap)
	}
	if snap.SuccessionPending {
		t.Fatalf("a half-applied succession survived the restart as pending: %+v", snap)
	}
	restarted.dispatchMissionAuthority()
	restarted.observeFrom([]*pb.Telemetry{telem(2, 14.0, 100.0)})
	if cmd.calls != 0 || cmd.holdCalls != 0 {
		t.Fatalf("mid-succession recovery emitted a flight command: %+v", cmd)
	}
}
