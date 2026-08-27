// sitlverify runs a destructive-to-simulation-only multi-drone flight check.
// It connects three ArduCopter SITL instances, flies a formation, moves it,
// and verifies the core-owned return/landing sequence while measuring spacing.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math"
	"os"
	"sort"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
	"github.com/swarmgod/backend/pkg/geo"
)

const (
	minHorizontalM = 5.0
	altConflictM   = 2.0
)

type verifier struct {
	c            pb.SwarmGodServiceClient
	ctx          context.Context
	ids          []uint32
	minObserved  float64
	minSpatial   float64
	sampleCount  int
	collisionLog []string
	phase        string
}

func main() {
	core := flag.String("core", "127.0.0.1:50052", "core gRPC address")
	warmup := flag.Duration("warmup", 60*time.Second, "FC/EKF warm-up after GPS readiness")
	droneCount := flag.Int("count", 3, "number of SITL drones (ports 5760, 5770, ...)")
	extended := flag.Bool("extended", false, "also run grouped-waypoint and wave scenarios")
	waveOnly := flag.Bool("wave-only", false, "run only the two-group wave scenario after warm-up")
	flag.Parse()
	if *droneCount < 2 || *droneCount > 20 {
		log.Fatalf("count must be 2..20 (got %d)", *droneCount)
	}
	if (*extended || *waveOnly) && *droneCount < 4 {
		log.Fatal("extended wave scenario requires at least 4 drones")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	ids := make([]uint32, *droneCount)
	for i := range ids {
		ids[i] = uint32(i + 1)
	}
	v := &verifier{c: pb.NewSwarmGodServiceClient(conn), ctx: ctx,
		ids: ids, minObserved: math.Inf(1), minSpatial: math.Inf(1), phase: "connect"}
	defer v.cleanup()

	v.connectAll()
	v.waitFor("GPS ready", 120*time.Second, func(s *pb.FleetSnapshot) bool {
		return count(s.Drones, func(d *pb.Telemetry) bool {
			return d.TelemetryVerified && d.GpsFix >= pb.GpsFix_GPS_FIX_3D &&
				d.SatCount >= 6 && d.BatteryPct >= 25
		}) == len(v.ids)
	})
	// GPS 3D อย่างเดียวยังไม่แปลว่า EKF/Home ผ่าน pre-arm แล้ว; ArduPilot SITL
	// ต้อง warm ประมาณ 40–60 วินาทีตาม runbook ก่อนทดสอบ TAKEOFF.
	v.phase = "warmup"
	v.observe("FC/EKF warm-up", *warmup)
	if *waveOnly {
		v.runWave()
		v.assertNoCollisions()
		return
	}
	v.phase = "takeoff"
	result, err := v.c.Takeoff(v.ctx, &pb.TakeoffRequest{
		Target: &pb.Target{DroneIds: v.ids}, Altitude: 20, Confirmed: true,
		RequestId: "sitlverify-takeoff",
	})
	v.must(result, err, "TAKEOFF")
	v.waitFor("all airborne", 120*time.Second, func(s *pb.FleetSnapshot) bool {
		return count(s.Drones, func(d *pb.Telemetry) bool {
			return d.Armed && d.Position != nil && d.Position.AltRel >= 17
		}) == len(v.ids)
	})
	if *extended {
		v.phase = "waypoint-grouped"
		v.gotoTranslated(v.ids, 25, 0, 20, "GROUPED WAYPOINT")
		v.observe("grouped waypoint stable", 10*time.Second)
	}
	result, err = v.c.SetSwarmConfig(v.ctx, &pb.SwarmConfig{
		Spacing: 12, Formation: pb.Formation_FORMATION_LINE,
	})
	v.must(result, err, "SET FORMATION")
	result, err = v.c.SwarmControl(v.ctx, &pb.SwarmControlRequest{
		Action: pb.SwarmControlRequest_START, RequestId: "sitlverify-formup",
	})
	v.must(result, err, "FORM UP")
	v.phase = "formation"
	v.waitFor("formation settled", 6*time.Minute, func(s *pb.FleetSnapshot) bool {
		return formationSettled(s, v.ids, 12, 20)
	})
	v.observe("formation stable", 10*time.Second)
	result, err = v.c.Goto(v.ctx, &pb.GotoRequest{
		DroneId: 1, Lat: 14.9581695 + 0.00036, Lon: 102.0986187, Alt: 20,
		RequestId: "sitlverify-goto",
	})
	v.must(result, err, "GOTO LEADER")
	v.phase = "formation-move"
	v.observe("formation move", 25*time.Second)
	v.phase = "return-land"
	result, err = v.c.SwarmControl(v.ctx, &pb.SwarmControlRequest{
		Action: pb.SwarmControlRequest_RETURN, DroneIds: v.ids,
		ReturnBaseAlt: 20, ReturnGap: 5, RequestId: "sitlverify-return",
	})
	v.must(result, err, "RETURN + LAND")
	v.waitFor("all landed and disarmed", 5*time.Minute, func(s *pb.FleetSnapshot) bool {
		return count(s.Drones, func(d *pb.Telemetry) bool {
			return !d.Armed && d.Position != nil && d.Position.AltRel < 1
		}) == len(v.ids)
	})
	if *extended {
		v.runWave()
	}
	v.assertNoCollisions()
}

func (v *verifier) runWave() {
	groups := [][]uint32{append([]uint32(nil), v.ids[:2]...), append([]uint32(nil), v.ids[2:]...)}
	for i, group := range groups {
		v.phase = fmt.Sprintf("wave-group-%d-takeoff", i+1)
		v.takeoff(group, 20, fmt.Sprintf("WAVE G%d TAKEOFF", i+1))
		v.phase = fmt.Sprintf("wave-group-%d-route", i+1)
		v.gotoTranslated(group, 20, 0, 20, fmt.Sprintf("WAVE G%d WAYPOINT", i+1))
		v.observe(fmt.Sprintf("wave group %d route stable", i+1), 5*time.Second)
		v.phase = fmt.Sprintf("wave-group-%d-return", i+1)
		v.returnAndWait(group, fmt.Sprintf("WAVE G%d RETURN + LAND", i+1))
	}
}

func (v *verifier) assertNoCollisions() {
	if len(v.collisionLog) > 0 {
		for _, line := range v.collisionLog {
			log.Print(line)
		}
		log.Fatalf("FAIL: detected %d separation violations", len(v.collisionLog))
	}
	log.Printf("PASS: %d live samples, minimum horizontal %.2fm, minimum 3D %.2fm",
		v.sampleCount, v.minObserved, v.minSpatial)
}

func (v *verifier) takeoff(ids []uint32, altitude float64, label string) {
	result, err := v.c.Takeoff(v.ctx, &pb.TakeoffRequest{
		Target: &pb.Target{DroneIds: ids}, Altitude: altitude, Confirmed: true,
		RequestId: fmt.Sprintf("sitlverify-%s", label),
	})
	v.must(result, err, label)
	v.waitFor(label+" airborne", 120*time.Second, func(s *pb.FleetSnapshot) bool {
		return countIDs(s, ids, func(d *pb.Telemetry) bool {
			return d.Armed && d.Position != nil && d.Position.AltRel >= altitude-3
		}) == len(ids)
	})
}

func (v *verifier) gotoTranslated(ids []uint32, northM, eastM, altitude float64, label string) {
	s := v.snapshot()
	targets := make(map[uint32][2]float64, len(ids))
	byID := make(map[uint32]*pb.Telemetry, len(s.Drones))
	for _, d := range s.Drones {
		byID[d.DroneId] = d
	}
	for _, id := range ids {
		d := byID[id]
		if d == nil || d.Position == nil {
			log.Fatalf("%s: D%d has no position", label, id)
		}
		lat, lon := geo.OffsetM(d.Position.Lat, d.Position.Lon, northM, eastM)
		targets[id] = [2]float64{lat, lon}
		result, err := v.c.Goto(v.ctx, &pb.GotoRequest{DroneId: id, Lat: lat, Lon: lon,
			Alt: altitude, RequestId: fmt.Sprintf("sitlverify-%s-d%d", label, id)})
		v.must(result, err, fmt.Sprintf("%s D%d", label, id))
	}
	v.waitFor(label+" reached", 180*time.Second, func(s *pb.FleetSnapshot) bool {
		return countIDs(s, ids, func(d *pb.Telemetry) bool {
			t := targets[d.DroneId]
			return d.Position != nil && geo.HaversineM(d.Position.Lat, d.Position.Lon, t[0], t[1]) <= 2.5 &&
				math.Abs(d.Position.AltRel-altitude) <= 1.5
		}) == len(ids)
	})
}

func (v *verifier) returnAndWait(ids []uint32, label string) {
	result, err := v.c.SwarmControl(v.ctx, &pb.SwarmControlRequest{
		Action: pb.SwarmControlRequest_RETURN, DroneIds: ids,
		ReturnBaseAlt: 20, ReturnGap: 5, RequestId: "sitlverify-" + label,
	})
	v.must(result, err, label)
	v.waitFor(label+" landed", 5*time.Minute, func(s *pb.FleetSnapshot) bool {
		return countIDs(s, ids, func(d *pb.Telemetry) bool {
			return !d.Armed && d.Position != nil && d.Position.AltRel < 1
		}) == len(ids)
	})
}

func (v *verifier) connectAll() {
	for _, id := range v.ids {
		port := 5760 + (id-1)*10
		result, err := v.c.Connect(v.ctx, &pb.ConnectRequest{DroneId: id,
			Name: fmt.Sprintf("SITL_%d", id), Protocol: "tcp",
			Host: "127.0.0.1", Port: port})
		v.must(result, err, fmt.Sprintf("CONNECT D%d", id))
	}
}

func (v *verifier) must(result *pb.CommandResult, err error, label string) {
	if err != nil {
		log.Fatalf("%s RPC: %v", label, err)
	}
	if result == nil || !result.Ok {
		log.Fatalf("%s rejected: %s", label, result.GetMessage())
	}
	log.Printf("%s accepted: %s", label, result.Message)
}

func (v *verifier) snapshot() *pb.FleetSnapshot {
	s, err := v.c.GetFleetSnapshot(v.ctx, &pb.FleetSnapshotRequest{})
	if err != nil {
		log.Fatalf("snapshot: %v", err)
	}
	v.checkSeparation(s)
	return s
}

func (v *verifier) waitFor(label string, timeout time.Duration,
	ready func(*pb.FleetSnapshot) bool) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if ready(v.snapshot()) {
			log.Printf("%s", label)
			return
		}
		time.Sleep(time.Second)
	}
	log.Fatalf("timeout waiting for %s", label)
}

func (v *verifier) observe(label string, duration time.Duration) {
	deadline := time.Now().Add(duration)
	for time.Now().Before(deadline) {
		v.snapshot()
		time.Sleep(500 * time.Millisecond)
	}
	log.Printf("observed %s for %s", label, duration)
}

func (v *verifier) checkSeparation(s *pb.FleetSnapshot) {
	armed := make([]*pb.Telemetry, 0, len(s.Drones))
	for _, d := range s.Drones {
		if d.Armed && d.TelemetryVerified && d.Position != nil &&
			(d.Position.Lat != 0 || d.Position.Lon != 0) {
			armed = append(armed, d)
		}
	}
	for i, a := range armed {
		for _, b := range armed[i+1:] {
			horizontal := geo.HaversineM(a.Position.Lat, a.Position.Lon,
				b.Position.Lat, b.Position.Lon)
			altGap := math.Abs(a.Position.AltRel - b.Position.AltRel)
			spatial := math.Hypot(horizontal, altGap)
			v.sampleCount++
			if horizontal < v.minObserved {
				v.minObserved = horizontal
			}
			if spatial < v.minSpatial {
				v.minSpatial = spatial
			}
			if spatial < minHorizontalM-0.05 ||
				(horizontal < minHorizontalM && altGap <= altConflictM) {
				v.collisionLog = append(v.collisionLog,
					fmt.Sprintf("COLLISION RISK phase=%s D%d/D%d horizontal=%.2fm altGap=%.2fm spatial=%.2fm "+
						"A=(%.7f,%.7f,%.2f) B=(%.7f,%.7f,%.2f)",
						v.phase, a.DroneId, b.DroneId, horizontal, altGap, spatial,
						a.Position.Lat, a.Position.Lon, a.Position.AltRel,
						b.Position.Lat, b.Position.Lon, b.Position.AltRel))
			}
		}
	}
}

func (v *verifier) cleanup() {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	_, _ = v.c.SwarmControl(ctx, &pb.SwarmControlRequest{Action: pb.SwarmControlRequest_STOP})
	_, _ = v.c.Land(ctx, &pb.LandRequest{Target: &pb.Target{DroneIds: v.ids}})
}

func count(items []*pb.Telemetry, predicate func(*pb.Telemetry) bool) int {
	n := 0
	for _, item := range items {
		if predicate(item) {
			n++
		}
	}
	return n
}

func countIDs(s *pb.FleetSnapshot, ids []uint32, predicate func(*pb.Telemetry) bool) int {
	wanted := make(map[uint32]bool, len(ids))
	for _, id := range ids {
		wanted[id] = true
	}
	return count(s.Drones, func(d *pb.Telemetry) bool {
		return wanted[d.DroneId] && predicate(d)
	})
}

func formationSettled(s *pb.FleetSnapshot, ids []uint32, spacing, altitude float64) bool {
	if len(s.Drones) < len(ids) {
		return false
	}
	byID := make(map[uint32]*pb.Telemetry, len(s.Drones))
	for _, d := range s.Drones {
		byID[d.DroneId] = d
	}
	dists := make([]float64, 0, len(ids)*(len(ids)-1)/2)
	for i, aid := range ids {
		a := byID[aid]
		if a == nil || a.Position == nil || !a.Armed || math.Abs(a.Position.AltRel-altitude) > 1.2 {
			return false
		}
		for _, bid := range ids[i+1:] {
			b := byID[bid]
			if b == nil || b.Position == nil {
				return false
			}
			dists = append(dists, geo.HaversineM(a.Position.Lat, a.Position.Lon,
				b.Position.Lat, b.Position.Lon))
		}
	}
	sort.Float64s(dists)
	// LINE slots are -2d,-d,0,+d,+2d...; compare the complete pair-distance
	// multiset so the initial launch line cannot be mistaken for settled formation.
	offsets := []float64{0}
	for i := 0; i < len(ids)-1; i++ {
		rank := float64(i/2+1) * spacing
		if i%2 == 0 {
			rank = -rank
		}
		offsets = append(offsets, rank)
	}
	want := make([]float64, 0, len(dists))
	for i, a := range offsets {
		for _, b := range offsets[i+1:] {
			want = append(want, math.Abs(a-b))
		}
	}
	sort.Float64s(want)
	if len(dists) != len(want) {
		return false
	}
	for i := range want {
		if math.Abs(dists[i]-want[i]) > 2.5 {
			return false
		}
	}
	return true
}

func init() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	if os.Getenv("SWARMGOD_PROFILE") == "production" {
		log.Fatal("sitlverify refuses production profile")
	}
}
