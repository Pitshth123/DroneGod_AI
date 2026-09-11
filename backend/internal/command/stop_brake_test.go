package command

import (
	"os"
	"strings"
	"testing"
)

// Regression (audit 2026-09-11 14:50:46): releasing MOVE sent STOP to the
// leader; Stop = zero velocity + Hold, and Hold pinned SafetyState's lat/lon —
// where the aircraft was one telemetry sample ago at full speed. The position
// controller overshot that point while braking, then flew BACK to it.
//
// In GUIDED the zero-velocity target is already the stop (ArduCopter brakes and
// holds the point where it actually stopped), so Stop must return before Hold
// there and must never pin a position itself.
func TestStopInGuidedBrakesWithoutPinningStalePosition(t *testing.T) {
	b, err := os.ReadFile("service.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Service) Stop(")
	if start < 0 {
		t.Fatal("Service.Stop missing")
	}
	body := src[start:]
	if end := strings.Index(body, "\nfunc "); end >= 0 {
		body = body[:end]
	}
	zero := strings.Index(body, "d.MoveVelocityContext(ctx, 0, 0, 0, 0)")
	guided := strings.Index(body, "st.Mode == holdModeName")
	failsafe := strings.Index(body, "!s.mgr.FailsafeActive(id)")
	hold := strings.Index(body, "s.Hold(ctx, id)")
	if zero < 0 || guided < 0 || failsafe < 0 || hold < 0 {
		t.Fatal("Stop must keep zero velocity, a failsafe-aware GUIDED brake path, and the Hold fallback")
	}
	if !(zero < guided && guided < hold) {
		t.Fatal("GUIDED brake must follow the zero-velocity send and precede the Hold fallback")
	}
	if strings.Contains(body, "GotoContext(") {
		t.Fatal("Stop must never pin a (possibly stale) position itself")
	}
}
