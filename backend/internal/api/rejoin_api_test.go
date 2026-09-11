package api

import (
	"context"
	"os"
	"strings"
	"testing"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
)

func funcBody(t *testing.T, file, signature string) string {
	t.Helper()
	b, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, signature)
	if start < 0 {
		t.Fatalf("%s missing in %s", signature, file)
	}
	body := src[start:]
	if end := strings.Index(body, "\nfunc "); end >= 0 {
		body = body[:end]
	}
	return body
}

func TestSwarmRejoinRequiresExactlyOneDrone(t *testing.T) {
	s := &Server{}
	for _, ids := range [][]uint32{nil, {0}, {2, 3}} {
		r, err := s.SwarmControl(context.Background(), &pb.SwarmControlRequest{
			Action: pb.SwarmControlRequest_REJOIN, DroneIds: ids})
		if err != nil || r.GetOk() {
			t.Fatalf("REJOIN %v must be refused, got %+v err=%v", ids, r, err)
		}
	}
}

func TestSwarmRejoinRespectsReservationAndMissionBeforeFlying(t *testing.T) {
	body := funcBody(t, "server.go", "func (s *Server) SwarmControl(")
	start := strings.Index(body, "case pb.SwarmControlRequest_REJOIN:")
	if start < 0 {
		t.Fatal("REJOIN action missing")
	}
	body = body[start:]
	if end := strings.Index(body, "case pb.SwarmControlRequest_STOP:"); end >= 0 {
		body = body[:end]
	}
	lock := strings.Index(body, "s.missionDispatchMu.Lock()")
	busy := strings.Index(body, "s.swarmNavigationReservationRejectLocked(")
	mission := strings.Index(body, "s.missionAuthorityActiveLocked()")
	fly := strings.Index(body, "s.swarm.Rejoin(id)")
	if lock < 0 || busy < 0 || mission < 0 || fly < 0 || !(lock < busy && busy < fly && mission < fly) {
		t.Fatal("REJOIN must check reservations and Core mission under missionDispatchMu before flying")
	}
}

func TestStopAndTakeoverAbortRejoinFirst(t *testing.T) {
	rc := funcBody(t, "server.go", "func (s *Server) RcMove(")
	abort := strings.Index(rc, "s.swarm.AbortRejoin(id)")
	send := strings.Index(rc, "s.cmd.RcMove(")
	if abort < 0 || send < 0 || abort > send {
		t.Fatal("RC STOP must abort an in-flight REJOIN before the stop is sent")
	}
	takeover := funcBody(t, "server.go", "func (s *Server) stopSwarmForTargetsLocked(")
	abort = strings.Index(takeover, "s.swarm.AbortRejoin(id)")
	mission := strings.Index(takeover, "s.applySwarmMissionTakeoversLocked(ids)")
	if abort < 0 || mission < 0 || abort > mission {
		t.Fatal("every takeover must abort an in-flight REJOIN first")
	}
}
