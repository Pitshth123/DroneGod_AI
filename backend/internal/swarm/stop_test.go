package swarm

import (
	"context"
	"math"
	"testing"
	"time"

	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/pkg/geo"
)

func TestStopWaitsForFormationLoopToExit(t *testing.T) {
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer aud.Close()

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	m := &Manager{audit: aud, active: true, cancel: cancel, done: done}
	go func() {
		<-ctx.Done()
		time.Sleep(40 * time.Millisecond) // จำลอง tick ที่กำลังส่งคำสั่งอยู่
		close(done)
	}()

	start := time.Now()
	m.Stop()
	if elapsed := time.Since(start); elapsed < 35*time.Millisecond {
		t.Fatalf("Stop returned before the active tick exited: %v", elapsed)
	}
	if m.active || m.stopping || m.cancel != nil || m.done != nil || m.stopDone != nil {
		t.Fatalf("Stop left loop state behind: active=%v stopping=%v cancel=%v done=%v stopDone=%v",
			m.active, m.stopping, m.cancel != nil, m.done != nil, m.stopDone != nil)
	}
}

func TestStartRejectedWhileStopIsInProgress(t *testing.T) {
	m := &Manager{stopping: true}
	if err := m.Start(context.Background()); err == nil {
		t.Fatal("Start must not create a new formation loop while Stop is waiting")
	}
}

func TestNavigationBusyCoversFormationStoppingAndReturn(t *testing.T) {
	m := &Manager{}
	if m.NavigationBusy() {
		t.Fatal("idle manager must not report navigation busy")
	}
	m.active = true
	if !m.NavigationBusy() {
		t.Fatal("active formation must report navigation busy")
	}
	m.active = false
	m.stopping = true
	if !m.NavigationBusy() {
		t.Fatal("stopping formation must remain navigation busy until loop exit")
	}
	m.stopping = false
	m.returnCancel = func() {}
	if !m.NavigationBusy() {
		t.Fatal("registered RETURN/LAND sequence must report navigation busy")
	}
	m.returnCancel = nil
	if m.NavigationBusy() {
		t.Fatal("cleared formation/return state must become idle")
	}
}

func TestCancelReturnWaitsForSequenceToExit(t *testing.T) {
	aud, err := audit.New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer aud.Close()

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	m := &Manager{audit: aud, returnCancel: cancel, returnDone: done}
	go func() {
		<-ctx.Done()
		time.Sleep(40 * time.Millisecond)
		close(done)
	}()
	start := time.Now()
	m.CancelReturn()
	if elapsed := time.Since(start); elapsed < 35*time.Millisecond {
		t.Fatalf("CancelReturn returned before sequence stopped: %v", elapsed)
	}
}

func TestReturnRequiresHorizontalAndAltitudeArrival(t *testing.T) {
	if !returnTargetReached(14.9581695, 102.0986187, 15.5, 14.9581695, 102.0986187, 15) {
		t.Fatal("near home at target altitude should be accepted")
	}
	if returnTargetReached(14.9581695, 102.0986187, 25, 14.9581695, 102.0986187, 15) {
		t.Fatal("horizontal arrival alone must not start landing")
	}
}

func TestReturnAltitudeMustBeReachedBeforeHorizontalReturn(t *testing.T) {
	if !returnAltitudeReached(20.8, 20) {
		t.Fatal("altitude inside tolerance should allow horizontal return")
	}
	if returnAltitudeReached(15, 20) {
		t.Fatal("horizontal return must stay blocked before the assigned layer")
	}
}

func TestLandingRequiresGroundAndDisarmed(t *testing.T) {
	if landedConfirmed(true, 0.2) {
		t.Fatal("low altitude while armed is not a confirmed landing")
	}
	if landedConfirmed(false, 5) {
		t.Fatal("disarmed while reporting high altitude is not a confirmed landing")
	}
	if !landedConfirmed(false, 0.2) {
		t.Fatal("disarmed on ground should confirm landing")
	}
}

func TestReturnLayerGapRespectsSafetySeparation(t *testing.T) {
	if got := returnLayerGap(5); got != 5 {
		t.Fatalf("return layer gap %.1fm is below configured separation", got)
	}
	if got := returnLayerGap(1); got != 2 {
		t.Fatalf("return layer gap %.1fm is below hard minimum", got)
	}
}

func TestReturnLandingOffsetsAreDistinctAndSeparated(t *testing.T) {
	want := []float64{0, 5, -5, 10, -10, 15}
	for i, expected := range want {
		if got := returnLandingEastOffset(i, 5); got != expected {
			t.Fatalf("offset[%d]=%.1f want %.1f", i, got, expected)
		}
	}
	for i := range want {
		for j := i + 1; j < len(want); j++ {
			if math.Abs(want[i]-want[j]) < 5 {
				t.Fatalf("landing offsets %.1f and %.1f violate separation", want[i], want[j])
			}
		}
	}
}

func TestReturnPositionsPreserveDistinctLaunchPoints(t *testing.T) {
	order := []uint32{1, 2, 3}
	preferred := map[uint32][2]float64{
		1: {14.0000, 100.0000},
		2: {14.0000, 100.0001},
		3: {14.0000, 100.0002},
	}
	got := planReturnPositions(order, preferred, 14.5, 100.5, 5)
	for _, id := range order {
		if got[id] != preferred[id] {
			t.Fatalf("Drone %d launch point changed: got=%v want=%v", id, got[id], preferred[id])
		}
	}
}

func TestReturnPositionsSeparateMissingOrOverlappingLaunchPoints(t *testing.T) {
	order := []uint32{1, 2, 3, 4}
	preferred := map[uint32][2]float64{
		1: {14.0, 100.0},
		2: {14.0, 100.0}, // จุดปล่อยซ้อน ต้องขยับเพื่อความปลอดภัย
	}
	got := planReturnPositions(order, preferred, 14.0, 100.0, 5)
	for i, a := range order {
		for _, b := range order[i+1:] {
			if dist := geo.HaversineM(got[a][0], got[a][1], got[b][0], got[b][1]); dist < 4.95 {
				t.Fatalf("targets D%d/D%d only %.2fm apart: %v %v", a, b, dist, got[a], got[b])
			}
		}
	}
}

func TestReturnPositionsScaleOneToTwentyWithOverlappingLaunchPoints(t *testing.T) {
	const gap = 5.0
	for count := 1; count <= 20; count++ {
		order := make([]uint32, count)
		preferred := make(map[uint32][2]float64, count)
		for i := range order {
			id := uint32(i + 1)
			order[i] = id
			// สลับระหว่างพิกัดหาย, พิกัดซ้อนฐาน และพิกัดซ้อนอีกกลุ่ม
			// เพื่อจำลองการตั้งใจให้หลายลำ "จอดทับกัน".
			switch i % 3 {
			case 1:
				preferred[id] = [2]float64{14.0, 100.0}
			case 2:
				preferred[id] = [2]float64{14.0, 100.00001}
			}
		}
		got := planReturnPositions(order, preferred, 14.0, 100.0, gap)
		if len(got) != count {
			t.Fatalf("count=%d planned %d targets", count, len(got))
		}
		for i, a := range order {
			for _, b := range order[i+1:] {
				dist := geo.HaversineM(got[a][0], got[a][1], got[b][0], got[b][1])
				if dist < gap-0.05 {
					t.Fatalf("count=%d D%d/D%d targets only %.2fm apart: %v %v",
						count, a, b, dist, got[a], got[b])
				}
			}
		}
	}
}

func TestSelectReturnDronesUsesRequestedOnlineSubset(t *testing.T) {
	got, err := selectReturnDrones([]uint32{1, 2, 3}, []uint32{3, 1, 3})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != 3 || got[1] != 1 {
		t.Fatalf("selected subset = %v", got)
	}
	if _, err := selectReturnDrones([]uint32{1, 2}, []uint32{3}); err == nil {
		t.Fatal("offline requested drone must fail the whole return sequence")
	}
}
