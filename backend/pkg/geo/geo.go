// Package geo — คำนวณระยะทาง/พิกัดภูมิศาสตร์ (พอร์ตจาก safety.py เดิม)
package geo

import "math"

const earthRadiusM = 6371000.0

// HaversineM คืนระยะทางเป็นเมตรระหว่าง 2 จุด (lat/lon องศา)
func HaversineM(lat1, lon1, lat2, lon2 float64) float64 {
	rlat1 := lat1 * math.Pi / 180
	rlat2 := lat2 * math.Pi / 180
	dlat := (lat2 - lat1) * math.Pi / 180
	dlon := (lon2 - lon1) * math.Pi / 180
	a := math.Sin(dlat/2)*math.Sin(dlat/2) +
		math.Cos(rlat1)*math.Cos(rlat2)*math.Sin(dlon/2)*math.Sin(dlon/2)
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	return earthRadiusM * c
}

// ── local-meters helpers (tangent plane รอบจุดอ้างอิง) ──
func toLocal(lat, lon, lat0, lon0 float64) (east, north float64) {
	east = (lon - lon0) * math.Cos(lat0*math.Pi/180) * earthRadiusM * math.Pi / 180
	north = (lat - lat0) * earthRadiusM * math.Pi / 180
	return
}
func fromLocal(east, north, lat0, lon0 float64) (lat, lon float64) {
	lat = lat0 + north/(earthRadiusM*math.Pi/180)
	lon = lon0 + east/(math.Cos(lat0*math.Pi/180)*earthRadiusM*math.Pi/180)
	return
}

// OffsetM เลื่อนจุดด้วยระยะทาง local N/E (เมตร) รอบพิกัดอ้างอิง
// ใช้ฉายตำแหน่งล่วงหน้าก่อนอนุญาตคำสั่ง manual velocity.
func OffsetM(lat, lon, north, east float64) (targetLat, targetLon float64) {
	return fromLocal(east, north, lat, lon)
}

// Centroid — จุดกึ่งกลางของ polygon (เฉลี่ย vertex)
func Centroid(poly [][2]float64) (lat, lon float64) {
	if len(poly) == 0 {
		return 0, 0
	}
	for _, p := range poly {
		lat += p[0]
		lon += p[1]
	}
	n := float64(len(poly))
	return lat / n, lon / n
}

// NearestEdgePoint — จุดบนขอบ polygon ที่ใกล้ (lat,lon) ที่สุด
func NearestEdgePoint(lat, lon float64, poly [][2]float64) (elat, elon float64) {
	best := math.Inf(1)
	elat, elon = lat, lon
	j := len(poly) - 1
	for i := 0; i < len(poly); i++ {
		ax, ay := toLocal(poly[j][0], poly[j][1], lat, lon)
		bx, by := toLocal(poly[i][0], poly[i][1], lat, lon)
		// project (0,0) onto segment a-b
		dx, dy := bx-ax, by-ay
		l2 := dx*dx + dy*dy
		var t float64
		if l2 > 1e-9 {
			t = -(ax*dx + ay*dy) / l2
			if t < 0 {
				t = 0
			} else if t > 1 {
				t = 1
			}
		}
		px, py := ax+t*dx, ay+t*dy
		d := px*px + py*py
		if d < best {
			best = d
			elat, elon = fromLocal(px, py, lat, lon)
		}
		j = i
	}
	return
}

// HerdTarget — จุดที่อยู่ insetM เมตร "ในเขต" จากขอบที่ใกล้สุด (ดึงโดรนกลับเข้าเขต)
func HerdTarget(lat, lon float64, poly [][2]float64, insetM float64) (tlat, tlon float64) {
	if len(poly) < 3 {
		return lat, lon
	}
	elat, elon := NearestEdgePoint(lat, lon, poly)
	clat, clon := Centroid(poly)
	cx, cy := toLocal(clat, clon, elat, elon) // ทิศ edge -> centroid (เข้าเขต)
	d := math.Hypot(cx, cy)
	if d < 1e-6 {
		return clat, clon
	}
	return fromLocal(insetM*cx/d, insetM*cy/d, elat, elon)
}

// DistanceToEdge — ระยะ (m) จากจุดถึงขอบที่ใกล้สุด
func DistanceToEdge(lat, lon float64, poly [][2]float64) float64 {
	elat, elon := NearestEdgePoint(lat, lon, poly)
	return HaversineM(lat, lon, elat, elon)
}

// PointInPolygon — ray casting; polygon เป็น []{lat,lon} (สำหรับ geofence)
func PointInPolygon(lat, lon float64, poly [][2]float64) bool {
	if len(poly) < 3 {
		return true // ไม่มี fence = ผ่าน (fence ปิดใช้งาน)
	}
	inside := false
	j := len(poly) - 1
	for i := 0; i < len(poly); i++ {
		xi, yi := poly[i][0], poly[i][1]
		xj, yj := poly[j][0], poly[j][1]
		if ((yi > lon) != (yj > lon)) &&
			(lat < (xj-xi)*(lon-yi)/(yj-yi)+xi) {
			inside = !inside
		}
		j = i
	}
	return inside
}
