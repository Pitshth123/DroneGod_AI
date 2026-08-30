package command

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

// V3-S07 Part B — Takeoff must abort promptly when an operator emergency/takeover
// cancels its exec context, instead of sleeping out the GUIDED→arm→takeoff
// delays. The API layer cancels execCtx via preemptReserveLocked; here we verify
// the command layer's cancellation primitive and that Takeoff uses it so no
// later Arm/Takeoff step runs after cancellation.

func TestSleepCtxReturnsFalseImmediatelyWhenCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	start := time.Now()
	if sleepCtx(ctx, 5*time.Second) {
		t.Fatal("sleepCtx must return false when ctx is already cancelled")
	}
	if elapsed := time.Since(start); elapsed > 200*time.Millisecond {
		t.Fatalf("cancelled sleepCtx must return promptly, took %v", elapsed)
	}
}

func TestSleepCtxCompletesWhenNotCancelled(t *testing.T) {
	if !sleepCtx(context.Background(), 10*time.Millisecond) {
		t.Fatal("sleepCtx must return true when it sleeps the full duration")
	}
}

func TestSleepCtxWakesOnMidSleepCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()
	start := time.Now()
	if sleepCtx(ctx, 10*time.Second) {
		t.Fatal("sleepCtx must return false when cancelled mid-sleep")
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("sleepCtx did not wake promptly on cancellation, took %v", elapsed)
	}
}

// Structural guard: every inter-step delay in Takeoff must be context-aware
// (sleepCtx), and the arm-retry loop must break on cancellation, so a preempted
// Takeoff cannot issue a later arm/takeoff step. Mirrors the goto_failsafe order
// guard style.
func TestTakeoffSequenceIsContextPreemptible(t *testing.T) {
	b, err := os.ReadFile("service.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(b)
	start := strings.Index(src, "func (s *Service) Takeoff(")
	end := strings.Index(src[start:], "func (s *Service) Land(")
	if start < 0 || end < 0 {
		t.Fatal("could not locate Service.Takeoff body")
	}
	body := src[start : start+end]

	if strings.Contains(body, "time.Sleep(") {
		t.Fatal("Takeoff must not use non-cancellable time.Sleep; use sleepCtx so a takeover preempts it")
	}
	setGuided := strings.Index(body, "SetMode(c, guided)")
	armLoop := strings.Index(body, "d.Arm(c, false)")
	takeoff := strings.Index(body, "d.Takeoff(c, alt)")
	if setGuided < 0 || armLoop < 0 || takeoff < 0 {
		t.Fatal("Takeoff must retain GUIDED -> arm -> takeoff stages")
	}
	// A sleepCtx guard must sit before the arm loop and before the final takeoff,
	// each returning on cancellation so no later step runs after preemption.
	firstSleep := strings.Index(body, "sleepCtx(cctx, 400")
	armSleep := strings.Index(body, "sleepCtx(cctx, 2*time.Second)")
	preTakeoffSleep := strings.Index(body, "sleepCtx(cctx, 500")
	if firstSleep < 0 || armSleep < 0 || preTakeoffSleep < 0 {
		t.Fatal("Takeoff must guard each delay with sleepCtx for preemption")
	}
	if !(setGuided < firstSleep && firstSleep < armLoop &&
		armLoop < preTakeoffSleep && preTakeoffSleep < takeoff) {
		t.Fatal("sleepCtx preemption guards must sit between the takeoff stages in order")
	}
	// The final takeoff step must be gated behind a cancellation check.
	if !strings.Contains(body, "preempted by operator/takeover after arm") {
		t.Fatal("Takeoff must not issue the final takeoff step after a preemption")
	}
}
