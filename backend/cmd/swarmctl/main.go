// swarmctl — Phase 5 test: connect 3 SITL → takeoff → FORM UP → move leader → failover
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
)

func main() {
	core := flag.String("core", "127.0.0.1:50051", "core gRPC")
	flag.Parse()

	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	c := pb.NewSwarmGodServiceClient(conn)
	ctx := context.Background()

	// connect 3 drones (5760/5770/5780)
	for i := uint32(1); i <= 3; i++ {
		port := 5760 + (i-1)*10
		r, _ := c.Connect(ctx, &pb.ConnectRequest{DroneId: i, Name: fmt.Sprintf("UAV_%d", i),
			Protocol: "tcp", Host: "127.0.0.1", Port: port})
		log.Printf("Connect UAV_%d (:%d): %v %s", i, port, r.Ok, r.Message)
	}

	log.Println("waiting for GPS on all 3...")
	for i := 0; i < 60; i++ {
		snap, _ := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		ready := 0
		for _, d := range snap.Drones {
			if d.SatCount >= 6 && int(d.GpsFix) >= 3 && d.TelemetryVerified {
				ready++
			}
		}
		if ready >= 3 {
			log.Println("all 3 GPS ready")
			break
		}
		time.Sleep(1 * time.Second)
	}

	// takeoff all 3 to 20m
	log.Println(">>> TAKEOFF x3 @ 20m")
	r, _ := c.Takeoff(ctx, &pb.TakeoffRequest{Target: &pb.Target{DroneIds: []uint32{1, 2, 3}}, Altitude: 20, Confirmed: true})
	log.Printf("Takeoff: %v %s", r.Ok, r.Message)
	monitor(c, ctx, 8, "airborne")

	// FORM UP
	log.Println(">>> FORM UP (spacing 12m)")
	c.SetSwarmConfig(ctx, &pb.SwarmConfig{Spacing: 12})
	rs, _ := c.SwarmControl(ctx, &pb.SwarmControlRequest{Action: pb.SwarmControlRequest_START})
	log.Printf("SwarmStart: %v %s", rs.Ok, rs.Message)
	monitor(c, ctx, 10, "forming")

	// move leader north ~60m → followers should track
	log.Println(">>> MOVE LEADER (goto UAV_1 north ~60m)")
	c.Goto(ctx, &pb.GotoRequest{DroneId: 1, Lat: 14.9581695 + 0.00054, Lon: 102.0986187, Alt: 20})
	monitor(c, ctx, 12, "following")

	// FAILOVER: disconnect leader UAV_1 → UAV_2 should become leader
	log.Println(">>> FAILOVER: disconnect leader UAV_1")
	c.Disconnect(ctx, &pb.DisconnectRequest{DroneId: 1})
	monitor(c, ctx, 12, "failover")

	log.Println("DONE")
}

func monitor(c pb.SwarmGodServiceClient, ctx context.Context, secs int, tag string) {
	for i := 0; i < secs; i++ {
		snap, _ := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		sw, _ := c.GetSwarmState(ctx, &pb.SwarmStateRequest{})
		leader := "-"
		if sw != nil && sw.Active {
			leader = fmt.Sprintf("UAV_%d", sw.LeaderId)
		}
		line := fmt.Sprintf("  [%s] leader=%s |", tag, leader)
		for _, d := range snap.Drones {
			line += fmt.Sprintf(" UAV_%d(%s a%.0f %.5f,%.5f)", d.DroneId,
				trim(pb.LinkStatus_name[int32(d.Status)]), d.Position.AltRel,
				d.Position.Lat, d.Position.Lon)
		}
		log.Println(line)
		time.Sleep(1 * time.Second)
	}
}

func trim(s string) string {
	const p = "LINK_STATUS_"
	if len(s) > len(p) {
		return s[len(p):]
	}
	return s
}
