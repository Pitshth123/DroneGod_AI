// benchprobe is the F9A non-flight Core/telemetry recorder.
// By default it is read-only. With --connect-port it may establish the Core↔FC
// transport, but it never issues Arm/Takeoff/Goto/Hold/Land or parameter writes.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/bench"
	"github.com/swarmgod/backend/pkg/clientcreds"
)

type report struct {
	GeneratedAt       string              `json:"generated_at"`
	Core              string              `json:"core"`
	DroneID           uint32              `json:"drone_id"`
	DurationSeconds   float64             `json:"duration_seconds"`
	Timing            bench.TimingSummary `json:"timing"`
	TelemetryVerified bool                `json:"telemetry_verified"`
	LinkQualityMin    uint32              `json:"link_quality_min"`
	LinkQualityMax    uint32              `json:"link_quality_max"`
	DropRateMax       uint32              `json:"drop_rate_max_permille"`
	RSSIAvailable     bool                `json:"rssi_available"`
	RSSIMinDbm        int32               `json:"rssi_min_dbm,omitempty"`
	RSSIMaxDbm        int32               `json:"rssi_max_dbm,omitempty"`
	LinkWarnSeconds   float64             `json:"link_warn_seconds"`
	LinkLostSeconds   float64             `json:"link_lost_seconds"`
	BelowWarnMargin   bool                `json:"timing_below_link_warn_margin"`
	Mission           missionSnapshot     `json:"mission"`
}

type missionSnapshot struct {
	Active          bool   `json:"active"`
	AuthorityActive bool   `json:"authority_active"`
	RunID           uint64 `json:"run_id"`
	PlanID          string `json:"plan_id"`
	State           string `json:"state"`
	Revision        uint64 `json:"revision"`
	TerminalReason  string `json:"terminal_reason,omitempty"`
}

func main() {
	core := flag.String("core", "127.0.0.1:50051", "Core gRPC address")
	drone := flag.Uint("drone", 1, "drone id to observe")
	duration := flag.Duration("duration", 30*time.Second, "sample duration")
	maxHz := flag.Uint("max-hz", 20, "requested telemetry stream ceiling")
	connectPort := flag.Uint("connect-port", 0, "optional FC/SITL TCP port to connect through Core; 0 = do not connect")
	connectHost := flag.String("connect-host", "127.0.0.1", "host used with --connect-port")
	connectProto := flag.String("connect-proto", "tcp", "transport used with --connect-port")
	linkWarn := flag.Float64("link-warn", 3.0, "Core LinkWarnSec used for timing margin check")
	linkLost := flag.Float64("link-lost", 10.0, "Core LinkLostSec recorded in evidence")
	out := flag.String("out", "", "optional JSON output path; stdout when empty")
	flag.Parse()
	if *duration <= 0 || *duration > 30*time.Minute {
		log.Fatal("duration must be >0 and <=30m")
	}
	if *linkWarn <= 0 || *linkLost <= *linkWarn {
		log.Fatal("link thresholds must satisfy 0 < link-warn < link-lost")
	}

	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	c := pb.NewSwarmGodServiceClient(conn)
	if *connectPort != 0 {
		cctx, ccancel := context.WithTimeout(context.Background(), 10*time.Second)
		res, err := c.Connect(cctx, &pb.ConnectRequest{DroneId: uint32(*drone), Name: "F9_BENCH_PROBE",
			Protocol: *connectProto, Host: *connectHost, Port: uint32(*connectPort)})
		ccancel()
		if err != nil {
			log.Fatalf("Connect: %v", err)
		}
		if res == nil || (!res.GetOk() && !strings.Contains(strings.ToLower(res.GetMessage()), "already connected")) {
			log.Fatalf("Connect rejected: %+v", res)
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), *duration+15*time.Second)
	defer cancel()
	stream, err := c.SubscribeTelemetry(ctx, &pb.SubscribeTelemetryRequest{
		DroneIds: []uint32{uint32(*drone)}, MaxHz: uint32(*maxHz),
	})
	if err != nil {
		log.Fatalf("SubscribeTelemetry: %v", err)
	}

	deadline := time.Now().Add(*duration)
	received := make([]int64, 0, int(duration.Seconds())*int(*maxHz)+16)
	source := make([]int64, 0, cap(received))
	var first bool
	var verified bool
	var lqMin, lqMax, dropMax uint32
	var rssiSeen bool
	var rssiMin, rssiMax int32
	for time.Now().Before(deadline) {
		t, err := stream.Recv()
		if err != nil {
			if err == io.EOF || ctx.Err() != nil {
				break
			}
			log.Fatalf("telemetry recv: %v", err)
		}
		if t.GetDroneId() != uint32(*drone) {
			continue
		}
		now := time.Now().UnixMilli()
		received = append(received, now)
		source = append(source, t.GetTimestampMs())
		verified = verified || t.GetTelemetryVerified()
		if !first {
			first = true
			lqMin, lqMax = t.GetLinkQuality(), t.GetLinkQuality()
		} else {
			if t.GetLinkQuality() < lqMin {
				lqMin = t.GetLinkQuality()
			}
			if t.GetLinkQuality() > lqMax {
				lqMax = t.GetLinkQuality()
			}
		}
		if t.GetDropRate() > dropMax {
			dropMax = t.GetDropRate()
		}
		if t.GetRssiValid() {
			if !rssiSeen {
				rssiMin, rssiMax = t.GetRssi(), t.GetRssi()
				rssiSeen = true
			} else {
				if t.GetRssi() < rssiMin {
					rssiMin = t.GetRssi()
				}
				if t.GetRssi() > rssiMax {
					rssiMax = t.GetRssi()
				}
			}
		}
	}
	if len(received) == 0 {
		log.Fatal("no telemetry samples received")
	}

	mctx, mcancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer mcancel()
	ms, err := c.GetMissionState(mctx, &pb.GetMissionStateRequest{})
	if err != nil {
		log.Fatalf("GetMissionState: %v", err)
	}

	timing := bench.SummarizeTiming(received, source)
	r := report{
		GeneratedAt: time.Now().Format(time.RFC3339), Core: *core, DroneID: uint32(*drone),
		DurationSeconds: duration.Seconds(), Timing: timing,
		TelemetryVerified: verified, LinkQualityMin: lqMin, LinkQualityMax: lqMax,
		DropRateMax: dropMax, RSSIAvailable: rssiSeen, RSSIMinDbm: rssiMin, RSSIMaxDbm: rssiMax,
		LinkWarnSeconds: *linkWarn, LinkLostSeconds: *linkLost,
		BelowWarnMargin: timing.IntervalMaxMs < *linkWarn*1000 && timing.AgeMaxMs < *linkWarn*1000,
		Mission: missionSnapshot{Active: ms.GetActive(), AuthorityActive: ms.GetAuthorityActive(),
			RunID: ms.GetRunId(), PlanID: ms.GetPlanId(), State: ms.GetState().String(),
			Revision: ms.GetRevision(), TerminalReason: ms.GetTerminalReason()},
	}

	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		log.Fatal(err)
	}
	b = append(b, '\n')
	if *out == "" {
		_, _ = os.Stdout.Write(b)
		return
	}
	if err := os.WriteFile(*out, b, 0o644); err != nil {
		log.Fatalf("write report: %v", err)
	}
	fmt.Printf("wrote %s\n", *out)
}
