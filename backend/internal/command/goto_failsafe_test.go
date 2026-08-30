package command

import (
	"os"
	"strings"
	"testing"
)

// Regression guard for F4: every GOTO source shares Service.Goto, so the
// failsafe latch must be checked there before normal navigation safety/send.
// fleet/failsafe_active_test.go separately proves the latch semantics.
func TestGotoChecksFailsafeLatchBeforeNavigationSend(t *testing.T) {
	b, err := os.ReadFile("service.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Service) Goto(")
	end := strings.Index(src[start:], "func (s *Service) ChangeAlt(")
	if start < 0 || end < 0 {
		t.Fatal("could not locate Service.Goto")
	}
	body := src[start : start+end]
	guard := strings.Index(body, "s.mgr.FailsafeActive(id)")
	safety := strings.Index(body, "s.env.CheckGoto(")
	finalGuard := strings.Index(body, "s.mgr.WithFailsafeSendGuard(ctx, id)")
	send := strings.Index(body, "d.GotoContext(")
	if guard < 0 || safety < 0 || finalGuard < 0 || send < 0 {
		t.Fatalf("Goto must contain failsafe precheck, safety, final-write guard, and send stages")
	}
	if !(guard < safety && safety < finalGuard && finalGuard < send) {
		t.Fatalf("Goto order must be failsafe precheck -> safety -> atomic final-write guard -> MAVLink send")
	}
}

func TestHoldChecksFailsafeLatchBeforeAnyModeOrNavigationSend(t *testing.T) {
	b, err := os.ReadFile("service.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Service) Hold(")
	endRel := strings.Index(src[start:], "func (s *Service) Goto(")
	if start < 0 || endRel < 0 {
		t.Fatal("could not locate Service.Hold")
	}
	body := src[start : start+endRel]
	guard := strings.Index(body, "s.mgr.FailsafeActive(id)")
	finalGuard := strings.Index(body, "s.mgr.WithFailsafeSendGuard(ctx, id)")
	state := strings.Index(body, "d.SafetyState()")
	mode := strings.Index(body, "d.SetMode(")
	gotoSend := strings.Index(body, "d.GotoContext(")
	velocitySend := strings.Index(body, "d.MoveVelocityContext(")
	if guard < 0 || finalGuard < 0 || state < 0 || mode < 0 || gotoSend < 0 || velocitySend < 0 {
		t.Fatalf("Hold must contain failsafe precheck plus atomic final-write guard before mode/navigation stages")
	}
	if !(guard < finalGuard && finalGuard < state && finalGuard < mode && finalGuard < gotoSend && finalGuard < velocitySend) {
		t.Fatalf("Hold must attach the atomic failsafe guard before every possible transport write")
	}
}
