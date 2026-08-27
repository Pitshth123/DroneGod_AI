package swarm

import (
	"math"
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

func approx(a, b float64) bool { return math.Abs(a-b) < 0.05 }

func TestMinPairwise_Line(t *testing.T) {
	// หน้ากระดาน: ตัวแม่ถึงลูกตัวแรก = d
	if got := minPairwiseDist(pb.Formation_FORMATION_LINE, 3, 6); !approx(got, 6) {
		t.Errorf("LINE d=6 count=3 min=%.2f want 6", got)
	}
}

func TestMinPairwise_Column(t *testing.T) {
	if got := minPairwiseDist(pb.Formation_FORMATION_COLUMN, 5, 5); !approx(got, 5) {
		t.Errorf("COLUMN d=5 count=5 min=%.2f want 5", got)
	}
}

func TestMinPairwise_Wedge(t *testing.T) {
	// ลิ่ม d=10: ตัวแม่ถึง slot0 = sqrt(10^2 + 8^2) = 12.806
	if got := minPairwiseDist(pb.Formation_FORMATION_WEDGE, 3, 10); !approx(got, 12.806) {
		t.Errorf("WEDGE d=10 count=3 min=%.2f want 12.81", got)
	}
}

func TestMinPairwise_Diamond(t *testing.T) {
	// เพชร d=10: ตัวแม่ถึง (-10,-10) = 14.14 ; แต่ (-10,-10)-(-20,0)=14.14 ; min ~14.14
	got := minPairwiseDist(pb.Formation_FORMATION_DIAMOND, 4, 10)
	if got < 13 || got > 15 {
		t.Errorf("DIAMOND d=10 count=4 min=%.2f want ~14", got)
	}
}

func TestMinPairwise_TooClose(t *testing.T) {
	// LINE d=4 → min 4 < MinSeparation 5 → ต้องถูกจับได้ว่าใกล้เกิน
	if got := minPairwiseDist(pb.Formation_FORMATION_LINE, 3, 4); got >= 5 {
		t.Errorf("LINE d=4 should be < 5, got %.2f", got)
	}
}

func TestMinPairwise_Monotonic(t *testing.T) {
	// spacing มากขึ้น → ระยะห่างต่ำสุดมากขึ้น (ทุกรูปแบบ)
	for _, f := range []pb.Formation{
		pb.Formation_FORMATION_WEDGE, pb.Formation_FORMATION_LINE,
		pb.Formation_FORMATION_COLUMN, pb.Formation_FORMATION_DIAMOND,
		pb.Formation_FORMATION_ECHELON,
	} {
		a := minPairwiseDist(f, 5, 5)
		b := minPairwiseDist(f, 5, 10)
		if b <= a {
			t.Errorf("formation %v not monotonic: d=5→%.2f d=10→%.2f", f, a, b)
		}
	}
}

func TestFormUpTransitLayersRemainSeparatedForTwentyFollowers(t *testing.T) {
	const maxCurrentAlt = 10.0
	const gap = 5.0
	previous := maxCurrentAlt
	for i := 0; i < 20; i++ {
		alt := formUpTransitAltitude(maxCurrentAlt, i, gap)
		if alt-previous < gap {
			t.Fatalf("layer %d gap %.1fm, want at least %.1fm", i, alt-previous, gap)
		}
		previous = alt
	}
}

func TestFormUpTransitLayersEnforceHardTwoMetreMinimum(t *testing.T) {
	if got := formUpTransitAltitude(20, 0, 0.5); got != 22 {
		t.Fatalf("first transit altitude %.1fm, want 22m", got)
	}
}
