package mission

import (
	"errors"
	"testing"
)

func wp(seq int, lat, lon float64) Waypoint { return Waypoint{Seq: seq, Lat: lat, Lon: lon} }

func TestValidateGroupedOK(t *testing.T) {
	p := MissionPlan{
		PlanID:       "p1",
		Mode:         ModeGrouped,
		Participants: []uint32{1, 2},
		Routes:       []Route{{DroneID: 0, Points: []Waypoint{wp(0, 14, 100), wp(1, 14.001, 100)}}},
	}
	if err := p.Validate(); err != nil {
		t.Fatalf("valid GROUPED plan rejected: %v", err)
	}
}

func TestValidateRejectsEmpties(t *testing.T) {
	cases := []struct {
		name string
		p    MissionPlan
		want error
	}{
		{"no plan id", MissionPlan{Mode: ModeGrouped, Participants: []uint32{1}, Routes: []Route{{Points: []Waypoint{wp(0, 1, 1)}}}}, ErrNoPlanID},
		{"no participants", MissionPlan{PlanID: "p", Mode: ModeGrouped, Routes: []Route{{Points: []Waypoint{wp(0, 1, 1)}}}}, ErrNoParticipants},
		{"no route", MissionPlan{PlanID: "p", Mode: ModeGrouped, Participants: []uint32{1}}, ErrNoRoute},
		{"empty route", MissionPlan{PlanID: "p", Mode: ModeGrouped, Participants: []uint32{1}, Routes: []Route{{Points: nil}}}, ErrEmptyRoute},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if err := c.p.Validate(); !errors.Is(err, c.want) {
				t.Fatalf("want %v, got %v", c.want, err)
			}
		})
	}
}

func TestValidateBadWait(t *testing.T) {
	p := MissionPlan{
		PlanID: "p", Mode: ModeGrouped, Participants: []uint32{1},
		Routes: []Route{{Points: []Waypoint{{Seq: 0, Lat: 1, Lon: 1, WaitSeconds: 601}}}},
	}
	if err := p.Validate(); !errors.Is(err, ErrBadWait) {
		t.Fatalf("want ErrBadWait, got %v", err)
	}
}

func TestValidateSeparateRouteCount(t *testing.T) {
	// SEPARATE needs one route per participant, keyed by participant id.
	p := MissionPlan{
		PlanID: "p", Mode: ModeSeparate, Participants: []uint32{1, 2},
		Routes: []Route{{DroneID: 1, Points: []Waypoint{wp(0, 1, 1)}}},
	}
	if err := p.Validate(); !errors.Is(err, ErrRouteMismatch) {
		t.Fatalf("want ErrRouteMismatch (route count), got %v", err)
	}
}

func TestValidateSeparateUnknownDrone(t *testing.T) {
	p := MissionPlan{
		PlanID: "p", Mode: ModeSeparate, Participants: []uint32{1, 2},
		Routes: []Route{
			{DroneID: 1, Points: []Waypoint{wp(0, 1, 1)}},
			{DroneID: 9, Points: []Waypoint{wp(0, 1, 1)}},
		},
	}
	if err := p.Validate(); !errors.Is(err, ErrRouteMismatch) {
		t.Fatalf("want ErrRouteMismatch (unknown drone), got %v", err)
	}
}

func TestValidateSwarmLeaderNotParticipant(t *testing.T) {
	p := MissionPlan{
		PlanID: "p", Mode: ModeSwarmLeader, Participants: []uint32{1, 2}, LeaderID: 9,
		Routes: []Route{{Points: []Waypoint{wp(0, 1, 1)}}},
	}
	if err := p.Validate(); !errors.Is(err, ErrRouteMismatch) {
		t.Fatalf("want ErrRouteMismatch (leader not participant), got %v", err)
	}
}

func TestArrivalRadiusDefault(t *testing.T) {
	p := MissionPlan{ArrivalRadiusM: 0}
	if got := p.arrivalRadius(); got != DefaultArrivalRadiusM {
		t.Fatalf("default radius = %v, want %v", got, DefaultArrivalRadiusM)
	}
	p.ArrivalRadiusM = 5
	if got := p.arrivalRadius(); got != 5 {
		t.Fatalf("radius = %v, want 5", got)
	}
}
