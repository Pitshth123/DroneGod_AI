package bench

import (
	"math"
	"testing"
)

func TestSummarizeTiming(t *testing.T) {
	recv := []int64{1000, 1100, 1200, 1350, 1450}
	src := []int64{980, 1070, 1170, 1300, 1400}
	got := SummarizeTiming(recv, src)
	if got.Samples != 5 {
		t.Fatalf("samples=%d", got.Samples)
	}
	if math.Abs(got.RateHz-8.8888889) > 0.01 {
		t.Fatalf("rate=%v", got.RateHz)
	}
	if got.IntervalP50Ms != 100 || got.IntervalP95Ms < 140 || got.IntervalMaxMs != 150 {
		t.Fatalf("interval summary=%+v", got)
	}
	if got.AgeP50Ms != 30 || got.AgeMaxMs != 50 {
		t.Fatalf("age summary=%+v", got)
	}
}

func TestSummarizeTimingClampsNegativeAge(t *testing.T) {
	got := SummarizeTiming([]int64{1000, 1100}, []int64{1010, 1090})
	if got.AgeP50Ms != 5 || got.AgeMaxMs != 10 {
		t.Fatalf("unexpected age summary: %+v", got)
	}
}

func TestSummarizeTimingEmpty(t *testing.T) {
	if got := SummarizeTiming(nil, nil); got.Samples != 0 || got.RateHz != 0 {
		t.Fatalf("empty=%+v", got)
	}
}
