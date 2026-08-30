package mission

import (
	"math"
	"testing"
)

// V3-S09-A parity — the Go formation-offset port must reproduce the exact legacy
// Python contract pinned in
// frontend/tests/test_grouped_multi_characterization.py.  The shared vector
// (POS/WP/EXPECTED_*) is duplicated here on purpose so a drift in either language
// fails a test rather than silently diverging.

var parityPos = map[uint32][2]float64{
	1: {14.0000, 100.0000},
	2: {14.0000, 100.0010},
	3: {14.0010, 100.0005},
}

var parityWP = [2]float64{14.0050, 100.0050}

var parityExpectedTargets = map[uint32][2]float64{
	1: {14.0046666667, 100.0045},
	2: {14.0046666667, 100.0055},
	3: {14.0056666667, 100.0050},
}

var parityExpectedOrder = []uint32{3, 1, 2}

const parityTol = 1e-7

func TestGroupedMultiParityOffsetTargets(t *testing.T) {
	got := groupOffsetTargets(parityPos, parityWP[0], parityWP[1])
	if len(got) != len(parityExpectedTargets) {
		t.Fatalf("target count = %d, want %d", len(got), len(parityExpectedTargets))
	}
	for id, want := range parityExpectedTargets {
		g, ok := got[id]
		if !ok {
			t.Fatalf("missing target for D%d", id)
		}
		if math.Abs(g[0]-want[0]) > parityTol || math.Abs(g[1]-want[1]) > parityTol {
			t.Fatalf("D%d target = (%.10f,%.10f), want (%.10f,%.10f)",
				id, g[0], g[1], want[0], want[1])
		}
	}
}

func TestGroupedMultiParityCentroidIsWaypoint(t *testing.T) {
	got := groupOffsetTargets(parityPos, parityWP[0], parityWP[1])
	var clat, clon float64
	for _, t := range got {
		clat += t[0]
		clon += t[1]
	}
	n := float64(len(got))
	clat, clon = clat/n, clon/n
	if math.Abs(clat-parityWP[0]) > parityTol || math.Abs(clon-parityWP[1]) > parityTol {
		t.Fatalf("centroid of targets = (%.10f,%.10f), want waypoint (%.10f,%.10f)",
			clat, clon, parityWP[0], parityWP[1])
	}
}

func TestGroupedMultiParityPairwiseOffsetsPreserved(t *testing.T) {
	got := groupOffsetTargets(parityPos, parityWP[0], parityWP[1])
	ids := []uint32{1, 2, 3}
	for _, a := range ids {
		for _, b := range ids {
			if a >= b {
				continue
			}
			posOff := [2]float64{parityPos[a][0] - parityPos[b][0], parityPos[a][1] - parityPos[b][1]}
			tgtOff := [2]float64{got[a][0] - got[b][0], got[a][1] - got[b][1]}
			if math.Abs(posOff[0]-tgtOff[0]) > parityTol || math.Abs(posOff[1]-tgtOff[1]) > parityTol {
				t.Fatalf("pairwise offset D%d-D%d not preserved: pos %v tgt %v", a, b, posOff, tgtOff)
			}
		}
	}
}

func TestGroupedMultiParitySingleDroneTargetsRawWaypoint(t *testing.T) {
	got := groupOffsetTargets(map[uint32][2]float64{7: {14.0, 100.0}}, parityWP[0], parityWP[1])
	g := got[7]
	if math.Abs(g[0]-parityWP[0]) > parityTol || math.Abs(g[1]-parityWP[1]) > parityTol {
		t.Fatalf("single-drone target = %v, want raw waypoint %v", g, parityWP)
	}
}

func TestGroupedMultiParityDispatchOrder(t *testing.T) {
	got := groupGotoOrder(parityPos, []uint32{1, 2, 3}, parityWP[0], parityWP[1])
	if len(got) != len(parityExpectedOrder) {
		t.Fatalf("order len = %d, want %d (%v)", len(got), len(parityExpectedOrder), got)
	}
	for i := range parityExpectedOrder {
		if got[i] != parityExpectedOrder[i] {
			t.Fatalf("order = %v, want %v", got, parityExpectedOrder)
		}
	}
}

func TestGroupedMultiParityDispatchOrderEast(t *testing.T) {
	// Moving due east: the rightmost (largest lon) drone is sent first.
	pos := map[uint32][2]float64{
		1: {14.0, 100.000},
		2: {14.0, 100.001},
		3: {14.0, 100.002},
	}
	got := groupGotoOrder(pos, []uint32{1, 2, 3}, 14.0, 100.010)
	want := []uint32{3, 2, 1}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("east order = %v, want %v", got, want)
		}
	}
}

func TestGroupedMultiParityUnknownPositionAppendedLast(t *testing.T) {
	// D3 has no known position -> it falls to the end of the dispatch order in id
	// order, matching legacy `order + [d for d in ids if d not in order]`.
	known := map[uint32][2]float64{
		1: {14.0, 100.000},
		2: {14.0, 100.001},
	}
	got := groupGotoOrder(known, []uint32{1, 2, 3}, 14.0, 100.010)
	if got[len(got)-1] != 3 {
		t.Fatalf("unknown-position drone must be last, got order %v", got)
	}
}
