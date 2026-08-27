package api

import (
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// PARTIAL-001 (§8.3): group ต้องเก็บผลรายลำใน PerDrone ไม่ collapse
func TestAggregatePartialSuccess(t *testing.T) {
	results := []*pb.CommandResult{
		{Ok: true, DroneId: 1, Command: "Arm", Outcome: pb.CommandOutcome_OUTCOME_ACCEPTED},
		{Ok: false, DroneId: 2, Command: "Arm", Message: "not connected",
			Outcome: pb.CommandOutcome_OUTCOME_NOT_CONNECTED},
		{Ok: false, DroneId: 3, Command: "Arm", Message: "battery too low",
			Outcome: pb.CommandOutcome_OUTCOME_SAFETY_REJECTED},
	}
	top := aggregate("Arm", "req-99", results)

	// ผลรายลำต้องครบ 3 (ห้าม collapse)
	if len(top.PerDrone) != 3 {
		t.Fatalf("PerDrone = %d, want 3 (ห้าม collapse §8.3)", len(top.PerDrone))
	}
	// มีลำ fail → top.Ok=false
	if top.Ok {
		t.Fatal("top.Ok ควร false เมื่อมีลำ fail")
	}
	// parent request id stamp ทั้งบนสุดและทุก child
	if top.RequestId != "req-99" {
		t.Fatalf("top.RequestId = %q", top.RequestId)
	}
	for _, r := range top.PerDrone {
		if r.RequestId != "req-99" {
			t.Fatalf("child UAV_%d ไม่ถูก stamp request_id", r.DroneId)
		}
	}
	// outcome ต้องคงอยู่รายลำ (taxonomy §8.3)
	if top.PerDrone[1].Outcome != pb.CommandOutcome_OUTCOME_NOT_CONNECTED {
		t.Fatalf("outcome รายลำหาย: %v", top.PerDrone[1].Outcome)
	}
	// message สรุประบุลำที่ fail
	if top.Message == "" || top.Message == "3 drone(s) ACCEPTED" {
		t.Fatalf("summary message ควรระบุ fail รายลำ, got %q", top.Message)
	}
}

func TestAggregateAllOk(t *testing.T) {
	results := []*pb.CommandResult{
		{Ok: true, DroneId: 1}, {Ok: true, DroneId: 2},
	}
	top := aggregate("Takeoff", "r1", results)
	if !top.Ok || len(top.PerDrone) != 2 || top.Message != "2 drone(s) ACCEPTED" {
		t.Fatalf("all-ok aggregate wrong: ok=%v n=%d msg=%q", top.Ok, len(top.PerDrone), top.Message)
	}
}

func TestAggregateEmpty(t *testing.T) {
	top := aggregate("Arm", "r0", nil)
	if top.Ok || top.Message != "no target drone selected" || top.RequestId != "r0" {
		t.Fatalf("empty aggregate wrong: %+v", top)
	}
}
