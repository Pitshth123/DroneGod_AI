package bench

import (
	"math"
	"sort"
)

// TimingSummary is a transport-agnostic summary used by the F9A read-only
// bench probe. Values are milliseconds except RateHz.
type TimingSummary struct {
	Samples       int     `json:"samples"`
	RateHz        float64 `json:"rate_hz"`
	IntervalP50Ms float64 `json:"interval_p50_ms"`
	IntervalP95Ms float64 `json:"interval_p95_ms"`
	IntervalMaxMs float64 `json:"interval_max_ms"`
	AgeP50Ms      float64 `json:"age_p50_ms"`
	AgeP95Ms      float64 `json:"age_p95_ms"`
	AgeMaxMs      float64 `json:"age_max_ms"`
}

// SummarizeTiming consumes receive timestamps and source telemetry timestamps.
// Input slices must describe the same samples in milliseconds since epoch.
func SummarizeTiming(receivedMs, sourceMs []int64) TimingSummary {
	n := len(receivedMs)
	if len(sourceMs) < n {
		n = len(sourceMs)
	}
	if n <= 0 {
		return TimingSummary{}
	}

	ages := make([]float64, 0, n)
	for i := 0; i < n; i++ {
		age := float64(receivedMs[i] - sourceMs[i])
		if age < 0 {
			age = 0 // tolerate small clock skew in a report rather than inventing negative age
		}
		ages = append(ages, age)
	}
	intervals := make([]float64, 0, n-1)
	for i := 1; i < n; i++ {
		d := float64(receivedMs[i] - receivedMs[i-1])
		if d >= 0 {
			intervals = append(intervals, d)
		}
	}

	out := TimingSummary{Samples: n}
	if len(intervals) > 0 {
		out.IntervalP50Ms = percentile(intervals, 0.50)
		out.IntervalP95Ms = percentile(intervals, 0.95)
		out.IntervalMaxMs = max(intervals)
		elapsed := float64(receivedMs[n-1]-receivedMs[0]) / 1000.0
		if elapsed > 0 {
			out.RateHz = float64(n-1) / elapsed
		}
	}
	out.AgeP50Ms = percentile(ages, 0.50)
	out.AgeP95Ms = percentile(ages, 0.95)
	out.AgeMaxMs = max(ages)
	return out
}

func percentile(values []float64, q float64) float64 {
	if len(values) == 0 {
		return 0
	}
	v := append([]float64(nil), values...)
	sort.Float64s(v)
	if q <= 0 {
		return v[0]
	}
	if q >= 1 {
		return v[len(v)-1]
	}
	pos := q * float64(len(v)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return v[lo]
	}
	f := pos - float64(lo)
	return v[lo]*(1-f) + v[hi]*f
}

func max(values []float64) float64 {
	var out float64
	for i, v := range values {
		if i == 0 || v > out {
			out = v
		}
	}
	return out
}
