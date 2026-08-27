package safety

import (
	"math"
	"testing"

	"github.com/swarmgod/backend/internal/config"
)

func newEnv() *Envelope {
	e := New(config.Default())
	e.SetGCS(14.9581695, 102.0986187)
	return e
}

func TestCheckAltitude(t *testing.T) {
	e := newEnv()
	cases := []struct {
		alt   float64
		allow bool
	}{
		{-1, false}, {0, false}, {1, true}, {120, true}, {121, false}, {500, false},
	}
	for _, c := range cases {
		if got := e.CheckAltitude(c.alt).Allow; got != c.allow {
			t.Errorf("CheckAltitude(%.0f)=%v want %v", c.alt, got, c.allow)
		}
	}
}

func TestNonFiniteLimitsAreRejected(t *testing.T) {
	e := newEnv()
	for _, value := range []float64{math.NaN(), math.Inf(1), math.Inf(-1)} {
		if e.CheckAltitude(value).Allow {
			t.Errorf("non-finite altitude %v must be denied", value)
		}
		if e.CheckSpeed(value).Allow {
			t.Errorf("non-finite speed %v must be denied", value)
		}
		if e.CheckGoto(DroneState{ID: 1, Armed: true}, value, 102, 20, nil).Allow {
			t.Errorf("non-finite target latitude %v must be denied", value)
		}
	}
}

func TestNewEnvelopeUsesConfiguredHome(t *testing.T) {
	cfg := config.Default()
	cfg.GCSLat, cfg.GCSLon = 13.7563, 100.5018
	e := New(cfg)
	self := DroneState{ID: 1, Armed: true}
	if e.CheckGoto(self, 14.9581695, 102.0986187, 30, nil).Allow {
		t.Fatal("configured home must enforce radius without a separate SetGCS call")
	}
}

func TestArmPrecondition(t *testing.T) {
	e := newEnv()
	ok := DroneState{ID: 1, BatteryPct: 80, GpsFix: 3, SatCount: 12}
	if !e.CheckArmPrecondition(ok).Allow {
		t.Error("healthy drone should be allowed to arm")
	}
	lowBatt := DroneState{ID: 1, BatteryPct: 10, GpsFix: 3, SatCount: 12}
	if e.CheckArmPrecondition(lowBatt).Allow {
		t.Error("low battery must block arm")
	}
	noFix := DroneState{ID: 1, BatteryPct: 80, GpsFix: 1, SatCount: 12}
	if e.CheckArmPrecondition(noFix).Allow {
		t.Error("no 3D fix must block arm")
	}
	fewSat := DroneState{ID: 1, BatteryPct: 80, GpsFix: 3, SatCount: 4}
	if e.CheckArmPrecondition(fewSat).Allow {
		t.Error("too few sats must block arm")
	}
}

func TestGotoSeparation(t *testing.T) {
	e := newEnv()
	self := DroneState{ID: 1, Armed: true, Lat: 14.9581, Lon: 102.0986}
	others := []DroneState{{ID: 2, Lat: 14.95811, Lon: 102.09861}} // ~1-2m away
	// เป้าใกล้ลำอื่นเกินไป → ต้อง deny
	if e.CheckGoto(self, 14.95811, 102.09861, 30, others).Allow {
		t.Error("goto within min separation must be denied")
	}
	// เป้าไกลพอ + ในรัศมี → allow
	if !e.CheckGoto(self, 14.95850, 102.09900, 30, others).Allow {
		t.Error("valid goto should be allowed")
	}
}

func TestGotoFailsClosedWhenOtherPositionIsStale(t *testing.T) {
	e := newEnv()
	self := DroneState{ID: 1, Armed: true}
	others := []DroneState{{ID: 2, Lat: 14.9588, Lon: 102.0990, TelemetryAgeSec: 10}}
	if e.CheckGoto(self, 14.9585, 102.0990, 30, others).Allow {
		t.Fatal("goto must not proceed when separation from another connected drone cannot be verified")
	}
}

func TestGotoRadius(t *testing.T) {
	e := newEnv()
	self := DroneState{ID: 1, Armed: true}
	// จุดไกลจาก GCS มาก (คนละจังหวัด) → เกินรัศมี
	if e.CheckGoto(self, 13.7563, 100.5018, 30, nil).Allow {
		t.Error("goto far outside radius must be denied")
	}
}

func TestGotoRequiresArmed(t *testing.T) {
	e := newEnv()
	self := DroneState{ID: 1, Armed: false}
	if e.CheckGoto(self, 14.9582, 102.0988, 30, nil).Allow {
		t.Error("goto on disarmed drone must be denied")
	}
}

func TestGeofence(t *testing.T) {
	e := newEnv()
	// สี่เหลี่ยมเล็กรอบ home (~ครอบ 14.9580–14.9583, 102.0985–102.0988)
	if err := e.SetGeofence([][2]float64{
		{14.9580, 102.0985}, {14.9583, 102.0985},
		{14.9583, 102.0988}, {14.9580, 102.0988},
	}); err != nil {
		t.Fatal(err)
	}
	self := DroneState{ID: 1, Armed: true, Lat: 14.9581, Lon: 102.0986}
	// จุดในรั้ว → allow
	if !e.CheckGoto(self, 14.9582, 102.0987, 20, nil).Allow {
		t.Error("goto inside geofence should be allowed")
	}
	// จุดนอกรั้ว (แต่ยังในรัศมี) → deny
	if e.CheckGoto(self, 14.9590, 102.0986, 20, nil).Allow {
		t.Error("goto outside geofence must be denied")
	}
	// CheckPoint (swarm) นอกรั้ว → deny
	if e.CheckPoint(14.9590, 102.0986, 20).Allow {
		t.Error("swarm point outside geofence must be denied")
	}
}

func TestGeofenceValidationFailsClosed(t *testing.T) {
	e := newEnv()
	bad := [][][2]float64{
		{{14, 102}, {14.1, 102.1}},
		{{14, 102}, {14.1, 102.1}, {14.2, 102.2}},
		{{14, 102}, {14.1, 102.1}, {14, 102}},
		{{14, 102}, {14.1, 102.1}, {14, 102.1}, {14.1, 102}},
		{{math.NaN(), 102}, {14.1, 102.1}, {14, 102.1}},
	}
	for i, poly := range bad {
		if err := e.SetGeofence(poly); err == nil {
			t.Errorf("invalid polygon %d was accepted", i)
		}
	}
	if err := e.SetGeofence(nil); err != nil {
		t.Fatalf("clearing geofence should be allowed: %v", err)
	}
}

func TestManualStateRequiresFreshTelemetryAndPosition(t *testing.T) {
	e := newEnv()
	valid := DroneState{ID: 1, Lat: 14.9581, Lon: 102.0986, Heading: 90, TelemetryAgeSec: 0.2}
	if !e.CheckManualState(valid).Allow {
		t.Fatal("fresh valid telemetry should allow manual safety projection")
	}
	stale := valid
	stale.TelemetryAgeSec = 10
	if e.CheckManualState(stale).Allow {
		t.Fatal("stale telemetry must deny manual movement")
	}
	missing := valid
	missing.Lat, missing.Lon = 0, 0
	if e.CheckManualState(missing).Allow {
		t.Fatal("missing position must deny manual movement")
	}
}

func TestDangerousRequiresConfirm(t *testing.T) {
	e := newEnv()
	if e.CheckDangerous("KILL", false).Allow {
		t.Error("KILL without confirm must be denied")
	}
	if !e.CheckDangerous("KILL", true).Allow {
		t.Error("KILL with confirm should be allowed")
	}
}
