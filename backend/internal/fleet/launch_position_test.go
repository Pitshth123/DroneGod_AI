package fleet

import (
	"testing"

	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/minimal"
)

func feedPosition(d *Drone, lat, lon float64, altM float64) {
	d.HandleFrame(1, &ardupilotmega.MessageGlobalPositionInt{
		Lat:         int32(lat * 1e7),
		Lon:         int32(lon * 1e7),
		RelativeAlt: int32(altM * 1000),
	})
}

func TestLaunchPositionCapturedOnGroundAndFrozenInFlight(t *testing.T) {
	d := newDrone(1, "D1", "host", 5760, nil)
	feedPosition(d, 14.100001, 100.200001, 0.2)
	lat, lon, ok := d.LaunchPosition()
	if !ok || lat != 14.100001 || lon != 100.200001 {
		t.Fatalf("ground launch position = %.7f,%.7f ok=%v", lat, lon, ok)
	}

	d.HandleFrame(1, &ardupilotmega.MessageHeartbeat{
		BaseMode: minimal.MAV_MODE_FLAG_SAFETY_ARMED,
	})
	feedPosition(d, 14.300001, 100.400001, 0.8)
	gotLat, gotLon, _ := d.LaunchPosition()
	if gotLat != lat || gotLon != lon {
		t.Fatalf("armed position overwrote launch point: got %.7f,%.7f want %.7f,%.7f",
			gotLat, gotLon, lat, lon)
	}
}

func TestLaunchPositionRefreshesAfterLandingForNextSortie(t *testing.T) {
	d := newDrone(2, "D2", "host", 5760, nil)
	feedPosition(d, 14.0, 100.0, 0.1)
	d.HandleFrame(1, &ardupilotmega.MessageHeartbeat{
		BaseMode: minimal.MAV_MODE_FLAG_SAFETY_ARMED,
	})
	feedPosition(d, 14.1, 100.1, 20)
	d.HandleFrame(1, &ardupilotmega.MessageHeartbeat{BaseMode: 0})
	feedPosition(d, 14.0002, 100.0002, 0.2)

	lat, lon, ok := d.LaunchPosition()
	if !ok || lat != 14.0002 || lon != 100.0002 {
		t.Fatalf("next-sortie launch position = %.7f,%.7f ok=%v", lat, lon, ok)
	}
}
