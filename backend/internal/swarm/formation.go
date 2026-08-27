package swarm

import (
	"math"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// slotOffset คืน (north, east, up) ของ follower slot ที่ i ตามรูปแบบขบวน
// (ตัวแม่อยู่ที่ origin 0,0; +north=หน้า, +east=ขวา)
func slotOffset(f pb.Formation, i int, d float64) (north, east, up float64) {
	switch f {
	case pb.Formation_FORMATION_LINE:
		// หน้ากระดาน — เรียงข้างกัน (สลับซ้าย-ขวา)
		rank := float64(i/2 + 1)
		side := sideOf(i)
		return 0, side * rank * d, 0

	case pb.Formation_FORMATION_COLUMN:
		// แถวตอน — เรียงหลังกันเป็นเส้นเดียว
		return -float64(i+1) * d, 0, 0

	case pb.Formation_FORMATION_DIAMOND:
		// เพชร — slot: หลังซ้าย, หลังขวา, หลังไกล, (ต่อด้วย wedge ถ้าเกิน)
		switch i {
		case 0:
			return -d, -d, 0
		case 1:
			return -d, d, 0
		case 2:
			return -2 * d, 0, 0
		default:
			rank := float64((i-3)/2 + 2)
			return -rank * d, sideOf(i-3) * rank * d, 0
		}

	case pb.Formation_FORMATION_ECHELON:
		// ทแยง — เฉียงไปด้านขวา-หลัง
		s := float64(i+1) * d * 0.75
		return -s, s, 0

	default: // WEDGE (ลิ่ม/V)
		rank := float64(i/2 + 1)
		side := sideOf(i)
		return -rank * d, side * rank * d * 0.8, 0
	}
}

func sideOf(i int) float64 {
	if i%2 == 1 {
		return 1.0 // ขวา
	}
	return -1.0 // ซ้าย
}

// minPairwiseDist คืนระยะห่างต่ำสุด (m) ระหว่างโดรนทุกคู่ในขบวน
// (รวมตัวแม่ที่ origin + follower count-1 ตัว) — ใช้ตรวจความปลอดภัยก่อนตั้งค่า
func minPairwiseDist(f pb.Formation, count int, d float64) float64 {
	if count < 2 {
		return math.Inf(1)
	}
	pts := make([][2]float64, 0, count)
	pts = append(pts, [2]float64{0, 0}) // ตัวแม่
	for i := 0; i < count-1; i++ {
		n, e, _ := slotOffset(f, i, d)
		pts = append(pts, [2]float64{n, e})
	}
	minD := math.Inf(1)
	for a := 0; a < len(pts); a++ {
		for b := a + 1; b < len(pts); b++ {
			dn := pts[a][0] - pts[b][0]
			de := pts[a][1] - pts[b][1]
			dist := math.Hypot(dn, de)
			if dist < minD {
				minD = dist
			}
		}
	}
	return minD
}
