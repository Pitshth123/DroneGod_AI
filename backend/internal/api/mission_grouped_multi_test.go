package api

import (
	"context"
	"math"
	"sync"
	"testing"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/safety"
)

// V3-S09-A API-level tests.  The multi-drone GROUPED per-drone dispatcher is wired
// behind the engine's grouped-multi opt-in, which NO live profile token enables
// (see the gate test) — so production never runs it.  These tests enable the
// opt-in in-process and drive the dispatcher through a deterministic scheduler so
// the staggered/cancellable/guarded takeover contract is proven without real waits.

func pbGroupedMultiPlan(planID string, participants ...uint32) *pb.MissionPlan {
	if len(participants) == 0 {
		participants = []uint32{1, 2, 3}
	}
	return &pb.MissionPlan{
		PlanId:       planID,
		Mode:         pb.MissionMode_MISSION_MODE_GROUPED,
		Participants: participants,
		Routes: []*pb.MissionRoute{{DroneId: 0, Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: 14.0050, Lon: 100.0050},
			{Seq: 1, Lat: 14.0100, Lon: 100.0100},
		}}},
	}
}

// gmApiStart mirrors the mission-package gmStart triangle so front-of-travel order
// is the deterministic [3,1,2].
var gmApiStart = map[uint32][2]float64{
	1: {14.0000, 100.0000},
	2: {14.0000, 100.0010},
	3: {14.0010, 100.0005},
}

// schedCapture captures scheduled per-drone sends instead of really sleeping.
type schedCapture struct {
	mu    sync.Mutex
	delay []time.Duration
	fire  []func()
}

func (c *schedCapture) schedule(d time.Duration, fn func()) {
	c.mu.Lock()
	c.delay = append(c.delay, d)
	c.fire = append(c.fire, fn)
	c.mu.Unlock()
}

func (c *schedCapture) fireAll() {
	c.mu.Lock()
	fns := append([]func(){}, c.fire...)
	c.mu.Unlock()
	for _, fn := range fns {
		fn()
	}
}

func (c *schedCapture) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.fire)
}

// newGroupedMultiServer wires a state-only server into grouped-multi authority with
// a deterministic capture scheduler and pre-seeded participant positions.
func newGroupedMultiServer(t *testing.T, participants ...uint32) (*Server, *apiMissionCommander, *schedCapture) {
	t.Helper()
	s := newMissionServer()
	cmd := &apiMissionCommander{}
	cap := &schedCapture{}
	s.mission.EnableGroupedMultiAuthority()
	s.missionAuthority = true
	s.missionExec = cmd
	s.missionSchedule = cap.schedule
	if len(participants) == 0 {
		participants = []uint32{1, 2, 3}
	}
	seed := map[uint32][2]float64{}
	for _, id := range participants {
		if p, ok := gmApiStart[id]; ok {
			seed[id] = p
		}
	}
	// Positions are seeded at dispatch time (when the run exists) via this provider,
	// standing in for the fleet telemetry snapshot a real server would read.
	s.missionPosProvider = func() map[uint32][2]float64 { return seed }
	return s, cmd, cap
}

func startGroupedMultiMission(t *testing.T, s *Server, planID string, participants ...uint32) uint64 {
	t.Helper()
	resp, err := s.StartMission(context.Background(), &pb.StartMissionRequest{
		Plan: pbGroupedMultiPlan(planID, participants...), OperationId: "op-gm",
	})
	if err != nil || !resp.Ok || resp.RunId == 0 || !resp.AuthorityActive {
		t.Fatalf("grouped-multi StartMission = %+v err=%v", resp, err)
	}
	return resp.RunId
}

// ── staggered, front-of-travel, per-drone dispatch through command.Service ──

func TestGroupedMultiApiStaggeredPerDroneDispatch(t *testing.T) {
	s, cmd, cap := newGroupedMultiServer(t)
	startGroupedMultiMission(t, s, "gm-dispatch")

	if cap.count() != 3 {
		t.Fatalf("expected 3 scheduled per-drone sends, got %d", cap.count())
	}
	// ~150 ms intentional launch spacing, front-of-travel first.
	wantDelays := []time.Duration{0, missionGroupStagger, 2 * missionGroupStagger}
	for i, d := range cap.delay {
		if d != wantDelays[i] {
			t.Fatalf("send %d delay = %v, want %v", i, d, wantDelays[i])
		}
	}
	cap.fireAll()
	if cmd.calls != 3 {
		t.Fatalf("all three GOTOs must reach command.Service, got %d", cmd.calls)
	}
	if len(cmd.gotoIDs) != 3 || cmd.gotoIDs[0] != 3 || cmd.gotoIDs[1] != 1 || cmd.gotoIDs[2] != 2 {
		t.Fatalf("front-of-travel order want [3 1 2], got %v", cmd.gotoIDs)
	}
	// Core owns navigation for every participant.
	s.missionDispatchMu.Lock()
	for _, id := range []uint32{1, 2, 3} {
		if !s.missionOwnsDroneLocked(id) {
			s.missionDispatchMu.Unlock()
			t.Fatalf("Core must own participant D%d", id)
		}
	}
	s.missionDispatchMu.Unlock()
}

// ── real takeover primitive: pending per-drone GOTOs cannot write afterward ──

func TestGroupedMultiApiOperatorTakeoverCancelsPendingSends(t *testing.T) {
	// The primitive every operator takeover (HOLD/LAND/RTL/DISARM/STOPALL/KILL)
	// funnels through — exercised directly on a real pending group dispatch.
	for _, target := range []uint32{1, 2, 3} {
		s, cmd, cap := newGroupedMultiServer(t)
		startGroupedMultiMission(t, s, "gm-takeover")
		if cap.count() != 3 {
			t.Fatalf("precondition: 3 pending sends, got %d", cap.count())
		}
		s.missionDispatchMu.Lock()
		took := s.cancelMissionForOperatorTargetsLocked([]uint32{target})
		s.missionDispatchMu.Unlock()
		if !took {
			t.Fatalf("takeover on participant D%d must cancel the run", target)
		}
		if st := s.mission.Snapshot(); st.Active || st.State != mission.StateCancelled {
			t.Fatalf("run must be terminal after takeover: %+v", st)
		}
		// Fire the pending sends AFTER the takeover: none may write to the FC.
		cap.fireAll()
		if cmd.calls != 0 {
			t.Fatalf("D%d takeover: pending mission GOTOs must not write, got %d", target, cmd.calls)
		}
		// No next-WP dispatch after a terminal run.
		s.dispatchMissionAuthority()
		if cap.count() != 3 {
			t.Fatalf("terminal run must not schedule more sends (had %d)", cap.count())
		}
	}
}

// CancelMission RPC (self-contained, no command.Service) cancels pending sends too.
func TestGroupedMultiApiCancelMissionRpcCancelsPendingSends(t *testing.T) {
	s, cmd, cap := newGroupedMultiServer(t)
	runID := startGroupedMultiMission(t, s, "gm-cancel")
	if _, err := s.CancelMission(context.Background(), &pb.CancelMissionRequest{RunId: runID}); err != nil {
		t.Fatal(err)
	}
	if s.mission.Snapshot().Active {
		t.Fatal("CancelMission must make the run terminal")
	}
	cap.fireAll()
	if cmd.calls != 0 {
		t.Fatalf("cancelled run: pending GOTOs must not write, got %d", cmd.calls)
	}
}

// ── in-flight (already executing) send is cancelled/guarded by takeover ─────

func TestGroupedMultiApiInFlightSendCancelledByTakeover(t *testing.T) {
	s, cmd, cap := newGroupedMultiServer(t)
	cmd.block = make(chan struct{})
	cmd.started = make(chan context.Context, 1)
	startGroupedMultiMission(t, s, "gm-inflight")

	// Fire the first send; it enters command.Service and blocks (in-flight).
	c := cap
	go func() {
		c.mu.Lock()
		fn := c.fire[0]
		c.mu.Unlock()
		fn()
	}()
	select {
	case <-cmd.started:
	case <-time.After(2 * time.Second):
		t.Fatal("first send did not start")
	}
	// Operator takeover while it is in-flight.
	s.missionDispatchMu.Lock()
	s.cancelMissionForOperatorTargetsLocked([]uint32{1})
	s.missionDispatchMu.Unlock()
	// The in-flight send observes context cancellation and never commits a write.
	close(cmd.block)
	time.Sleep(20 * time.Millisecond)
	if cmd.calls != 0 {
		t.Fatalf("in-flight stale send must be cancelled before writing, got %d", cmd.calls)
	}
	if s.mission.Snapshot().Active {
		t.Fatal("run must be terminal after takeover")
	}
}

// ── blocker 5: a NON-participant battery/link ALARM must not tear down an
//    in-flight participant mission send. ──────────────────────────────────────

func TestGroupedMultiApiNonParticipantFailsafeDoesNotCancelInFlight(t *testing.T) {
	s, cmd, cap := newGroupedMultiServer(t)
	cmd.block = make(chan struct{})
	cmd.started = make(chan context.Context, 1)
	startGroupedMultiMission(t, s, "gm-nonpart-fs")

	go func() {
		c := cap
		c.mu.Lock()
		fn := c.fire[0]
		c.mu.Unlock()
		fn()
	}()
	select {
	case <-cmd.started:
	case <-time.After(2 * time.Second):
		t.Fatal("first send did not start")
	}
	// Non-participant (D99) battery ALARM: Interrupt is ignored, so the in-flight
	// participant send must NOT be cancelled.
	s.handleMissionSafetyEvent(&pb.Event{
		Level: pb.EventLevel_EVENT_LEVEL_ALARM, Category: "battery", DroneId: 99,
		Message: "battery critical (non-participant)",
	})
	if !s.mission.Snapshot().Active {
		t.Fatal("non-participant failsafe must not interrupt the run")
	}
	// Let the in-flight send finish: it commits normally (was never cancelled).
	close(cmd.block)
	time.Sleep(20 * time.Millisecond)
	if cmd.calls != 1 {
		t.Fatalf("in-flight send must complete (non-participant failsafe ignored), got %d", cmd.calls)
	}
}

// participant battery/link failsafe interrupts + cancels pending sends.
func TestGroupedMultiApiParticipantFailsafeInterruptsAndCancels(t *testing.T) {
	for _, cat := range []string{"battery", "link"} {
		s, cmd, cap := newGroupedMultiServer(t)
		startGroupedMultiMission(t, s, "gm-fs")
		s.handleMissionSafetyEvent(&pb.Event{
			Level: pb.EventLevel_EVENT_LEVEL_ALARM, Category: cat, DroneId: 2,
			Message: cat + " critical",
		})
		if s.mission.Snapshot().Active {
			t.Fatalf("%s failsafe must interrupt the run", cat)
		}
		cap.fireAll()
		if cmd.calls != 0 {
			t.Fatalf("%s: pending sends must not write after failsafe, got %d", cat, cmd.calls)
		}
	}
}

// non-participant operator command must not cancel the run.
func TestGroupedMultiApiNonParticipantDoesNotCancel(t *testing.T) {
	s, _, _ := newGroupedMultiServer(t)
	startGroupedMultiMission(t, s, "gm-nonpart")
	s.missionDispatchMu.Lock()
	took := s.cancelMissionForOperatorTargetsLocked([]uint32{99})
	s.missionDispatchMu.Unlock()
	if took {
		t.Fatal("non-participant takeover must not cancel the multi-drone run")
	}
	if !s.mission.Snapshot().Active {
		t.Fatal("run must remain active after an unrelated operator command")
	}
}

// ── partial participant GOTO rejection is best-effort: run stays active ─────

func TestGroupedMultiApiSeedPathUsesAuthoritativeSampleAge(t *testing.T) {
	s, _, cap := newGroupedMultiServer(t)
	s.missionPosProvider = nil
	s.missionPosSampleProvider = func() map[uint32]mission.PositionSample {
		return map[uint32]mission.PositionSample{
			1: {Lat: gmApiStart[1][0], Lon: gmApiStart[1][1], Age: time.Second, Valid: true},
			2: {Lat: gmApiStart[2][0], Lon: gmApiStart[2][1], Age: 2 * time.Second, Valid: true},
			3: {Lat: gmApiStart[3][0], Lon: gmApiStart[3][1], Age: 30 * time.Second, Valid: true},
		}
	}
	startGroupedMultiMission(t, s, "gm-production-sample-age")
	if cap.count() != 3 {
		t.Fatalf("expected one scheduled send per participant, got %d", cap.count())
	}

	s.missionDispatchMu.Lock()
	d1 := s.missionGroupSends[1]
	d2 := s.missionGroupSends[2]
	d3 := s.missionGroupSends[3]
	s.missionDispatchMu.Unlock()
	if d1 == nil || d2 == nil || d3 == nil {
		t.Fatalf("missing scheduled group sends: D1=%v D2=%v D3=%v", d1 != nil, d2 != nil, d3 != nil)
	}
	wp := pbGroupedMultiPlan("raw").Routes[0].Points[0]
	if d1.lat == wp.Lat && d1.lon == wp.Lon {
		t.Fatal("fresh D1 must use a formation-offset target")
	}
	if d2.lat == wp.Lat && d2.lon == wp.Lon {
		t.Fatal("fresh D2 must use a formation-offset target")
	}
	if d3.lat != wp.Lat || d3.lon != wp.Lon {
		t.Fatalf("stale D3 must use Legacy raw-waypoint fallback, got %.7f,%.7f", d3.lat, d3.lon)
	}
}

func TestMissionPositionSampleUsesNavigationSpecificFreshness(t *testing.T) {
	fresh := missionPositionSampleFromSafety(safety.DroneState{
		Lat: 14, Lon: 100, GpsFix: 3,
		TelemetryAgeSec: 0.1, PositionAgeSec: 1.0, GpsAgeSec: 1.25,
	})
	if !fresh.Valid || fresh.Age != 1250*time.Millisecond {
		t.Fatalf("fresh navigation evidence conversion: %+v", fresh)
	}
	// A fresh heartbeat/other MAVLink packet must not refresh old cached position.
	staleNavFreshHeartbeat := missionPositionSampleFromSafety(safety.DroneState{
		Lat: 14, Lon: 100, GpsFix: 3,
		TelemetryAgeSec: 0.05, PositionAgeSec: 30, GpsAgeSec: 2,
	})
	if !staleNavFreshHeartbeat.Valid || staleNavFreshHeartbeat.Age != 30*time.Second {
		t.Fatalf("fresh heartbeat must not hide stale position age: %+v", staleNavFreshHeartbeat)
	}
	// GPS validity is only as fresh as the older of position/fix evidence.
	staleFix := missionPositionSampleFromSafety(safety.DroneState{
		Lat: 14, Lon: 100, GpsFix: 3,
		TelemetryAgeSec: 0.05, PositionAgeSec: 1, GpsAgeSec: 40,
	})
	if !staleFix.Valid || staleFix.Age != 40*time.Second {
		t.Fatalf("stale GPS fix age must dominate navigation freshness: %+v", staleFix)
	}
	for name, st := range map[string]safety.DroneState{
		"zero position":          {Lat: 0, Lon: 0, GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: 1},
		"NaN latitude":           {Lat: math.NaN(), Lon: 100, GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: 1},
		"Inf longitude":          {Lat: 14, Lon: math.Inf(1), GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: 1},
		"latitude out of range":  {Lat: 90.001, Lon: 100, GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: 1},
		"longitude out of range": {Lat: 14, Lon: -180.001, GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: 1},
		"no GPS fix":             {Lat: 14, Lon: 100, GpsFix: 2, PositionAgeSec: 1, GpsAgeSec: 1},
		"no position":            {Lat: 14, Lon: 100, GpsFix: 3, PositionAgeSec: -1, GpsAgeSec: 1},
		"no GPS sample":          {Lat: 14, Lon: 100, GpsFix: 3, PositionAgeSec: 1, GpsAgeSec: -1},
	} {
		if got := missionPositionSampleFromSafety(st); got.Valid {
			t.Fatalf("%s must be unavailable: %+v", name, got)
		}
	}
}

func TestGroupedMultiApiParticipantRejectDoesNotFailRun(t *testing.T) {
	s, cmd, cap := newGroupedMultiServer(t)
	cmd.perDroneErr = map[uint32]error{2: context.DeadlineExceeded}
	runID := startGroupedMultiMission(t, s, "gm-reject")
	cap.fireAll() // D2's GOTO is rejected; D1/D3 succeed
	if st := s.mission.Snapshot(); !st.Active || st.State != mission.StateRunning {
		t.Fatalf("one participant reject must NOT fail the run: %+v", st)
	}
	if cmd.calls != 2 {
		t.Fatalf("the two accepted GOTOs should still send, got %d", cmd.calls)
	}
	if rej := s.mission.GroupRejections(); rej[2] == "" {
		t.Fatalf("D2 rejection must be recorded/observable, got %v", rej)
	}
	protoState := snapshotToProto(s.mission.Snapshot())
	if len(protoState.GetParticipantRejections()) != 1 {
		t.Fatalf("operator API must expose one structured rejection: %+v", protoState)
	}
	rej := protoState.GetParticipantRejections()[0]
	if rej.GetRunId() != runID || rej.GetWaypointIndex() != 0 ||
		rej.GetDroneId() != 2 || rej.GetReason() == "" {
		t.Fatalf("bad structured rejection identity: %+v", rej)
	}
}
