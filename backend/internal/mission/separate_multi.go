package mission

import "fmt"

// V3-S09-B — SEPARATE Core authority (PRE-FLIP, OFF by default).
//
// Legacy SEPARATE (app.py `_wp_advance_one` / `_on_target_reached` SEPARATE branch):
// each participant has its OWN route and its own index; it flies to its route's RAW
// waypoint (NO formation offset — unlike GROUPED), at its per-drone altitude, and
// advances independently.  One drone finishing does not block the others; the run
// completes when every route is finished.  Arrival is judged per drone against the
// raw waypoint (which is exactly what was commanded), so the shadow engine's
// existing observeSeparate arrival is already parity-correct — SEPARATE needs no
// offset-target machinery. Route-conflict/preflight geometry is now also ported
// into Core: the authority predicate rejects conflicting planned segments, and the
// API authority boundary additionally prepends fresh current positions so the
// initial leg to WP1 cannot bypass the Legacy collision gate.
//
// Gated behind EnableSeparateAuthority(); no live profile token wires it up.  WAIT,
// actions and rtl_after stay Python-owned via ValidateAuthoritySeparate.

// ValidateAuthoritySeparate is the exact S09-B capability predicate.  Separate from
// the GROUPED predicates so scopes never widen each other.
//
// Round-1 supported: SEPARATE, >= 2 unique participants, one route per participant,
// per-drone altitude, independent progression.
// Deferred (stay Python-owned): WAIT, payload actions, rtl_after.
func (p *MissionPlan) ValidateAuthoritySeparate() error {
	if err := p.Validate(); err != nil {
		return err
	}
	if p.Mode != ModeSeparate {
		return fmt.Errorf("%w: mode %s", ErrAuthorityUnsupported, p.Mode)
	}
	if n := len(canonicalParticipants(p.Participants)); n < 2 {
		return fmt.Errorf("%w: SEPARATE requires >= 2 unique participants, got %d",
			ErrAuthorityUnsupported, n)
	}
	if err := p.ValidateSeparateRouteConflicts(nil); err != nil {
		return err
	}
	if p.RtlAfter || p.ReturnPolicy != ReturnNone {
		return fmt.Errorf("%w: rtl_after is deferred", ErrAuthorityUnsupported)
	}
	for _, r := range p.Routes {
		for _, wp := range r.Points {
			if wp.Action != ActionNone {
				return fmt.Errorf("%w: payload action is deferred", ErrAuthorityUnsupported)
			}
			if wp.WaitSeconds != 0 {
				return fmt.Errorf("%w: SEPARATE WAIT is deferred", ErrAuthorityUnsupported)
			}
		}
	}
	return nil
}

// ClaimAuthoritySeparateGotos returns one GotoIntent per participant that has an
// un-dispatched current waypoint, each targeting its own route's RAW waypoint at
// its per-drone altitude.  Each (drone,index) is claimed exactly once
// (sepClaimedIndex), so no duplicate GOTO is emitted while a drone sits at an index;
// when a drone advances independently, its next index becomes claimable again.  A
// terminal/cancelled/interrupted/failed run emits nothing, so a late claim after
// takeover can never write to the FC.
func (e *Engine) ClaimAuthoritySeparateGotos() ([]GotoIntent, bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	run := e.run
	if !e.authority || !e.authoritySeparate || run == nil || run.State != StateRunning {
		return nil, false, nil
	}
	validationPlan := e.authorityValidationPlan(run.Plan)
	if err := validationPlan.ValidateAuthoritySeparate(); err != nil {
		return nil, false, err
	}
	var intents []GotoIntent
	for _, id := range run.Participants { // Participants is canonical (unique, sorted)
		route, ok := run.Plan.routeFor(id)
		if !ok {
			return nil, false, fmt.Errorf("%w: missing SEPARATE route for drone %d",
				ErrRouteMismatch, id)
		}
		idx := run.sepIndex[id]
		if idx < 0 || idx >= len(route.Points) {
			continue // this drone finished its route
		}
		if _, waiting := run.waits[id]; waiting {
			continue // holding at a WAIT (not in round-1 scope, but stay safe)
		}
		if ci, seen := run.sepClaimedIndex[id]; seen && ci == idx {
			continue // already dispatched this drone's current index
		}
		wp := route.Points[idx]
		intents = append(intents, GotoIntent{
			RunID:   run.RunID,
			Index:   idx,
			DroneID: id,
			Lat:     wp.Lat,
			Lon:     wp.Lon,
			Alt:     run.Plan.AltitudeFor(id, wp.Alt),
		})
		run.sepClaimedIndex[id] = idx
	}
	if len(intents) == 0 {
		return nil, false, nil
	}
	return intents, true, nil
}
