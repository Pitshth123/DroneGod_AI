// flyctl — Phase 3 test: สั่งโดรน SITL บินจริงผ่าน core (safety + audit)
//
//	connect -> รอ GPS -> takeoff 20m -> goto จุดใหม่ -> monitor altitude/pos
//	go run ./cmd/flyctl -core 127.0.0.1:50051 -sitl 127.0.0.1:5760
package main

import (
	"context"
	"flag"
	"log"
	"strconv"
	"strings"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
)

func main() {
	coreAddr := flag.String("core", "127.0.0.1:50051", "core gRPC")
	sitl := flag.String("sitl", "127.0.0.1:5760", "SITL host:port")
	flag.Parse()

	conn, err := clientcreds.Dial(*coreAddr)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	cli := pb.NewSwarmGodServiceClient(conn)
	ctx := context.Background()

	host, portStr, _ := strings.Cut(*sitl, ":")
	port, _ := strconv.Atoi(portStr)
	r, err := cli.Connect(ctx, &pb.ConnectRequest{DroneId: 1, Name: "UAV_1", Protocol: "tcp", Host: host, Port: uint32(port)})
	if err != nil {
		log.Fatalf("Connect: %v", err)
	}
	log.Printf("Connect: ok=%v %s", r.Ok, r.Message)

	// รอ GPS fix + telemetry verified
	log.Println("waiting for GPS fix...")
	var home *pb.Telemetry
	for i := 0; i < 40; i++ {
		snap, _ := cli.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		if len(snap.Drones) > 0 {
			d := snap.Drones[0]
			if d.SatCount >= 6 && int(d.GpsFix) >= 3 && d.TelemetryVerified {
				home = d
				log.Printf("GPS ready: sat=%d fix=%d pos=%.6f,%.6f bat=%.0f%%",
					d.SatCount, d.GpsFix, d.Position.Lat, d.Position.Lon, d.BatteryPct)
				break
			}
		}
		time.Sleep(1 * time.Second)
	}
	if home == nil {
		log.Fatal("no GPS fix — abort")
	}

	// TAKEOFF 20m
	log.Println(">>> TAKEOFF 20m")
	r, err = cli.Takeoff(ctx, &pb.TakeoffRequest{Target: &pb.Target{DroneIds: []uint32{1}}, Altitude: 20, Confirmed: true})
	if err != nil {
		log.Fatalf("Takeoff RPC: %v", err)
	}
	log.Printf("Takeoff: ok=%v %s", r.Ok, r.Message)

	monitor(cli, ctx, 14, "climbing")

	// GOTO ~30m เหนือ home
	tgtLat := home.Position.Lat + 0.00027
	tgtLon := home.Position.Lon
	log.Printf(">>> GOTO %.6f,%.6f alt=20", tgtLat, tgtLon)
	r, err = cli.Goto(ctx, &pb.GotoRequest{DroneId: 1, Lat: tgtLat, Lon: tgtLon, Alt: 20})
	if err != nil {
		log.Fatalf("Goto RPC: %v", err)
	}
	log.Printf("Goto: ok=%v %s", r.Ok, r.Message)

	monitor(cli, ctx, 16, "moving")

	// SAFETY DEMO: goto เกิน alt limit → ต้องถูก reject
	log.Println(">>> SAFETY TEST: goto alt=250m (เกิน limit 120m) — คาดว่า reject")
	r, _ = cli.Goto(ctx, &pb.GotoRequest{DroneId: 1, Lat: tgtLat, Lon: tgtLon, Alt: 250})
	log.Printf("Goto(250m): ok=%v msg=%q", r.Ok, r.Message)

	log.Println(">>> RTL")
	r, _ = cli.ReturnToLaunch(ctx, &pb.RtlRequest{Target: &pb.Target{DroneIds: []uint32{1}}})
	log.Printf("RTL: ok=%v %s", r.Ok, r.Message)
	monitor(cli, ctx, 6, "returning")
	log.Println("DONE")
}

func monitor(cli pb.SwarmGodServiceClient, ctx context.Context, secs int, tag string) {
	for i := 0; i < secs; i++ {
		snap, _ := cli.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		if len(snap.Drones) > 0 {
			d := snap.Drones[0]
			st := pb.LinkStatus_name[int32(d.Status)]
			md := pb.FlightMode_name[int32(d.Mode)]
			log.Printf("  [%s] %s %s armed=%v alt=%5.1fm spd=%.1f pos=%.6f,%.6f",
				tag, strings.TrimPrefix(st, "LINK_STATUS_"), strings.TrimPrefix(md, "FLIGHT_MODE_"),
				d.Armed, d.Position.AltRel, d.GroundSpeed, d.Position.Lat, d.Position.Lon)
		}
		time.Sleep(1 * time.Second)
	}
}
