package mission

import (
	"errors"
	"testing"
	"time"
)

func waveGroups() []WaveGroup {
	return []WaveGroup{
		{ID: 1, Members: []uint32{1, 2}},
		{ID: 2, Members: []uint32{3, 4}},
		{ID: 3, Members: []uint32{5}},
	}
}

func startWave(t *testing.T, w *WaveEngine, autoNext bool) {
	t.Helper()
	if err := w.Start(waveGroups(), autoNext); err != nil {
		t.Fatalf("Start wave: %v", err)
	}
}

func finishWaveGroup(t *testing.T, w *WaveEngine) {
	t.Helper()
	if !w.TakeoffComplete(w.Token()) || !w.RouteComplete(w.Token()) ||
		!w.LandedDisarmed(w.Token()) || !w.StartNextGroup(w.Token()) {
		t.Fatal("valid current-run group callbacks must progress")
	}
}

func TestWaveStartsSortedAndProgressesSequentially(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	if s := w.Snapshot(); !s.Active || s.GroupID != 1 || s.Phase != WavePhaseTakeoff {
		t.Fatalf("first sorted group must start in TAKEOFF: %+v", s)
	}
	for range waveGroups() {
		finishWaveGroup(t, w)
	}
	if s := w.Snapshot(); s.State != WaveCompleted || s.Active {
		t.Fatalf("all groups should complete: %+v", s)
	}
}

func TestWaveNeedsLandedProofAndRejectsWrongPhase(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	if w.RouteComplete(w.Token()) || w.StartNextGroup(w.Token()) {
		t.Fatal("wrong-phase callbacks must be rejected")
	}
	if !w.TakeoffComplete(w.Token()) || !w.RouteComplete(w.Token()) {
		t.Fatal("current phase callbacks should work")
	}
	if w.StartNextGroup(w.Token()) {
		t.Fatal("next group requires landed+disarmed proof")
	}
	if !w.LandedDisarmed(w.Token()) || !w.StartNextGroup(w.Token()) {
		t.Fatal("landed proof should permit next group")
	}
}

func TestWaveCompletedRunCallbackCannotAffectNewRun(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	old := w.Token()
	for range waveGroups() {
		finishWaveGroup(t, w)
	}
	if err := w.Start(waveGroups(), false); err != nil {
		t.Fatal(err)
	}
	if w.TakeoffComplete(old) {
		t.Fatal("completed Wave A callback must not affect Wave B")
	}
	if s := w.Snapshot(); s.GroupIndex != 0 || s.Phase != WavePhaseTakeoff {
		t.Fatalf("new run changed by stale callback: %+v", s)
	}
}

func TestWaveCancelledRunCallbackCannotAffectNewRun(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	old := w.Token()
	w.Cancel()
	if err := w.Start(waveGroups(), false); err != nil {
		t.Fatal(err)
	}
	if w.TakeoffComplete(old) {
		t.Fatal("cancelled Wave A callback must not affect Wave B")
	}
}

func TestWaveGroupOneTakeoffCannotAffectGroupTwoTakeoff(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	group1Takeoff := w.Token()
	finishWaveGroup(t, w)
	if s := w.Snapshot(); s.GroupIndex != 1 || s.Phase != WavePhaseTakeoff {
		t.Fatalf("expected group 2 TAKEOFF: %+v", s)
	}
	if w.TakeoffComplete(group1Takeoff) {
		t.Fatal("group 1 TAKEOFF callback must not complete group 2 TAKEOFF")
	}
}

func TestWaveStaleOldGroupLandedAndNextIgnored(t *testing.T) {
	w := NewWaveEngine(newClock().now)
	startWave(t, w, false)
	if !w.TakeoffComplete(w.Token()) || !w.RouteComplete(w.Token()) {
		t.Fatal("setup")
	}
	oldLanded := w.Token()
	if !w.LandedDisarmed(oldLanded) {
		t.Fatal("setup landed")
	}
	oldNext := w.Token()
	if !w.StartNextGroup(oldNext) {
		t.Fatal("setup next")
	}
	if w.LandedDisarmed(oldLanded) || w.StartNextGroup(oldNext) {
		t.Fatal("old group landed/next callbacks must be stale")
	}
	if s := w.Snapshot(); s.GroupIndex != 1 || s.Phase != WavePhaseTakeoff {
		t.Fatalf("old group changed current group: %+v", s)
	}
}

func TestWaveStaleTimeoutPhaseTokenCannotAffectNewerPhase(t *testing.T) {
	clk := newClock()
	w := NewWaveEngine(clk.now)
	w.SetTimeout(5 * time.Minute)
	startWave(t, w, false)
	takeoffToken := w.Token()
	clk.advance(4 * time.Minute)
	if w.Poll() {
		t.Fatal("premature timeout")
	}
	if !w.TakeoffComplete(takeoffToken) {
		t.Fatal("current callback should work")
	}
	clk.advance(4 * time.Minute)
	if w.Poll() {
		t.Fatal("phase transition must reset timeout")
	}
	if w.RouteComplete(takeoffToken) {
		t.Fatal("old phase token must not affect newer phase")
	}
	clk.advance(2 * time.Minute)
	current := w.Token()
	if !w.Poll() {
		t.Fatal("current phase should time out")
	}
	if w.RouteComplete(current) {
		t.Fatal("pre-timeout callback cannot affect terminal run")
	}
}

func TestWaveCancelAndFailsafeAreTerminal(t *testing.T) {
	for _, interrupt := range []bool{false, true} {
		w := NewWaveEngine(newClock().now)
		startWave(t, w, false)
		old := w.Token()
		if interrupt {
			w.Interrupt("battery critical")
			if w.Snapshot().State != WaveInterrupted {
				t.Fatal("failsafe should interrupt")
			}
		} else {
			w.Cancel()
			if w.Snapshot().State != WaveCancelled {
				t.Fatal("cancel should terminate")
			}
		}
		if w.TakeoffComplete(old) {
			t.Fatal("terminal run callback accepted")
		}
	}
}

func TestWaveValidationActiveAndSnapshot(t *testing.T) {
	w := NewWaveEngine(nil)
	if err := w.Start([]WaveGroup{{ID: 1, Members: []uint32{1}}}, false); !errors.Is(err, ErrWaveNeedsTwoGroups) {
		t.Fatalf("one group: %v", err)
	}
	if err := w.Start([]WaveGroup{{ID: 1, Members: []uint32{1}}, {ID: 2}}, false); !errors.Is(err, ErrWaveEmptyGroup) {
		t.Fatalf("empty group: %v", err)
	}
	startWave(t, w, true)
	if err := w.Start(waveGroups(), false); !errors.Is(err, ErrWaveActive) {
		t.Fatalf("duplicate active start: %v", err)
	}
	if !w.Snapshot().AutoNext {
		t.Fatal("auto_next must be captured")
	}
}
