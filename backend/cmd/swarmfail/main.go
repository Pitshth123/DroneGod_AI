// swarmfail — เทสต์: 3 ลำ แม่=UAV_1 → (kill SITL ตัวแม่ภายนอก) →
// ต้องเห็น failsafe RTL ของ UAV_1 + UAV_2 ขึ้นเป็นแม่แทน
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"sync"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
)

func main() {
	core := flag.String("core", "127.0.0.1:50051", "core gRPC")
	secs := flag.Int("secs", 45, "monitor duration")
	flag.Parse()

	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	c := pb.NewSwarmGodServiceClient(conn)
	ctx := context.Background()

	for i := uint32(1); i <= 3; i++ {
		port := 5760 + (i-1)*10
		c.Connect(ctx, &pb.ConnectRequest{DroneId: i, Name: fmt.Sprintf("UAV_%d", i),
			Protocol: "tcp", Host: "127.0.0.1", Port: port})
	}
	log.Println("waiting GPS on 3...")
	for i := 0; i < 60; i++ {
		snap, _ := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		ready := 0
		for _, d := range snap.Drones {
			if d.SatCount >= 6 && int(d.GpsFix) >= 3 && d.TelemetryVerified {
				ready++
			}
		}
		if ready >= 3 {
			break
		}
		time.Sleep(time.Second)
	}

	log.Println(">>> TAKEOFF x3 (parallel) @ 20m")
	var wg sync.WaitGroup
	for i := uint32(1); i <= 3; i++ {
		wg.Add(1)
		go func(id uint32) {
			defer wg.Done()
			c.Takeoff(ctx, &pb.TakeoffRequest{Target: &pb.Target{DroneIds: []uint32{id}}, Altitude: 20, Confirmed: true})
		}(i)
	}
	wg.Wait()
	time.Sleep(4 * time.Second)

	log.Println(">>> FORM UP (WEDGE, 12m) leader=UAV_1")
	c.SetSwarmConfig(ctx, &pb.SwarmConfig{Spacing: 12, Formation: pb.Formation_FORMATION_WEDGE})
	c.SwarmControl(ctx, &pb.SwarmControlRequest{Action: pb.SwarmControlRequest_START})

	// subscribe events (จะโชว์ failsafe RTL)
	go func() {
		st, err := c.SubscribeEvents(ctx, &pb.EventSubscribeRequest{})
		if err != nil {
			return
		}
		for {
			ev, err := st.Recv()
			if err != nil {
				return
			}
			if ev.Level >= pb.EventLevel_EVENT_LEVEL_WARN {
				log.Printf("  ⚠ EVENT [%s] UAV_%d: %s", ev.Category, ev.DroneId, ev.Message)
			}
		}
	}()

	log.Println("=== monitor (kill UAV_1 SITL ระหว่างนี้) ===")
	for i := 0; i < *secs; i++ {
		snap, _ := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
		sw, _ := c.GetSwarmState(ctx, &pb.SwarmStateRequest{})
		leader := uint32(0)
		if sw != nil {
			leader = sw.LeaderId
		}
		line := fmt.Sprintf("  leader=UAV_%d |", leader)
		for _, d := range snap.Drones {
			tag := trim(pb.LinkStatus_name[int32(d.Status)])
			link := d.ConnectElapsed
			_ = link
			line += fmt.Sprintf(" UAV_%d(%s %s a%.0f)", d.DroneId, tag,
				trim2(pb.FlightMode_name[int32(d.Mode)]), d.Position.AltRel)
		}
		log.Println(line)
		time.Sleep(time.Second)
	}
	log.Println("DONE")
}

func trim(s string) string {
	const p = "LINK_STATUS_"
	if len(s) > len(p) {
		return s[len(p):]
	}
	return s
}
func trim2(s string) string {
	const p = "FLIGHT_MODE_"
	if len(s) > len(p) {
		return s[len(p):]
	}
	return s
}
