package fleet

import (
	"testing"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
)

// Mission formation geometry must age the navigation samples themselves.  A
// HEARTBEAT or any unrelated MAVLink packet may refresh link telemetry, but must
// never refresh cached GLOBAL_POSITION_INT / GPS_RAW_INT evidence.
func TestNavigationSampleTimestampsIgnoreUnrelatedMAVLink(t *testing.T) {
	d := newDrone(1, "UAV_1", "host", 5760, nil)
	d.HandleFrame(1, &ardupilotmega.MessageGlobalPositionInt{
		Lat: 140000000, Lon: 1000000000, RelativeAlt: 20000,
	})
	d.HandleFrame(1, &ardupilotmega.MessageGpsRawInt{})

	d.mu.RLock()
	positionAt := d.positionAt
	gpsAt := d.gpsAt
	d.mu.RUnlock()
	if positionAt.IsZero() || gpsAt.IsZero() {
		t.Fatalf("navigation messages must establish their own sample timestamps: pos=%v gps=%v",
			positionAt, gpsAt)
	}

	// HEARTBEAT updates lastMsg/hbLast only. It must not make old navigation
	// coordinates or fix evidence appear newly sampled.
	d.HandleFrame(1, &ardupilotmega.MessageHeartbeat{})
	d.mu.RLock()
	gotPositionAt := d.positionAt
	gotGPSAt := d.gpsAt
	d.mu.RUnlock()
	if !gotPositionAt.Equal(positionAt) {
		t.Fatalf("heartbeat refreshed position sample time: before=%v after=%v", positionAt, gotPositionAt)
	}
	if !gotGPSAt.Equal(gpsAt) {
		t.Fatalf("heartbeat refreshed GPS sample time: before=%v after=%v", gpsAt, gotGPSAt)
	}

	state := d.SafetyState()
	if state.PositionAgeSec < 0 || state.GpsAgeSec < 0 || state.TelemetryAgeSec < 0 {
		t.Fatalf("SafetyState must expose separate link/position/GPS ages: %+v", state)
	}
}
