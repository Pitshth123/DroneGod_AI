package config

import (
	"os"
	"testing"
)

// SYSID_MYGCS ที่ FC ต้องตรงกับ sysid ที่ core ส่งออก ไม่งั้น ArduPilot ทิ้ง
// RC_CHANNELS_OVERRIDE เงียบ ๆ (handle_rc_channels_override เช็ค msg.sysid ตรง ๆ)
// เคสจริง: รีโมท G12 + แอป MX330 ตั้ง SYSID_MYGCS = 250 แต่ core ส่ง 255 มาตลอด

func TestDefaultSysIDIsStandardGCS(t *testing.T) {
	if got := Default().MAVLinkSysID; got != 255 {
		t.Fatalf("ค่าเริ่มต้นควรเป็น 255 (มาตรฐาน GCS) แต่ได้ %d", got)
	}
}

func TestSysIDOverridableByEnv(t *testing.T) {
	t.Setenv("SWARMGOD_MAVLINK_SYSID", "250")
	if got := Load().MAVLinkSysID; got != 250 {
		t.Fatalf("ตั้ง env 250 แล้วควรได้ 250 แต่ได้ %d", got)
	}
}

func TestSysIDIgnoresOutOfRangeValues(t *testing.T) {
	for _, bad := range []string{"0", "256", "-1", "abc", ""} {
		os.Setenv("SWARMGOD_MAVLINK_SYSID", bad)
		if got := Load().MAVLinkSysID; got != 255 {
			t.Fatalf("ค่าใช้ไม่ได้ %q ต้องคงค่าเริ่มต้น 255 แต่ได้ %d", bad, got)
		}
	}
	os.Unsetenv("SWARMGOD_MAVLINK_SYSID")
}

func TestHomeLocationFromEnv(t *testing.T) {
	t.Setenv("SWARMGOD_HOME_LOC", "13.7563,100.5018,12,90")
	cfg := Load()
	if cfg.GCSLat != 13.7563 || cfg.GCSLon != 100.5018 {
		t.Fatalf("home parse failed: %.6f,%.6f", cfg.GCSLat, cfg.GCSLon)
	}
}

func TestInvalidHomeLocationKeepsSafeDefault(t *testing.T) {
	want := Default()
	for _, bad := range []string{"", "bad", "91,100", "13,181", "0,0", "NaN,100", "13,+Inf"} {
		t.Run(bad, func(t *testing.T) {
			t.Setenv("SWARMGOD_HOME_LOC", bad)
			got := Load()
			if got.GCSLat != want.GCSLat || got.GCSLon != want.GCSLon {
				t.Fatalf("invalid home %q changed default to %.6f,%.6f", bad, got.GCSLat, got.GCSLon)
			}
		})
	}
}

func TestSetupProfileAllowsTelemetryBootstrapWithoutExplicitHome(t *testing.T) {
	cfg := Default()
	cfg.Profile = "setup"
	if cfg.HomeExplicit {
		t.Fatal("precondition: default home must not count as explicit field home")
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("setup telemetry bootstrap must start without explicit home: %v", err)
	}
	if !cfg.TelemetryOnly() {
		t.Fatal("setup profile must suppress automatic/background FC writes")
	}
	cfg.Profile = "hil"
	if cfg.TelemetryOnly() {
		t.Fatal("hil must not be classified as telemetry-only")
	}
}

func TestProductionRequiresExplicitHome(t *testing.T) {
	cfg := Default()
	cfg.Profile = "production"
	if err := cfg.Validate(); err == nil {
		t.Fatal("production without explicit home must fail closed")
	}

	keyPath := t.TempDir() + "/mavlink_key"
	if err := os.WriteFile(keyPath, []byte("0123456789abcdef0123456789abcdef"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg.HomeExplicit = true
	cfg.MAVLinkSignStrict = true
	cfg.MAVLinkSignKey = keyPath
	if err := cfg.Validate(); err != nil {
		t.Fatalf("explicit production home rejected: %v", err)
	}
}

func TestHILRequiresExplicitHome(t *testing.T) {
	cfg := Default()
	cfg.Profile = "hil"
	if err := cfg.Validate(); err == nil {
		t.Fatal("HIL without explicit home must not inherit the SITL location")
	}
	cfg.HomeExplicit = true
	if err := cfg.Validate(); err != nil {
		t.Fatalf("HIL with explicit home rejected: %v", err)
	}
}

func TestProductionRequiresStrictSigning(t *testing.T) {
	keyPath := t.TempDir() + "/mavlink_key"
	if err := os.WriteFile(keyPath, []byte("0123456789abcdef0123456789abcdef"), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := Default()
	cfg.Profile = "production"
	cfg.HomeExplicit = true
	cfg.MAVLinkSignKey = keyPath
	if err := cfg.Validate(); err == nil {
		t.Fatal("production without strict signing must fail closed")
	}
	cfg.MAVLinkSignStrict = true
	cfg.MAVLinkSigningOff = true
	if err := cfg.Validate(); err == nil {
		t.Fatal("production with signing disabled must fail closed")
	}
}

func TestUnknownProfileRejected(t *testing.T) {
	cfg := Default()
	cfg.Profile = "real-ish"
	if err := cfg.Validate(); err == nil {
		t.Fatal("unknown profile must fail closed")
	}
}
