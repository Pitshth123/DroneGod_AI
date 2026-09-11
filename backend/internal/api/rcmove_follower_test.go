package api

import (
	"os"
	"strings"
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

// Manual MOVE to a formation-owned follower is refused at Core (the cockpit
// routes SWARM MOVE to the leader, but Core owns safety): the check must run
// per drone before the velocity send, and STOP must never be blocked.
func TestRcMoveChecksFormationOwnershipBeforeSending(t *testing.T) {
	b, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Server) RcMove(")
	if start < 0 {
		t.Fatal("RcMove missing")
	}
	body := src[start:]
	if end := strings.Index(body, "\nfunc "); end >= 0 {
		body = body[:end]
	}
	guard := strings.Index(body, "s.formationFollowerMoveReject(id, req.Dir)")
	send := strings.Index(body, "s.cmd.RcMove(")
	if guard < 0 || send < 0 || guard > send {
		t.Fatal("RcMove must reject formation-owned followers before the velocity send")
	}

	hstart := strings.Index(src, "func (s *Server) formationFollowerMoveReject(")
	if hstart < 0 {
		t.Fatal("formationFollowerMoveReject missing")
	}
	helper := src[hstart:]
	if end := strings.Index(helper, "\nfunc "); end >= 0 {
		helper = helper[:end]
	}
	stopPass := strings.Index(helper, "dir == pb.RcDirection_RC_DIR_STOP")
	ownership := strings.Index(helper, "s.swarm.FormationOwnsFollower(id)")
	if stopPass < 0 || ownership < 0 || stopPass > ownership {
		t.Fatal("RC STOP must pass before any formation ownership check")
	}
}

func TestFormationFollowerMoveRejectWithoutSwarmIsNoop(t *testing.T) {
	s := &Server{}
	for _, dir := range []pb.RcDirection{pb.RcDirection_RC_DIR_FWD, pb.RcDirection_RC_DIR_STOP} {
		if r := s.formationFollowerMoveReject(2, dir); r != nil {
			t.Fatalf("no swarm manager → nothing to protect, got %+v", r)
		}
	}
}
