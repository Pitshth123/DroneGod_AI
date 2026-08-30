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

func TestValidateSeparateRequiresExactParticipantRouteBijection(t *testing.T) {
	point := []Waypoint{wp(0, 1, 1)}
	cases := []struct {
		name         string
		participants []uint32
		routes       []Route
		wantErr      bool
	}{
		{"duplicate D1 missing D2", []uint32{1, 2}, []Route{{DroneID: 1, Points: point}, {DroneID: 1, Points: point}}, true},
		{"D1 plus non-participant D3", []uint32{1, 2}, []Route{{DroneID: 1, Points: point}, {DroneID: 3, Points: point}}, true},
		{"missing D2", []uint32{1, 2}, []Route{{DroneID: 1, Points: point}}, true},
		{"duplicate participant input", []uint32{1, 1, 2}, []Route{{DroneID: 1, Points: point}, {DroneID: 2, Points: point}}, true},
		{"exact D1 D2", []uint32{1, 2}, []Route{{DroneID: 1, Points: point}, {DroneID: 2, Points: point}}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p := MissionPlan{PlanID: "bijection", Mode: ModeSeparate, Participants: tc.participants, Routes: tc.routes}
			err := p.Validate()
			if tc.wantErr && !errors.Is(err, ErrRouteMismatch) {
				t.Fatalf("want ErrRouteMismatch, got %v", err)
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("valid exact bijection rejected: %v", err)
			}
		})
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

func TestAltitudeForPreservesPerDroneGroupedAltitude(t *testing.T) {
	p := MissionPlan{ParticipantAltitudes: map[uint32]float64{1: 18, 2: 27}}
	if got := p.AltitudeFor(1, 20); got != 18 {
		t.Fatalf("D1 altitude = %v, want 18", got)
	}
	if got := p.AltitudeFor(2, 20); got != 27 {
		t.Fatalf("D2 altitude = %v, want 27", got)
	}
	if got := p.AltitudeFor(3, 20); got != 20 {
		t.Fatalf("missing participant altitude should fallback to waypoint alt: got %v", got)
	}
}

func authorityPlan() MissionPlan {
	return MissionPlan{
		PlanID:       "authority-v1",
		Mode:         ModeGrouped,
		Participants: []uint32{1},
		Routes: []Route{{Points: []Waypoint{
			{Seq: 0, Lat: 14.0, Lon: 100.0, Alt: 20},
			{Seq: 1, Lat: 14.001, Lon: 100.0, Alt: 20},
		}}},
	}
}

func TestValidateAuthorityV1AcceptsSingleDroneGroupedPlainRoute(t *testing.T) {
	p := authorityPlan()
	if err := p.ValidateAuthorityV1(); err != nil {
		t.Fatalf("eligible authority plan rejected: %v", err)
	}
}

func TestValidateAuthorityV2AcceptsWaitButKeepsLaterSemanticsBlocked(t *testing.T) {
	p := authorityPlan()
	p.Routes[0].Points[0].WaitSeconds = 30
	if err := p.ValidateAuthorityV2(); err != nil {
		t.Fatalf("F5 authority should accept WAIT: %v", err)
	}
	p.Routes[0].Points[0].Action = ActionServoA
	if err := p.ValidateAuthorityV2(); !errors.Is(err, ErrAuthorityUnsupported) {
		t.Fatalf("payload must remain blocked in F5, got %v", err)
	}
}

func TestValidateAuthorityV1RejectsDeferredSemantics(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(*MissionPlan)
	}{
		{"multi-drone", func(p *MissionPlan) { p.Participants = []uint32{1, 2} }},
		{"separate", func(p *MissionPlan) {
			p.Mode = ModeSeparate
			p.Routes = []Route{{DroneID: 1, Points: []Waypoint{wp(0, 14, 100)}}}
		}},
		{"wait", func(p *MissionPlan) { p.Routes[0].Points[0].WaitSeconds = 60 }},
		{"payload", func(p *MissionPlan) { p.Routes[0].Points[0].Action = ActionServoA }},
		{"rtl-after", func(p *MissionPlan) { p.RtlAfter = true }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p := authorityPlan()
			tc.mutate(&p)
			if err := p.ValidateAuthorityV1(); !errors.Is(err, ErrAuthorityUnsupported) {
				t.Fatalf("want ErrAuthorityUnsupported, got %v", err)
			}
		})
	}
}
