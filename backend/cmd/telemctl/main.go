// telemctl — gRPC test client สำหรับ Phase 1
// ต่อ core → Connect โดรน (ถ้าให้ -connect) → SubscribeTelemetry → พิมพ์ทุก 1s
//
//	go run ./cmd/telemctl -addr 127.0.0.1:50051 -connect 127.0.0.1:5760
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
	addr := flag.String("addr", "127.0.0.1:50051", "core gRPC address")
	connect := flag.String("connect", "", "SITL host:port to Connect first (optional)")
	flag.Parse()

	conn, err := clientcreds.Dial(*addr)
	if err != nil {
		log.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	cli := pb.NewSwarmGodServiceClient(conn)

	if *connect != "" {
		host, portStr, _ := strings.Cut(*connect, ":")
		port, _ := strconv.Atoi(portStr)
		res, err := cli.Connect(context.Background(), &pb.ConnectRequest{
			DroneId: 1, Name: "UAV_1", Protocol: "tcp", Host: host, Port: uint32(port),
		})
		if err != nil {
			log.Fatalf("Connect RPC: %v", err)
		}
		log.Printf("Connect → ok=%v msg=%q", res.Ok, res.Message)
	}

	stream, err := cli.SubscribeTelemetry(context.Background(), &pb.SubscribeTelemetryRequest{})
	if err != nil {
		log.Fatalf("SubscribeTelemetry: %v", err)
	}
	log.Println("subscribed — waiting for telemetry...")

	var lastPrint time.Time
	for {
		t, err := stream.Recv()
		if err != nil {
			log.Fatalf("stream: %v", err)
		}
		if time.Since(lastPrint) < time.Second {
			continue // print ~1Hz
		}
		lastPrint = time.Now()
		p := t.Position
		log.Printf("UAV_%d %-11s mode=%-9s armed=%-5v alt=%6.1fm bat=%3.0f%% %.2fV sat=%2d fix=%d hdg=%3.0f pos=%.6f,%.6f",
			t.DroneId, t.Status, t.Mode, t.Armed, p.AltRel, t.BatteryPct, t.Voltage,
			t.SatCount, t.GpsFix, t.Heading, p.Lat, p.Lon)
	}
}
