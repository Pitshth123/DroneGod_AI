package mission

import (
	"fmt"
	"math"
	"sort"
)

// V3-S09-B Legacy SEPARATE preflight thresholds from frontend app.py:
// _wp_alt_sep=2.0m and _wp_min_dist=6.0m.
const (
	SeparateAltSeparationM = 2.0
	SeparateMinDistanceM   = 6.0
	metersPerDegreeLat     = 111320.0
)

// SeparateRouteConflict is the Core-side representation of the Legacy
// waypoint_logic.check_route_conflicts result.
type SeparateRouteConflict struct {
	A, B   uint32
	DistM  float64
	AltGap float64
}

// SeparateRouteConflicts ports the deterministic Legacy preflight geometry.
// starts may contain the current fresh position for each participant; when
// present it is prepended so the initial leg to WP1 is checked too.
func (p *MissionPlan) SeparateRouteConflicts(starts map[uint32][2]float64,
	altSepM, minDistM float64) ([]SeparateRouteConflict, error) {
	if err := p.Validate(); err != nil {
		return nil, err
	}
	if p.Mode != ModeSeparate {
		return nil, fmt.Errorf("%w: route-conflict check requires SEPARATE mode", ErrRouteMismatch)
	}
	if altSepM <= 0 {
		altSepM = SeparateAltSeparationM
	}
	if minDistM <= 0 {
		minDistM = SeparateMinDistanceM
	}
	ids := canonicalParticipants(p.Participants)
	paths := make(map[uint32][][2]float64, len(ids))
	alts := make(map[uint32]float64, len(ids))
	for _, id := range ids {
		route, ok := p.routeFor(id)
		if !ok || len(route.Points) == 0 {
			return nil, fmt.Errorf("%w: missing SEPARATE route for drone %d", ErrRouteMismatch, id)
		}
		path := make([][2]float64, 0, len(route.Points)+1)
		if start, ok := starts[id]; ok {
			if !validPosition(start[0], start[1]) {
				return nil, fmt.Errorf("%w: invalid SEPARATE start position for drone %d", ErrRouteMismatch, id)
			}
			path = append(path, start)
		}
		for _, wp := range route.Points {
			if !validPosition(wp.Lat, wp.Lon) {
				return nil, fmt.Errorf("%w: invalid SEPARATE waypoint for drone %d", ErrRouteMismatch, id)
			}
			path = append(path, [2]float64{wp.Lat, wp.Lon})
		}
		paths[id] = path
		alts[id] = p.AltitudeFor(id, route.Points[0].Alt)
	}

	var out []SeparateRouteConflict
	for i, a := range ids {
		for _, b := range ids[i+1:] {
			gap := math.Abs(alts[a] - alts[b])
			if gap > altSepM {
				continue
			}
			d := separateRouteMinDistance(paths[a], paths[b])
			if d < minDistM {
				out = append(out, SeparateRouteConflict{A: a, B: b, DistM: d, AltGap: gap})
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].DistM < out[j].DistM })
	return out, nil
}

// ValidateSeparateRouteConflicts fails closed when any Legacy-equivalent route
// conflict exists. This is intentionally separate from structural Validate so
// shadow plans can still be represented without claiming authority.
func (p *MissionPlan) ValidateSeparateRouteConflicts(starts map[uint32][2]float64) error {
	conflicts, err := p.SeparateRouteConflicts(starts, SeparateAltSeparationM, SeparateMinDistanceM)
	if err != nil {
		return err
	}
	if len(conflicts) == 0 {
		return nil
	}
	c := conflicts[0]
	return fmt.Errorf("%w: SEPARATE D%d/D%d routes approach %.2fm with altitude gap %.2fm",
		ErrAuthorityUnsupported, c.A, c.B, c.DistM, c.AltGap)
}

func separateRouteMinDistance(a, b [][2]float64) float64 {
	if len(a) == 0 || len(b) == 0 {
		return math.Inf(1)
	}
	lat0 := a[0][0]
	toXY := func(p [2]float64) [2]float64 {
		return [2]float64{
			p[1] * metersPerDegreeLat * math.Cos(lat0*math.Pi/180),
			p[0] * metersPerDegreeLat,
		}
	}
	A := make([][2]float64, len(a))
	B := make([][2]float64, len(b))
	for i, p := range a {
		A[i] = toXY(p)
	}
	for i, p := range b {
		B[i] = toXY(p)
	}
	if len(A) == 1 && len(B) == 1 {
		return math.Hypot(A[0][0]-B[0][0], A[0][1]-B[0][1])
	}
	if len(A) == 1 {
		A = append(A, A[0])
	}
	if len(B) == 1 {
		B = append(B, B[0])
	}
	best := math.Inf(1)
	for i := 0; i < len(A)-1; i++ {
		for j := 0; j < len(B)-1; j++ {
			d := separateSegmentDistance(A[i], A[i+1], B[j], B[j+1])
			if d < best {
				best = d
				if best <= 0 {
					return 0
				}
			}
		}
	}
	return best
}

func separateSegmentDistance(p1, p2, p3, p4 [2]float64) float64 {
	sub := func(a, b [2]float64) [2]float64 { return [2]float64{a[0] - b[0], a[1] - b[1]} }
	dot := func(a, b [2]float64) float64 { return a[0]*b[0] + a[1]*b[1] }
	pointSeg := func(p, a, b [2]float64) float64 {
		ab := sub(b, a)
		den := dot(ab, ab)
		if den <= 1e-12 {
			d := sub(p, a)
			return math.Hypot(d[0], d[1])
		}
		t := dot(sub(p, a), ab) / den
		if t < 0 {
			t = 0
		} else if t > 1 {
			t = 1
		}
		proj := [2]float64{a[0] + ab[0]*t, a[1] + ab[1]*t}
		d := sub(p, proj)
		return math.Hypot(d[0], d[1])
	}
	r := sub(p2, p1)
	s := sub(p4, p3)
	denom := r[0]*s[1] - r[1]*s[0]
	if math.Abs(denom) > 1e-12 {
		qp := sub(p3, p1)
		t := (qp[0]*s[1] - qp[1]*s[0]) / denom
		u := (qp[0]*r[1] - qp[1]*r[0]) / denom
		if t >= 0 && t <= 1 && u >= 0 && u <= 1 {
			return 0
		}
	}
	return math.Min(
		math.Min(pointSeg(p1, p3, p4), pointSeg(p2, p3, p4)),
		math.Min(pointSeg(p3, p1, p2), pointSeg(p4, p1, p2)),
	)
}
