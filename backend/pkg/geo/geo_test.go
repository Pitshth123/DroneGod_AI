package geo

import "testing"

// สี่เหลี่ยมรอบ ~14.958,102.098 (~44m ต่อด้าน)
var fence = [][2]float64{
	{14.9580, 102.0980}, {14.9584, 102.0980},
	{14.9584, 102.0984}, {14.9580, 102.0984},
}

func TestHerdTarget_Inside(t *testing.T) {
	// จุดนอกเขต (เหนือ) → herd target ต้องอยู่ในเขต + ~2m จากขอบ
	tlat, tlon := HerdTarget(14.9590, 102.0982, fence, 2.0)
	if !PointInPolygon(tlat, tlon, fence) {
		t.Fatal("herd target ต้องอยู่ในเขต")
	}
	d := DistanceToEdge(tlat, tlon, fence)
	if d < 1.0 || d > 3.5 {
		t.Errorf("herd target ควร ~2m ในเขต, ได้ %.2fm", d)
	}
}

func TestHerdTarget_AllSides(t *testing.T) {
	pts := [][2]float64{{14.9590, 102.0982}, {14.9575, 102.0982},
		{14.9582, 102.0990}, {14.9582, 102.0975}}
	for _, p := range pts {
		tlat, tlon := HerdTarget(p[0], p[1], fence, 2.0)
		if !PointInPolygon(tlat, tlon, fence) {
			t.Errorf("จุด %.4f,%.4f: herd target หลุดเขต", p[0], p[1])
		}
	}
}

func TestDistanceToEdge(t *testing.T) {
	// จุดกลางเขต ควรห่างขอบพอสมควร (~22m)
	if d := DistanceToEdge(14.9582, 102.0982, fence); d < 15 {
		t.Errorf("center should be far from edge, got %.1fm", d)
	}
}
