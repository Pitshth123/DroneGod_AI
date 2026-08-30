package mission

import (
	"math"
	"testing"
)

func conflictPlan(alt1, alt2 float64) MissionPlan {
	return MissionPlan{
		PlanID: "conflict", Mode: ModeSeparate, Participants: []uint32{1, 2},
		ParticipantAltitudes: map[uint32]float64{1: alt1, 2: alt2},
		Routes: []Route{
			{DroneID: 1, Points: []Waypoint{{Seq: 0, Lat: 14.0000, Lon: 100.0000}, {Seq: 1, Lat: 14.0100, Lon: 100.0100}}},
			{DroneID: 2, Points: []Waypoint{{Seq: 0, Lat: 14.0000, Lon: 100.0100}, {Seq: 1, Lat: 14.0100, Lon: 100.0000}}},
		},
	}
}

func TestSeparateRouteConflictCrossingSameAltitudeRejected(t *testing.T) {
	p := conflictPlan(20, 20)
	conflicts, err := p.SeparateRouteConflicts(nil, SeparateAltSeparationM, SeparateMinDistanceM)
	if err != nil {
		t.Fatal(err)
	}
	if len(conflicts) != 1 || conflicts[0].A != 1 || conflicts[0].B != 2 || conflicts[0].DistM != 0 {
		t.Fatalf("crossing same-alt routes must conflict: %+v", conflicts)
	}
	if err := p.ValidateAuthoritySeparate(); err == nil {
		t.Fatal("Core SEPARATE authority must reject crossing same-alt routes")
	}
}

func TestSeparateRouteConflictCrossingDifferentAltitudeAllowed(t *testing.T) {
	p := conflictPlan(20, 23)
	conflicts, err := p.SeparateRouteConflicts(nil, SeparateAltSeparationM, SeparateMinDistanceM)
	if err != nil {
		t.Fatal(err)
	}
	if len(conflicts) != 0 {
		t.Fatalf("altitude gap >2m should match Legacy no-conflict rule: %+v", conflicts)
	}
}

func TestSeparateConflictThresholdBoundariesMatchLegacy(t *testing.T) {
	mk := func(distanceM, altGap float64) MissionPlan {
		lat0 := 14.0
		dLat := distanceM / metersPerDegreeLat
		return MissionPlan{
			PlanID: "boundary", Mode: ModeSeparate, Participants: []uint32{1, 2},
			ParticipantAltitudes: map[uint32]float64{1: 20, 2: 20 + altGap},
			Routes: []Route{
				{DroneID: 1, Points: []Waypoint{{Seq: 0, Lat: lat0, Lon: 100}}},
				{DroneID: 2, Points: []Waypoint{{Seq: 0, Lat: lat0 + dLat, Lon: 100}}},
			},
		}
	}
	// Legacy condition is dist < 6m, not <=. Using the same metres/degree
	// conversion makes the exact constructed 6m single-point case deterministic.
	p := mk(6.0, 2.0)
	if got, err := p.SeparateRouteConflicts(nil, 2.0, 6.0); err != nil || len(got) != 0 {
		t.Fatalf("exact 6m must be allowed: conflicts=%+v err=%v", got, err)
	}
	p = mk(5.9, 2.0)
	if got, err := p.SeparateRouteConflicts(nil, 2.0, 6.0); err != nil || len(got) != 1 {
		t.Fatalf("<6m at exact 2m altitude gap must conflict: conflicts=%+v err=%v", got, err)
	}
	p = mk(5.9, 2.01)
	if got, err := p.SeparateRouteConflicts(nil, 2.0, 6.0); err != nil || len(got) != 0 {
		t.Fatalf(">2m altitude gap must be allowed: conflicts=%+v err=%v", got, err)
	}
}

func TestSeparateRouteConflictRejectsSuppliedInvalidStart(t *testing.T) {
	p := conflictPlan(20, 23)
	for name, invalid := range map[string][2]float64{
		"zero":          {0, 0},
		"NaN latitude":  {math.NaN(), 100},
		"Inf longitude": {14, math.Inf(1)},
		"latitude":      {90.001, 100},
		"longitude":     {14, -180.001},
	} {
		t.Run(name, func(t *testing.T) {
			starts := map[uint32][2]float64{
				1: invalid,
				2: {14.0000, 100.0200},
			}
			if _, err := p.SeparateRouteConflicts(
				starts, SeparateAltSeparationM, SeparateMinDistanceM); err == nil {
				t.Fatal("supplied invalid start must fail closed, not be omitted from geometry")
			}
		})
	}
}

func TestSeparateRouteConflictIncludesFreshStartLeg(t *testing.T) {
	p := MissionPlan{
		PlanID: "start-leg", Mode: ModeSeparate, Participants: []uint32{1, 2},
		ParticipantAltitudes: map[uint32]float64{1: 20, 2: 20},
		Routes: []Route{
			{DroneID: 1, Points: []Waypoint{{Seq: 0, Lat: 14.0100, Lon: 100.0000}}},
			{DroneID: 2, Points: []Waypoint{{Seq: 0, Lat: 14.0100, Lon: 100.0100}}},
		},
	}
	// Planned points themselves are far apart. Current positions are swapped, so
	// the two initial legs cross before WP1 — exactly the Legacy start-position case.
	starts := map[uint32][2]float64{
		1: {14.0000, 100.0100},
		2: {14.0000, 100.0000},
	}
	conflicts, err := p.SeparateRouteConflicts(starts, SeparateAltSeparationM, SeparateMinDistanceM)
	if err != nil {
		t.Fatal(err)
	}
	if len(conflicts) != 1 || conflicts[0].DistM != 0 {
		t.Fatalf("crossing start->WP1 legs must be blocked: %+v", conflicts)
	}
}
