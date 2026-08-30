package mission

import (
	"errors"
	"testing"
)

// V3-S09-B SEPARATE adversarial engine tests.  SEPARATE flies each drone to its
// own route's RAW waypoint (no formation offset), advancing independently.

func sepRoute() []Waypoint {
	return []Waypoint{
		{Seq: 0, Lat: 14.0000, Lon: 100.0000},
		{Seq: 1, Lat: 14.0010, Lon: 100.0000},
	}
}

func separateAuthPlan(participants ...uint32) MissionPlan {
	routes := make([]Route, 0, len(participants))
	alts := make(map[uint32]float64, len(participants))
	for _, id := range participants {
		routes = append(routes, Route{DroneID: id, Points: sepRoute()})
		alts[id] = 30 + 3*float64(id)
	}
	return MissionPlan{
		PlanID: "sep1", Mode: ModeSeparate, Participants: participants,
		Routes: routes, ParticipantAltitudes: alts,
	}
}

func startSeparateAuth(t *testing.T, e *Engine, participants ...uint32) uint64 {
	t.Helper()
	e.EnableSeparateAuthority()
	id, err := e.StartOp(separateAuthPlan(participants...), "sep-op")
	if err != nil {
		t.Fatalf("StartOp separate: %v", err)
	}
	return id
}

func claimSep(t *testing.T, e *Engine) []GotoIntent {
	t.Helper()
	intents, ok, err := e.ClaimAuthoritySeparateGotos()
	if err != nil {
		t.Fatalf("ClaimAuthoritySeparateGotos err: %v", err)
	}
	if !ok {
		t.Fatal("expected separate claim")
	}
	return intents
}

// per-drone raw waypoint + per-drone altitude + all participants dispatched once.
func TestSeparateInitialClaimRawWaypointsPerDroneAlt(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2, 3)
	intents := claimSep(t, e)
	if len(intents) != 3 {
		t.Fatalf("want 3 initial GOTOs, got %+v", intents)
	}
	wp := sepRoute()[0]
	for _, in := range intents {
		if in.Lat != wp.Lat || in.Lon != wp.Lon {
			t.Fatalf("SEPARATE must target the raw route waypoint (no offset): D%d %+v", in.DroneID, in)
		}
		if in.Alt != 30+3*float64(in.DroneID) {
			t.Fatalf("D%d alt = %v, want per-drone %v", in.DroneID, in.Alt, 30+3*float64(in.DroneID))
		}
		if in.Index != 0 {
			t.Fatalf("initial claim index = %d, want 0", in.Index)
		}
	}
	// no duplicate claim for the same drone/index.
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
		t.Fatal("re-claim before any advance must emit nothing")
	}
}

// independent progression: one drone advances and gets its next GOTO alone.
func TestSeparateIndependentProgression(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2, 3)
	_ = claimSep(t, e)
	r := sepRoute()
	// Only D1 arrives at its WP0 -> D1 advances; D2/D3 stay.
	e.Observe(1, r[0].Lat, r[0].Lon, 0)
	next := claimSep(t, e)
	if len(next) != 1 || next[0].DroneID != 1 || next[0].Index != 1 {
		t.Fatalf("only D1 should get its WP1 GOTO: %+v", next)
	}
	if s := e.Snapshot(); s.SepIndex[1] != 1 || s.SepIndex[2] != 0 || s.State != StateRunning {
		t.Fatalf("D1 advanced alone, others unchanged: %+v", s)
	}
}

// one drone finishing while others continue; run completes when all finish.
func TestSeparateOneFinishesOthersContinue(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2)
	_ = claimSep(t, e)
	r := sepRoute()
	// D1 completes both waypoints.
	e.Observe(1, r[0].Lat, r[0].Lon, 0)
	_ = claimSep(t, e) // D1 WP1
	e.Observe(1, r[1].Lat, r[1].Lon, 0)
	if s := e.Snapshot(); s.State != StateRunning {
		t.Fatalf("run must stay RUNNING while D2 continues: %+v", s)
	}
	// D2 completes.
	e.Observe(2, r[0].Lat, r[0].Lon, 0)
	_ = claimSep(t, e) // D2 WP1
	e.Observe(2, r[1].Lat, r[1].Lon, 0)
	if s := e.Snapshot(); s.State != StateCompleted || s.Active {
		t.Fatalf("all routes finished -> COMPLETED: %+v", s)
	}
}

// a stale/disconnected participant holds only itself; others still progress but the
// run does not complete until the stuck drone finishes.
func TestSeparateStaleParticipantBlocksOnlyCompletion(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2)
	_ = claimSep(t, e)
	r := sepRoute()
	// D1 finishes; D2 never sends telemetry (disconnected).
	e.Observe(1, r[0].Lat, r[0].Lon, 0)
	_ = claimSep(t, e)
	e.Observe(1, r[1].Lat, r[1].Lon, 0)
	if s := e.Snapshot(); s.State != StateRunning {
		t.Fatalf("run must wait for the stuck D2: %+v", s)
	}
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
		t.Fatal("no new GOTO while D1 done and D2 stuck at claimed index")
	}
}

// A claimed SEPARATE index is not blindly retried while its future dispatcher
// reports/recovers a participant send failure. Structured rejection observability
// is implemented for S09-A GROUPED; the SEPARATE dispatcher remains pre-flip.
func TestSeparateClaimedIndexDoesNotBlindRetry(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2, 3)
	_ = claimSep(t, e)
	if s := e.Snapshot(); s.State != StateRunning || !s.Active {
		t.Fatalf("claim must keep run active: %+v", s)
	}
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
		t.Fatal("no blind retry: each participant index stays claimed")
	}
}

// operator takeover / failsafe make the run terminal and suppress further claims.
func TestSeparateTakeoverAndFailsafeTerminal(t *testing.T) {
	for _, tc := range []struct {
		name string
		stop func(*Engine, uint64)
	}{
		{"cancel", func(e *Engine, id uint64) { _ = e.Cancel(id) }},
		{"battery", func(e *Engine, _ uint64) { e.Interrupt(2, "battery", "") }},
		{"link", func(e *Engine, _ uint64) { e.Interrupt(1, "link", "") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			id := startSeparateAuth(t, e, 1, 2, 3)
			_ = claimSep(t, e)
			tc.stop(e, id)
			if s := e.Snapshot(); s.Active {
				t.Fatalf("%s must make the run terminal: %+v", tc.name, s)
			}
			r := sepRoute()
			e.Observe(1, r[0].Lat, r[0].Lon, 0) // late telemetry
			if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
				t.Fatalf("%s: terminal run must emit no claim", tc.name)
			}
		})
	}
}

func TestSeparateNonParticipantFailsafeIgnored(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2)
	_ = claimSep(t, e)
	if e.Interrupt(99, "battery", "") {
		t.Fatal("non-participant failsafe must report no interruption")
	}
	if e.Snapshot().State != StateRunning {
		t.Fatal("non-participant failsafe must not interrupt SEPARATE run")
	}
}

// duplicate Start (UI restart) returns the same run and does not re-dispatch.
func TestSeparateDuplicateStartNoDuplicateAuthority(t *testing.T) {
	e := NewEngine(nil)
	e.EnableSeparateAuthority()
	p := separateAuthPlan(1, 2, 3)
	id1, err := e.StartOp(p, "sep-op")
	if err != nil {
		t.Fatal(err)
	}
	_ = claimSep(t, e)
	id2, err := e.StartOp(p, "sep-op")
	if err != nil || id1 != id2 {
		t.Fatalf("duplicate Start must return same run: %d %d %v", id1, id2, err)
	}
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); ok {
		t.Fatal("duplicate Start must not re-dispatch already-claimed indices")
	}
}

// no dual authority: single-drone + grouped claims are inert in SEPARATE mode.
func TestSeparateNoDualAuthority(t *testing.T) {
	e := NewEngine(nil)
	startSeparateAuth(t, e, 1, 2, 3)
	if _, ok, err := e.ClaimAuthorityGoto(); ok || err != nil {
		t.Fatalf("single-drone claim must be inert in SEPARATE mode: ok=%v err=%v", ok, err)
	}
	if _, ok, err := e.ClaimAuthorityGroupedGotos(); ok || err != nil {
		t.Fatalf("grouped claim must be inert in SEPARATE mode: ok=%v err=%v", ok, err)
	}
	if _, ok, _ := e.ClaimAuthoritySeparateGotos(); !ok {
		t.Fatal("only the SEPARATE claim should emit")
	}
}

// unsupported plans stay Python-owned (rejected at Start under S09-B).
func TestSeparateRejectsUnsupportedPlans(t *testing.T) {
	dupRoutePlan := func() MissionPlan {
		p := separateAuthPlan(1)
		// participants [1,1] with two D1 routes -> single unique drone.
		p.Participants = []uint32{1, 1}
		p.Routes = []Route{{DroneID: 1, Points: sepRoute()}, {DroneID: 1, Points: sepRoute()}}
		return p
	}
	tests := []struct {
		name string
		plan MissionPlan
	}{
		{"single participant", separateAuthPlan(1)},
		{"duplicate-only single unique", dupRoutePlan()},
		{"wait deferred", func() MissionPlan {
			p := separateAuthPlan(1, 2)
			p.Routes[0].Points[0].WaitSeconds = 60
			return p
		}()},
		{"action deferred", func() MissionPlan {
			p := separateAuthPlan(1, 2)
			p.Routes[1].Points[1].Action = ActionServoB
			return p
		}()},
		{"rtl_after deferred", func() MissionPlan {
			p := separateAuthPlan(1, 2)
			p.RtlAfter = true
			return p
		}()},
		{"grouped mode rejected", groupedMultiPlan(gmRoute, 1, 2)},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			e := NewEngine(nil)
			e.EnableSeparateAuthority()
			if _, err := e.StartOp(tc.plan, "op"); !errors.Is(err, ErrAuthorityUnsupported) &&
				!errors.Is(err, ErrRouteMismatch) {
				t.Fatalf("want unsupported/route mismatch, got %v", err)
			}
			if e.Snapshot().Active {
				t.Fatal("unsupported plan must create no run")
			}
		})
	}
}
