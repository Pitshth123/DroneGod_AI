// missioncrashprobe is a two-stage SITL helper for F7 Core-process failure.
//
//	missioncrashprobe -mode start  : ensure D1 airborne, start a long Core-owned
//	                                 mission, then exit while Core remains alive.
//	(test harness kills/restarts Core here)
//	missioncrashprobe -mode check  : assert restarted Core has no active mission,
//	                                 reconnect D1, and print FC state for the
//	                                 configured onboard-failsafe evidence.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"strings"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
	"github.com/swarmgod/backend/pkg/geo"
)

func main() {
	mode := flag.String("mode", "check", "start|check|expect-interrupted")
	core := flag.String("core", "127.0.0.1:50053", "Core gRPC address")
	port := flag.Uint("port", 5760, "SITL TCP port")
	northM := flag.Float64("north", 250, "north offset for start-mode mission target (negative = south)")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	c := pb.NewSwarmGodServiceClient(conn)

	switch *mode {
	case "start":
		connect(ctx, c, uint32(*port))
		t := waitTelemetry(ctx, c, 60*time.Second, func(t *pb.Telemetry) bool {
			return t.TelemetryVerified && t.Position != nil && t.GpsFix >= pb.GpsFix_GPS_FIX_3D
		})
		if !t.Armed || t.Position.AltRel < 12 {
			res, err := c.Takeoff(ctx, &pb.TakeoffRequest{
				Target: &pb.Target{DroneIds: []uint32{1}}, Altitude: 20, Confirmed: true,
				RequestId: fmt.Sprintf("missioncrashprobe-takeoff-%d", time.Now().UnixNano()),
			})
			must(res, err, "TAKEOFF")
			t = waitTelemetry(ctx, c, 120*time.Second, func(t *pb.Telemetry) bool {
				return t.Armed && t.Position != nil && t.Position.AltRel >= 17
			})
		}
		lat, lon := geo.OffsetM(t.Position.Lat, t.Position.Lon, *northM, 0)
		plan := &pb.MissionPlan{
			PlanId:       "missioncrashprobe-active",
			Mode:         pb.MissionMode_MISSION_MODE_GROUPED,
			Participants: []uint32{1}, ParticipantAltitudes: map[uint32]float64{1: 20},
			ArrivalRadiusM: 3,
			Routes: []*pb.MissionRoute{{DroneId: 0, Points: []*pb.MissionWaypoint{
				{Seq: 0, Lat: lat, Lon: lon, Alt: 20},
			}}},
		}
		start, err := c.StartMission(ctx, &pb.StartMissionRequest{Plan: plan, OperationId: "missioncrashprobe-op"})
		if err != nil || start == nil || !start.Ok || !start.AuthorityActive {
			log.Fatalf("StartMission: resp=%+v err=%v", start, err)
		}
		log.Printf("ACTIVE RUN READY FOR CORE KILL: run=%d start=%.7f,%.7f target=%.7f,%.7f", start.RunId, t.Position.Lat, t.Position.Lon, lat, lon)
	case "check":
		st, err := c.GetMissionState(ctx, &pb.GetMissionStateRequest{})
		if err != nil {
			log.Fatalf("GetMissionState: %v", err)
		}
		if st.Active || st.RunId != 0 || st.State != pb.MissionRunState_MISSION_STATE_IDLE {
			log.Fatalf("restarted Core silently retained/resumed mission: %+v", st)
		}
		log.Printf("Core restart mission state PASS: IDLE / no run")
		connect(ctx, c, uint32(*port))
		t := waitTelemetry(ctx, c, 60*time.Second, func(t *pb.Telemetry) bool {
			return t.TelemetryVerified && t.Position != nil && t.GpsFix >= pb.GpsFix_GPS_FIX_3D && t.SatCount >= 6
		})
		log.Printf("FC AFTER CORE GAP: armed=%v mode=%s lat=%.7f lon=%.7f alt=%.1f speed=%.1f gps=%s sat=%d",
			t.Armed, t.Mode, t.Position.Lat, t.Position.Lon, t.Position.AltRel, t.GroundSpeed, t.GpsFix, t.SatCount)
		log.Printf("NOTE: FC state above is characterization evidence; onboard GCS-failsafe policy must be checked against intended field configuration before hardware flight")
	case "expect-interrupted":
		st, err := c.GetMissionState(ctx, &pb.GetMissionStateRequest{})
		if err != nil {
			log.Fatalf("GetMissionState: %v", err)
		}
		if st.State != pb.MissionRunState_MISSION_STATE_INTERRUPTED || st.Active {
			log.Fatalf("mission did not latch INTERRUPTED after FC link loss: %+v", st)
		}
		log.Printf("Mission link-loss preemption PASS: run=%d state=%s reason=%s", st.RunId, st.State, st.TerminalReason)
	default:
		log.Fatalf("unknown mode %q", *mode)
	}
}

func connect(ctx context.Context, c pb.SwarmGodServiceClient, port uint32) {
	res, err := c.Connect(ctx, &pb.ConnectRequest{DroneId: 1, Name: "MISSION_CRASH_SITL",
		Protocol: "tcp", Host: "127.0.0.1", Port: port})
	if err != nil {
		log.Fatalf("CONNECT RPC: %v", err)
	}
	if res != nil && !res.Ok && !strings.Contains(strings.ToLower(res.Message), "already connected") {
		log.Fatalf("CONNECT rejected: %s", res.Message)
	}
}

func waitTelemetry(ctx context.Context, c pb.SwarmGodServiceClient, timeout time.Duration,
	ready func(*pb.Telemetry) bool) *pb.Telemetry {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		s, err := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		if err != nil {
			log.Fatalf("snapshot: %v", err)
		}
		for _, t := range s.Drones {
			if t.DroneId == 1 && ready(t) {
				return t
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
	log.Fatal("timeout waiting for telemetry")
	return nil
}

func must(res *pb.CommandResult, err error, label string) {
	if err != nil || res == nil || !res.Ok {
		log.Fatalf("%s: result=%+v err=%v", label, res, err)
	}
}

func init() { log.SetFlags(log.LstdFlags | log.Lmicroseconds) }
