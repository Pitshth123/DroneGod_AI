package api

import (
	"context"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/swarmgod/backend/internal/config"
)

func TestSetupProfileAllowsOnlyBootstrapReadPaths(t *testing.T) {
	s := &Server{cfg: config.Config{Profile: "setup"}}
	interceptor := s.setupSafetyUnary()

	allowed := []string{
		"/swarmgod.v1.SwarmGodService/Connect",
		"/swarmgod.v1.SwarmGodService/Disconnect",
		"/swarmgod.v1.SwarmGodService/GetFleetSnapshot",
		"/swarmgod.v1.SwarmGodService/GetMissionState",
		"/swarmgod.v1.SwarmGodService/ParamGet",
		"/swarmgod.v1.SwarmGodService/ParamList",
		"/swarmgod.v1.SwarmGodService/GetSwarmState",
	}
	for _, method := range allowed {
		t.Run("allow "+method, func(t *testing.T) {
			called := false
			_, err := interceptor(context.Background(), nil, &grpc.UnaryServerInfo{FullMethod: method},
				func(context.Context, any) (any, error) {
					called = true
					return nil, nil
				})
			if err != nil || !called {
				t.Fatalf("setup bootstrap method rejected: method=%s called=%v err=%v", method, called, err)
			}
		})
	}
}

func TestSetupProfileBlocksEveryFlightMutationBeforeHandler(t *testing.T) {
	s := &Server{cfg: config.Config{Profile: "setup"}}
	interceptor := s.setupSafetyUnary()

	blocked := []string{
		"Arm", "Disarm", "Kill", "Takeoff", "Land", "ReturnToLaunch", "Hold", "Goto",
		"ChangeAlt", "EqualizeAlt", "ChangeSpeed", "RcMove", "SetYaw", "StopAll", "SetMode",
		"UploadMission", "MissionControl", "Orbit", "StartMission", "CancelMission",
		"Gimbal", "Servo", "ParamSet", "SetGeofence", "SetLeader", "SetSwarmConfig", "SwarmControl",
	}
	for _, name := range blocked {
		method := "/swarmgod.v1.SwarmGodService/" + name
		t.Run(name, func(t *testing.T) {
			called := false
			_, err := interceptor(context.Background(), nil, &grpc.UnaryServerInfo{FullMethod: method},
				func(context.Context, any) (any, error) {
					called = true
					return nil, nil
				})
			if called {
				t.Fatalf("setup flight mutation reached handler: %s", name)
			}
			if status.Code(err) != codes.FailedPrecondition {
				t.Fatalf("setup mutation code=%v want FailedPrecondition, err=%v", status.Code(err), err)
			}
		})
	}
}

func TestSetupGateDoesNotChangeNormalProfiles(t *testing.T) {
	for _, profile := range []string{"sitl", "hil", "production"} {
		s := &Server{cfg: config.Config{Profile: profile}}
		called := false
		_, err := s.setupSafetyUnary()(context.Background(), nil,
			&grpc.UnaryServerInfo{FullMethod: "/swarmgod.v1.SwarmGodService/Arm"},
			func(context.Context, any) (any, error) {
				called = true
				return nil, nil
			})
		if err != nil || !called {
			t.Fatalf("profile %s unexpectedly changed by setup gate: called=%v err=%v", profile, called, err)
		}
	}
}
