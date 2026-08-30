// benchack validates Core→FC COMMAND_ACK timing in a bench-safe way.
// Default is observation-only. Execution requires an explicit props-removed token
// and refuses to send anything while the vehicle is armed.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/pkg/clientcreds"
)

const confirmation = "PROPS-REMOVED-BENCH"

type ackResult struct {
	Command    string  `json:"command"`
	ElapsedMs  float64 `json:"elapsed_ms"`
	OK         bool    `json:"ok"`
	Outcome    string  `json:"outcome"`
	ResultCode int32   `json:"result_code"`
	Message    string  `json:"message"`
}

type report struct {
	GeneratedAt string     `json:"generated_at"`
	Core        string     `json:"core"`
	DroneID     uint32     `json:"drone_id"`
	Execute     bool       `json:"execute"`
	Armed       bool       `json:"armed"`
	InitialMode string     `json:"initial_mode"`
	TargetMode  string     `json:"target_mode"`
	Set         *ackResult `json:"set,omitempty"`
	Restore     *ackResult `json:"restore,omitempty"`
	Note        string     `json:"note"`
}

func main() {
	core := flag.String("core", "127.0.0.1:50051", "Core gRPC address")
	drone := flag.Uint("drone", 1, "drone id")
	execute := flag.Bool("execute", false, "actually issue bench mode command")
	confirm := flag.String("confirm", "", "must equal PROPS-REMOVED-BENCH when --execute is used")
	out := flag.String("out", "", "optional JSON output path")
	flag.Parse()

	conn, err := clientcreds.Dial(*core)
	if err != nil {
		log.Fatal(err)
	}
	defer conn.Close()
	c := pb.NewSwarmGodServiceClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	snap, err := c.GetFleetSnapshot(ctx, &pb.FleetSnapshotRequest{})
	if err != nil {
		log.Fatalf("GetFleetSnapshot: %v", err)
	}
	var t *pb.Telemetry
	for _, candidate := range snap.GetDrones() {
		if candidate.GetDroneId() == uint32(*drone) {
			t = candidate
			break
		}
	}
	if t == nil || !t.GetTelemetryVerified() {
		log.Fatalf("drone %d has no verified telemetry", *drone)
	}
	r := report{GeneratedAt: time.Now().Format(time.RFC3339), Core: *core,
		DroneID: uint32(*drone), Execute: *execute, Armed: t.GetArmed(),
		InitialMode: t.GetMode().String(), TargetMode: pb.FlightMode_FLIGHT_MODE_GUIDED.String()}

	if !*execute {
		r.Note = "OBSERVE ONLY — no command sent; rerun with --execute --confirm=" + confirmation + " on a physically safe bench"
		writeReport(r, *out)
		return
	}
	if *confirm != confirmation {
		log.Fatalf("--execute requires --confirm=%s", confirmation)
	}
	if t.GetArmed() {
		log.Fatal("refuse bench ACK test while vehicle is armed")
	}
	if t.GetMode() == pb.FlightMode_FLIGHT_MODE_UNKNOWN {
		log.Fatal("refuse mode mutation because initial mode is UNKNOWN and cannot be restored")
	}

	r.Set = timed("SetMode(GUIDED)", func() (*pb.CommandResult, error) {
		return c.SetMode(ctx, &pb.SetModeRequest{Target: &pb.Target{DroneIds: []uint32{uint32(*drone)}}, Mode: pb.FlightMode_FLIGHT_MODE_GUIDED,
			RequestId: fmt.Sprintf("f9-benchack-set-%d", time.Now().UnixNano())})
	})
	if !r.Set.OK {
		r.Note = "target mode command failed/rejected; restore not attempted"
		writeReport(r, *out)
		os.Exit(1)
	}

	r.Restore = timed("SetMode(restore)", func() (*pb.CommandResult, error) {
		return c.SetMode(ctx, &pb.SetModeRequest{Target: &pb.Target{DroneIds: []uint32{uint32(*drone)}}, Mode: t.GetMode(),
			RequestId: fmt.Sprintf("f9-benchack-restore-%d", time.Now().UnixNano())})
	})
	if !r.Restore.OK {
		r.Note = "GUIDED ACK succeeded but restore failed — operator must verify mode before further bench work"
		writeReport(r, *out)
		os.Exit(1)
	}
	r.Note = "bench mode ACK and restore both accepted; no arm/takeoff/navigation command was issued"
	writeReport(r, *out)
}

func timed(name string, fn func() (*pb.CommandResult, error)) *ackResult {
	start := time.Now()
	res, err := fn()
	elapsed := time.Since(start).Seconds() * 1000
	if err != nil {
		return &ackResult{Command: name, ElapsedMs: elapsed, OK: false, Outcome: "RPC_ERROR", Message: err.Error()}
	}
	if res == nil {
		return &ackResult{Command: name, ElapsedMs: elapsed, OK: false, Outcome: "NO_RESULT", Message: "nil result"}
	}
	evidence := res
	if res.GetOutcome() == pb.CommandOutcome_OUTCOME_UNKNOWN && len(res.GetPerDrone()) == 1 {
		evidence = res.GetPerDrone()[0]
	}
	return &ackResult{Command: name, ElapsedMs: elapsed, OK: res.GetOk(), Outcome: evidence.GetOutcome().String(),
		ResultCode: evidence.GetResultCode(), Message: evidence.GetMessage()}
}

func writeReport(r report, out string) {
	b, err := json.MarshalIndent(r, "", "  ")
	if err != nil {
		log.Fatal(err)
	}
	b = append(b, '\n')
	if out == "" {
		_, _ = os.Stdout.Write(b)
		return
	}
	if err := os.WriteFile(out, b, 0o644); err != nil {
		log.Fatal(err)
	}
	fmt.Printf("wrote %s\n", out)
}
