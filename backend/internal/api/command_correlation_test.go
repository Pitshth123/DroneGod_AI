package api

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/command"
)

func correlationContext(op, cmd, attempt string) context.Context {
	return metadata.NewIncomingContext(context.Background(), metadata.Pairs(
		correlationOperationHeader, op,
		correlationCommandHeader, cmd,
		correlationAttemptHeader, attempt,
	))
}

func TestCommandCorrelationMetadataRequiresCompleteTuple(t *testing.T) {
	ctx := correlationContext("op-1", "cmd-1", "attempt-1")
	got, ok := commandCorrelationFromContext(ctx)
	if !ok || got.operationID != "op-1" || got.commandID != "cmd-1" || got.attemptID != "attempt-1" {
		t.Fatalf("complete tuple not extracted: ok=%v got=%+v", ok, got)
	}
	partial := metadata.NewIncomingContext(context.Background(), metadata.Pairs(
		correlationOperationHeader, "op-1",
		correlationCommandHeader, "cmd-1",
	))
	if _, ok := commandCorrelationFromContext(partial); ok {
		t.Fatal("partial correlation metadata must be ignored")
	}
}

func TestCorrelationInterceptorReturnsHandlerResultAndErrorUnchanged(t *testing.T) {
	s := &Server{}
	wantResp := &pb.CommandResult{Ok: false, Command: "Arm", RequestId: "req-1"}
	wantErr := errors.New("transport-test")
	gotResp, gotErr := s.correlationAuditUnary()(
		correlationContext("op", "cmd", "attempt"),
		&pb.ArmRequest{RequestId: "req-1"},
		&grpc.UnaryServerInfo{FullMethod: "/swarmgod.v1.SwarmGodService/Arm"},
		func(context.Context, any) (any, error) { return wantResp, wantErr },
	)
	if gotResp != wantResp || gotErr != wantErr {
		t.Fatalf("correlation wrapper changed handler result: resp=%p/%p err=%v/%v", gotResp, wantResp, gotErr, wantErr)
	}
}

func readAuditEntries(t *testing.T, dir string) []map[string]any {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(dir, "audit-*.jsonl"))
	if err != nil || len(files) != 1 {
		t.Fatalf("audit file lookup: files=%v err=%v", files, err)
	}
	b, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var entry map[string]any
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			t.Fatalf("decode audit line: %v", err)
		}
		out = append(out, entry)
	}
	return out
}

func TestCorrelationAuditLinksAttemptToRequestAndOutcome(t *testing.T) {
	dir := t.TempDir()
	aud, err := audit.New(dir)
	if err != nil {
		t.Fatal(err)
	}
	svc := command.NewService(nil, nil, aud)
	s := &Server{cmd: svc}

	resp := &pb.CommandResult{
		Ok: true, Command: "Arm", RequestId: "req-linked",
		Outcome: pb.CommandOutcome_OUTCOME_ACCEPTED,
	}
	got, err := s.correlationAuditUnary()(
		correlationContext("op-linked", "ARM#1", "attempt-linked"),
		&pb.ArmRequest{RequestId: "req-linked"},
		&grpc.UnaryServerInfo{FullMethod: "/swarmgod.v1.SwarmGodService/Arm"},
		func(context.Context, any) (any, error) { return resp, nil },
	)
	if err != nil || got != resp {
		t.Fatalf("handler changed: got=%v err=%v", got, err)
	}
	aud.Close()

	entries := readAuditEntries(t, dir)
	var corr map[string]any
	for _, e := range entries {
		if e["type"] == "command_correlation" {
			corr = e
			break
		}
	}
	if corr == nil {
		t.Fatal("missing command_correlation audit record")
	}
	if corr["operation_id"] != "op-linked" || corr["command_id"] != "ARM#1" ||
		corr["attempt_id"] != "attempt-linked" || corr["request_id"] != "req-linked" ||
		corr["command"] != "Arm" || corr["outcome"] != pb.CommandOutcome_OUTCOME_ACCEPTED.String() ||
		corr["allowed"] != true {
		t.Fatalf("correlation record missing deterministic request/result linkage: %+v", corr)
	}
}

func TestCorrelationRequestCanJoinIdempotentReplayAudit(t *testing.T) {
	dir := t.TempDir()
	aud, err := audit.New(dir)
	if err != nil {
		t.Fatal(err)
	}
	svc := command.NewService(nil, nil, aud)
	s := &Server{cmd: svc}
	var executions atomic.Int32

	handler := func(context.Context, any) (any, error) {
		return svc.Idempotent("req-replay", 7, func() *pb.CommandResult {
			executions.Add(1)
			return &pb.CommandResult{Ok: true, DroneId: 7, Command: "Arm",
				RequestId: "req-replay", Outcome: pb.CommandOutcome_OUTCOME_ACCEPTED}
		}), nil
	}
	info := &grpc.UnaryServerInfo{FullMethod: "/swarmgod.v1.SwarmGodService/Arm"}
	for _, attempt := range []string{"attempt-1", "attempt-2"} {
		_, err := s.correlationAuditUnary()(
			correlationContext("op-replay", "ARM#7", attempt),
			&pb.ArmRequest{RequestId: "req-replay"}, info, handler)
		if err != nil {
			t.Fatal(err)
		}
	}
	if executions.Load() != 1 {
		t.Fatalf("same request_id executed %d times, want 1", executions.Load())
	}
	aud.Close()

	entries := readAuditEntries(t, dir)
	replayRequest := false
	correlatedAttempts := map[string]bool{}
	for _, e := range entries {
		if e["type"] == "request" && e["request_id"] == "req-replay" && e["replay"] == true {
			replayRequest = true
		}
		if e["type"] == "command_correlation" && e["request_id"] == "req-replay" {
			if a, _ := e["attempt_id"].(string); a != "" {
				correlatedAttempts[a] = true
			}
		}
	}
	if !replayRequest || !correlatedAttempts["attempt-1"] || !correlatedAttempts["attempt-2"] {
		t.Fatalf("request replay and correlation records are not joinable: replay=%v attempts=%v", replayRequest, correlatedAttempts)
	}
}

func TestAuthAndSetupSafetyRemainBeforeCorrelation(t *testing.T) {
	b, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "grpc.ChainUnaryInterceptor(s.auth.unary(), s.setupSafetyUnary(), s.correlationAuditUnary())") {
		t.Fatal("auth and setup safety must remain before correlation in the unary interceptor chain")
	}
}
