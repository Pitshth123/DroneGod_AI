package api

import "testing"

func TestMissionAuthorityModeProfileGate(t *testing.T) {
	tests := []struct {
		name       string
		profile    string
		profileEnv string
		mode       string
		bench      string
		want       string
	}{
		{name: "sitl waypoint", profile: "sitl", profileEnv: "sitl", mode: "core-single", want: "core-single"},
		{name: "sitl wait", profile: "SITL", profileEnv: "sitl", mode: "core-single-wait", want: "core-single-wait"},
		{name: "default derived sitl rejected", profile: "sitl", profileEnv: "", mode: "core-single", want: ""},
		{name: "profile env mismatch rejected", profile: "sitl", profileEnv: "hil", mode: "core-single", want: ""},
		{name: "truthy token rejected", profile: "sitl", profileEnv: "sitl", mode: "true", want: ""},
		{name: "hil missing physical confirmation", profile: "hil", profileEnv: "hil", mode: "core-single-wait", want: ""},
		{name: "hil wrong physical confirmation", profile: "hil", profileEnv: "hil", mode: "core-single", bench: "YES", want: ""},
		{name: "hil waypoint bench", profile: "hil", profileEnv: "hil", mode: "core-single", bench: benchAuthorityConfirmation, want: "core-single"},
		{name: "hil wait bench", profile: "HIL", profileEnv: "hil", mode: "core-single-wait", bench: benchAuthorityConfirmation, want: "core-single-wait"},
		{name: "production remains locked", profile: "production", profileEnv: "production", mode: "core-single-wait", bench: benchAuthorityConfirmation, want: ""},
		{name: "unknown profile remains locked", profile: "dev", profileEnv: "dev", mode: "core-single", bench: benchAuthorityConfirmation, want: ""},
		// V3-S09-A: multi-drone GROUPED authority has NO live token this round.
		// Any multi-ish token must remain unrecognized so it can never be flipped on
		// by configuration before the authority-flip review.
		{name: "grouped-multi token not wired (sitl)", profile: "sitl", profileEnv: "sitl", mode: "core-grouped-multi", want: ""},
		{name: "grouped-multi token not wired (hil bench)", profile: "hil", profileEnv: "hil", mode: "core-grouped-multi", bench: benchAuthorityConfirmation, want: ""},
		{name: "separate token not wired", profile: "sitl", profileEnv: "sitl", mode: "core-separate", want: ""},
		{name: "swarm-leader token not wired", profile: "sitl", profileEnv: "sitl", mode: "core-swarm-leader", want: ""},
		{name: "wave token not wired", profile: "sitl", profileEnv: "sitl", mode: "core-wave", want: ""},
		{name: "payload token not wired", profile: "sitl", profileEnv: "sitl", mode: "core-payload", want: ""},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("SWARMGOD_PROFILE", tc.profileEnv)
			t.Setenv("SWARMGOD_MISSION_AUTHORITY", tc.mode)
			t.Setenv("SWARMGOD_BENCH_CONFIRM", tc.bench)
			if got := missionAuthorityMode(tc.profile); got != tc.want {
				t.Fatalf("missionAuthorityMode(%q)=%q want %q", tc.profile, got, tc.want)
			}
		})
	}
}
