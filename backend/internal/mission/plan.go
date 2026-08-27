// Package mission is the Go-side mission state model for the Real-Flight Safety
// Fast-Track V2 (docs/REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md, phase F2).
//
// SHADOW ONLY — this package computes the *expected* mission state (current
// waypoint, WAIT state, next transition) so it can be compared against the
// existing Python executor before any authority cutover.  It deliberately does
// NOT send flight commands and MUST NOT import the fleet/command send path:
//
//	NO GOTO · NO HOLD · NO TAKEOFF · NO SERVO · NO RTL
//
// The only external dependency is pkg/geo for the same haversine distance the
// rest of the system uses (pure math, no side effects).  See
// docs/MISSION_CORE_CUTOVER_CONTRACT.md PART B for the target contract this
// skeleton is built toward.
package mission

import (
	"errors"
	"fmt"
)

// Mode mirrors the Python waypoint modes (contract B1).  WAVE is intentionally
// out of scope for V2 (see V2 §3.6 / F6B).
type Mode int

const (
	ModeGrouped     Mode = iota // one shared route; advance only when all participants arrive
	ModeSeparate                // per-drone routes; each advances independently
	ModeSwarmLeader             // leader route only; followers stay under Go swarm formation
)

func (m Mode) String() string {
	switch m {
	case ModeGrouped:
		return "GROUPED"
	case ModeSeparate:
		return "SEPARATE"
	case ModeSwarmLeader:
		return "SWARM_LEADER"
	default:
		return fmt.Sprintf("Mode(%d)", int(m))
	}
}

// Action is the optional per-waypoint payload action (A/B servo).  It matches
// the frontend waypoint_logic action metadata and is kept separate from WAIT.
type Action int

const (
	ActionNone Action = iota
	ActionServoA
	ActionServoB
)

func (a Action) String() string {
	switch a {
	case ActionNone:
		return "none"
	case ActionServoA:
		return "servo_a"
	case ActionServoB:
		return "servo_b"
	default:
		return fmt.Sprintf("Action(%d)", int(a))
	}
}

// Waypoint is one point in a route.  WaitSeconds mirrors the frontend WAIT
// metadata (0..600s); Action mirrors the payload A/B metadata.
type Waypoint struct {
	Seq         int
	Lat         float64
	Lon         float64
	Alt         float64
	WaitSeconds int
	Action      Action
}

// Route is an ordered list of waypoints.  DroneID == 0 means the shared route
// used by GROUPED / SWARM_LEADER; for SEPARATE each participant has its own.
type Route struct {
	DroneID uint32
	Points  []Waypoint
}

// MissionPlan is the immutable-after-accept plan (contract B1).  A plan is
// frozen when a run starts; later edits do not affect a running run.
type MissionPlan struct {
	PlanID         string
	Mode           Mode
	Participants   []uint32
	Routes         []Route
	LeaderID       uint32  // SWARM_LEADER only: the drone whose arrival drives progression
	ArrivalRadiusM float64 // horizontal arrival threshold; default DefaultArrivalRadiusM
	RtlAfter       bool
}

// DefaultArrivalRadiusM is the horizontal arrival threshold moved out of the
// browser map JS (map.html TGT_REACH_M = 3.0) into a Core-owned policy value.
const DefaultArrivalRadiusM = 3.0

// MaxWaitSeconds mirrors the frontend WAIT cap (10 minutes).
const MaxWaitSeconds = 600

var (
	ErrNoPlanID       = errors.New("mission: plan_id required")
	ErrNoParticipants = errors.New("mission: at least one participant required")
	ErrNoRoute        = errors.New("mission: plan has no route")
	ErrEmptyRoute     = errors.New("mission: route has no waypoints")
	ErrBadWait        = errors.New("mission: wait_seconds out of range [0,600]")
	ErrRouteMismatch  = errors.New("mission: routes do not match mode/participants")
)

// Validate checks structural invariants without any side effect.  It does not
// judge geofence/altitude safety — that stays in safety.Envelope on the command
// path (contract B7); the shadow engine never issues commands.
func (p *MissionPlan) Validate() error {
	if p.PlanID == "" {
		return ErrNoPlanID
	}
	if len(p.Participants) == 0 {
		return ErrNoParticipants
	}
	if len(p.Routes) == 0 {
		return ErrNoRoute
	}
	for _, r := range p.Routes {
		if len(r.Points) == 0 {
			return ErrEmptyRoute
		}
		for _, wp := range r.Points {
			if wp.WaitSeconds < 0 || wp.WaitSeconds > MaxWaitSeconds {
				return ErrBadWait
			}
		}
	}
	switch p.Mode {
	case ModeGrouped, ModeSwarmLeader:
		// exactly one shared route (DroneID 0 is conventional but not required)
		if len(p.Routes) != 1 {
			return fmt.Errorf("%w: %s expects 1 shared route, got %d",
				ErrRouteMismatch, p.Mode, len(p.Routes))
		}
		if p.Mode == ModeSwarmLeader && p.LeaderID != 0 {
			found := false
			for _, id := range p.Participants {
				if id == p.LeaderID {
					found = true
					break
				}
			}
			if !found {
				return fmt.Errorf("%w: SWARM_LEADER leader %d is not a participant",
					ErrRouteMismatch, p.LeaderID)
			}
		}
	case ModeSeparate:
		// one route per participant, each keyed by a participant id
		if len(p.Routes) != len(p.Participants) {
			return fmt.Errorf("%w: SEPARATE expects %d routes, got %d",
				ErrRouteMismatch, len(p.Participants), len(p.Routes))
		}
		known := make(map[uint32]bool, len(p.Participants))
		for _, id := range p.Participants {
			known[id] = true
		}
		for _, r := range p.Routes {
			if !known[r.DroneID] {
				return fmt.Errorf("%w: SEPARATE route for non-participant drone %d",
					ErrRouteMismatch, r.DroneID)
			}
		}
	default:
		return fmt.Errorf("%w: unknown mode %d", ErrRouteMismatch, int(p.Mode))
	}
	return nil
}

// arrivalRadius returns the effective arrival radius (default if unset).
func (p *MissionPlan) arrivalRadius() float64 {
	if p.ArrivalRadiusM <= 0 {
		return DefaultArrivalRadiusM
	}
	return p.ArrivalRadiusM
}

// sharedRoute returns the single route for GROUPED / SWARM_LEADER.
func (p *MissionPlan) sharedRoute() Route {
	return p.Routes[0]
}

// routeFor returns the SEPARATE route for a drone (ok=false if none).
func (p *MissionPlan) routeFor(droneID uint32) (Route, bool) {
	for _, r := range p.Routes {
		if r.DroneID == droneID {
			return r, true
		}
	}
	return Route{}, false
}
