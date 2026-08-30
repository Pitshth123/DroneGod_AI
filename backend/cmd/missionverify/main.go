// missionverify exercises the F4-F7 Core-owned single-drone mission path against
// ArduCopter SITL. It intentionally disconnects/reconnects the gRPC client while
// the Core keeps mission authority, then verifies WAIT and Cancel semantics.
package main

import (
	"context"
	"flag"
	"log"
	"strings"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
	"github.com/swarmgod/backend/pkg/geo"
	"google.golang.org/grpc"
)

type verifier struct {
	core string
	conn *grpc.ClientConn
	c    pb.SwarmGodServiceClient
}

func main() {
	core := flag.String("core", "127.0.0.1:50051", "Core gRPC address")
	port := flag.Uint("port", 5760, "single ArduCopter SITL TCP port")
	warmup := flag.Duration("warmup", 45*time.Second, "EKF/home warm-up after GPS readiness")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Minute)
	defer cancel()
	v := &verifier{core: *core}
	v.dial()
	defer v.close()
	defer v.landBestEffort()

	v.connect(ctx, uint32(*port))
	v.waitTelemetry(ctx, 120*time.Second, func(t *pb.Telemetry) bool {
		return t.TelemetryVerified && t.GpsFix >= pb.GpsFix_GPS_FIX_3D && t.SatCount >= 6
	}, "GPS ready")
	log.Printf("warming EKF/home for %s", *warmup)
	time.Sleep(*warmup)

	v.mustCommand(ctx, "TAKEOFF", func() (*pb.CommandResult, error) {
		return v.c.Takeoff(ctx, &pb.TakeoffRequest{Target: &pb.Target{DroneIds: []uint32{1}},
			Altitude: 20, Confirmed: true,
			RequestId: "missionverify-takeoff-" + time.Now().Format("150405.000000000")})
	})
	start := v.waitTelemetry(ctx, 120*time.Second, func(t *pb.Telemetry) bool {
		return t.Armed && t.Position != nil && t.Position.AltRel >= 17
	}, "airborne")

	lat0, lon0 := geo.OffsetM(start.Position.Lat, start.Position.Lon, 22, 0)
	lat1, lon1 := geo.OffsetM(start.Position.Lat, start.Position.Lon, 42, 0)
	plan := &pb.MissionPlan{
		PlanId:               "missionverify-route-wait",
		Mode:                 pb.MissionMode_MISSION_MODE_GROUPED,
		Participants:         []uint32{1},
		ParticipantAltitudes: map[uint32]float64{1: 20},
		ArrivalRadiusM:       3,
		Routes: []*pb.MissionRoute{{DroneId: 0, Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: lat0, Lon: lon0, Alt: 20, WaitSeconds: 8},
			{Seq: 1, Lat: lat1, Lon: lon1, Alt: 20},
		}}},
	}
	resp := v.startMission(ctx, plan, "missionverify-op-1")
	if !resp.Ok || !resp.AuthorityActive || resp.RunId == 0 {
		log.Fatalf("StartMission did not acquire Core authority: %+v", resp)
	}
	runID := resp.RunId
	dup := v.startMission(ctx, plan, "missionverify-op-1")
	if !dup.Ok || dup.RunId != runID {
		log.Fatalf("duplicate Start created/returned wrong run: first=%d duplicate=%+v", runID, dup)
	}
	log.Printf("duplicate Start idempotent: run=%d", runID)

	// Simulate cockpit/gRPC loss while the Core remains alive and authoritative.
	v.close()
	log.Printf("client disconnected during transit; Core must continue")
	time.Sleep(2 * time.Second)
	v.dial()
	st := v.waitMissionState(ctx, runID, 120*time.Second, func(s *pb.MissionStateResponse) bool {
		return s.State == pb.MissionRunState_MISSION_STATE_WAITING
	}, "WAITING")
	if st.Plan == nil || st.Plan.PlanId != plan.PlanId || len(st.Waits) != 1 {
		log.Fatalf("reconnect did not rebuild frozen mission/WAIT state: %+v", st)
	}
	log.Printf("reconnected to same WAIT run=%d remaining=%.1fs", st.RunId, st.Waits[0].RemainingS)

	// Disconnect again during WAIT. Reconnect must see the same run or its natural
	// next state; it must never create a replacement mission.
	v.close()
	time.Sleep(3 * time.Second)
	v.dial()
	st = v.missionState(ctx)
	if st.RunId != runID || st.PlanId != plan.PlanId {
		log.Fatalf("WAIT reconnect lost run identity: %+v", st)
	}
	log.Printf("WAIT reconnect preserved run=%d state=%s", st.RunId, st.State)

	st = v.waitMissionState(ctx, runID, 180*time.Second, func(s *pb.MissionStateResponse) bool {
		return s.State == pb.MissionRunState_MISSION_STATE_COMPLETED
	}, "mission completed")
	if st.Active {
		log.Fatalf("completed mission still active: %+v", st)
	}

	// New run: cancel close to transit and prove it stays cancelled after stale
	// telemetry continues arriving from the vehicle.
	cur := v.waitTelemetry(ctx, 15*time.Second, func(t *pb.Telemetry) bool { return t.Position != nil }, "position")
	clat, clon := geo.OffsetM(cur.Position.Lat, cur.Position.Lon, 0, 45)
	cancelPlan := &pb.MissionPlan{
		PlanId:       "missionverify-cancel",
		Mode:         pb.MissionMode_MISSION_MODE_GROUPED,
		Participants: []uint32{1}, ParticipantAltitudes: map[uint32]float64{1: 20},
		ArrivalRadiusM: 3,
		Routes: []*pb.MissionRoute{{DroneId: 0, Points: []*pb.MissionWaypoint{
			{Seq: 0, Lat: clat, Lon: clon, Alt: 20},
		}}},
	}
	cancelStart := v.startMission(ctx, cancelPlan, "missionverify-op-cancel")
	if !cancelStart.Ok || !cancelStart.AuthorityActive {
		log.Fatalf("cancel scenario start rejected: %+v", cancelStart)
	}
	time.Sleep(1200 * time.Millisecond)
	res, err := v.c.CancelMission(ctx, &pb.CancelMissionRequest{RunId: cancelStart.RunId, RequestId: "missionverify-cancel"})
	if err != nil || res == nil || !res.Ok {
		log.Fatalf("CancelMission: result=%+v err=%v", res, err)
	}
	st = v.missionState(ctx)
	if st.RunId != cancelStart.RunId || st.State != pb.MissionRunState_MISSION_STATE_CANCELLED || st.Active {
		log.Fatalf("cancel did not latch terminal state: %+v", st)
	}
	time.Sleep(5 * time.Second)
	st = v.missionState(ctx)
	if st.State != pb.MissionRunState_MISSION_STATE_CANCELLED {
		log.Fatalf("cancelled mission resumed from later telemetry: %+v", st)
	}
	log.Printf("PASS: Core mission authority survived disconnect/reconnect, WAIT, duplicate Start, and Cancel")
}

func (v *verifier) dial() {
	conn, err := clientcreds.Dial(v.core)
	if err != nil {
		log.Fatalf("dial Core: %v", err)
	}
	v.conn = conn
	v.c = pb.NewSwarmGodServiceClient(conn)
}

func (v *verifier) close() {
	if v.conn != nil {
		_ = v.conn.Close()
		v.conn = nil
		v.c = nil
	}
}

func (v *verifier) connect(ctx context.Context, port uint32) {
	res, err := v.c.Connect(ctx, &pb.ConnectRequest{DroneId: 1, Name: "MISSION_SITL",
		Protocol: "tcp", Host: "127.0.0.1", Port: port})
	if err != nil {
		log.Fatalf("CONNECT RPC: %v", err)
	}
	if res == nil {
		log.Fatal("CONNECT returned no result")
	}
	if !res.Ok && !strings.Contains(strings.ToLower(res.Message), "already connected") {
		log.Fatalf("CONNECT rejected: %s", res.Message)
	}
	log.Printf("CONNECT ready: %s", res.Message)
}

func (v *verifier) startMission(ctx context.Context, plan *pb.MissionPlan, op string) *pb.StartMissionResponse {
	resp, err := v.c.StartMission(ctx, &pb.StartMissionRequest{Plan: plan, OperationId: op})
	if err != nil {
		log.Fatalf("StartMission: %v", err)
	}
	return resp
}

func (v *verifier) missionState(ctx context.Context) *pb.MissionStateResponse {
	st, err := v.c.GetMissionState(ctx, &pb.GetMissionStateRequest{})
	if err != nil {
		log.Fatalf("GetMissionState: %v", err)
	}
	return st
}

func (v *verifier) waitMissionState(ctx context.Context, runID uint64, timeout time.Duration,
	ready func(*pb.MissionStateResponse) bool, label string) *pb.MissionStateResponse {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		st := v.missionState(ctx)
		if st.RunId != runID {
			log.Fatalf("%s: run changed from %d to %d", label, runID, st.RunId)
		}
		if st.State == pb.MissionRunState_MISSION_STATE_FAILED || st.State == pb.MissionRunState_MISSION_STATE_INTERRUPTED {
			log.Fatalf("%s: mission terminal failure state=%s reason=%s", label, st.State, st.TerminalReason)
		}
		if ready(st) {
			log.Printf("%s: state=%s index=%d revision=%d", label, st.State, st.CurrentIndex, st.Revision)
			return st
		}
		time.Sleep(250 * time.Millisecond)
	}
	log.Fatalf("timeout waiting for mission state %s", label)
	return nil
}

func (v *verifier) waitTelemetry(ctx context.Context, timeout time.Duration,
	ready func(*pb.Telemetry) bool, label string) *pb.Telemetry {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		s, err := v.c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		if err != nil {
			log.Fatalf("GetFleetSnapshot: %v", err)
		}
		for _, t := range s.Drones {
			if t.DroneId == 1 && ready(t) {
				log.Printf("%s: alt=%.1f sat=%d", label, t.GetPosition().GetAltRel(), t.SatCount)
				return t
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	log.Fatalf("timeout waiting for %s", label)
	return nil
}

func (v *verifier) mustCommand(ctx context.Context, label string, call func() (*pb.CommandResult, error)) {
	res, err := call()
	if err != nil || res == nil || !res.Ok {
		log.Fatalf("%s: result=%+v err=%v", label, res, err)
	}
	log.Printf("%s accepted: %s", label, res.Message)
}

func (v *verifier) landBestEffort() {
	if v.c == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	res, err := v.c.Land(ctx, &pb.LandRequest{
		Target:    &pb.Target{DroneIds: []uint32{1}},
		RequestId: "missionverify-cleanup-" + time.Now().Format("150405.000000000"),
	})
	if err != nil || res == nil || !res.Ok {
		log.Printf("cleanup LAND warning: result=%+v err=%v", res, err)
		return
	}
	deadline := time.Now().Add(55 * time.Second)
	for time.Now().Before(deadline) {
		s, err := v.c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		if err != nil {
			log.Printf("cleanup snapshot warning: %v", err)
			return
		}
		for _, t := range s.Drones {
			if t.DroneId == 1 && !t.Armed && t.Position != nil && t.Position.AltRel < 1 {
				log.Printf("cleanup landed/disarmed")
				return
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	log.Printf("cleanup LAND warning: timeout waiting for landed/disarmed")
}

func init() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}
