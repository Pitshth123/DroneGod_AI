// Package api — gRPC server ที่ Python cockpit ต่อเข้ามา
// Phase 1: Connect, GetFleetSnapshot, SubscribeTelemetry
// (RPC อื่น embed Unimplemented ไว้ — เติมใน Phase 3)
package api

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"log"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/status"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/command"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/swarm"
	"github.com/swarmgod/backend/internal/telemetry"
)

type missionCommandExecutor interface {
	Goto(ctx context.Context, droneID uint32, lat, lon, alt float64) error
	Hold(ctx context.Context, droneID uint32) error
}

type missionReturnCommandExecutor interface {
	RTL(ctx context.Context, droneID uint32) error
}

type missionSwarmCoordinator interface {
	FollowerAuthorityReadyFor(leaderID uint32, participants []uint32) bool
	ClaimMissionMembership(leaderID uint32, participants []uint32) bool
	RebindMissionMembership(oldLeaderID, newLeaderID uint32,
		activeParticipants, excludedParticipants []uint32) bool
	ReleaseMissionLeader(leaderID uint32)
	RevokeFormationNavigation() <-chan struct{}
}

type missionSwarmReturnCoordinator interface {
	ReturnAndLand(context.Context, []uint32, float64, float64) error
	RevokeReturnNavigation() <-chan struct{}
	ReturnResults() <-chan swarm.ReturnResult
}

// missionSendGuard closes the final cancellation -> FC-write race for one Core
// mission send.  Operator takeover calls Cancel() while holding the API ownership
// lock; fleet.Drone calls DoSend() only around the actual conn.Send().  Therefore
// either the write commits first, or cancellation wins first and the stale write
// is refused.  No ACK wait is ever held under this guard.
type missionSendGuard struct {
	mu        sync.Mutex
	cancelled bool
}

func (g *missionSendGuard) DoSend(send func() error) error {
	if g == nil {
		return send()
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.cancelled {
		return context.Canceled
	}
	return send()
}

func (g *missionSendGuard) Cancel() {
	if g == nil {
		return
	}
	g.mu.Lock()
	g.cancelled = true
	g.mu.Unlock()
}

type Server struct {
	pb.UnimplementedSwarmGodServiceServer
	cfg     config.Config
	mgr     *fleet.Manager
	agg     *telemetry.Aggregator
	cmd     *command.Service
	swarm   *swarm.Manager
	events  *events.Bus
	mission *mission.Engine // command-free state machine; API adapter owns any F4 command execution

	// S10 durable last-run evidence reuses the canonical SQLite Store. Persistence
	// is revision-gated and never restores command/timer/generation state.
	missionStore             mission.DurableStore
	missionSessionID         string
	missionPersistMu         sync.Mutex
	missionPersistedRunID    uint64
	missionPersistedRevision uint64
	missionPersistenceFault  string

	// F4 Stage B is opt-in and SITL-only.  The executor is separate from cmd so
	// tests can prove exactly-once mission dispatch without a real MAVLink fleet.
	missionAuthority  bool
	missionExec       missionCommandExecutor
	missionDispatchMu sync.Mutex

	// V3-S07: per-drone command reservations, guarded by missionDispatchMu.
	// A normal (non-takeover) command reserves its targets only for the short
	// ownership critical section, then RELEASES the lock before the long FC
	// ACK wait, so an emergency/takeover command is never serialized behind it.
	// The reservation still blocks StartMission (no dual authority), and a
	// takeover preempts an in-flight normal op by cancelling its exec context.
	reservations map[uint32]*cmdLease

	// V3-S07/S08 review fix: exactly one Core mission GOTO/HOLD send may be
	// in-flight. The send runs with missionDispatchMu RELEASED so takeover is not
	// serialized behind an FC ACK. missionSendGuard synchronizes cancellation with
	// the final FC write itself, closing the ctx.Err() -> conn.Send() TOCTOU window.
	// All fields below are guarded by missionDispatchMu.
	missionSendCancel context.CancelFunc
	missionSendGuard  *missionSendGuard
	missionSendGen    uint64
	missionSendActive bool

	// V3-S09-A multi-drone GROUPED dispatch (PRE-FLIP, OFF by default).  This path
	// only runs when the engine's grouped-multi authority opt-in is enabled, which
	// NO live profile token sets — so production never touches it.  Each participant
	// send has its OWN cancel + final-write guard so an operator takeover on one
	// drone stops only its pending/in-flight send while the run goes terminal.
	// All fields guarded by missionDispatchMu.
	missionGroupSends map[uint32]*missionGroupSend
	missionGroupGen   uint64
	missionStagger    time.Duration               // per-drone launch spacing (default missionGroupStagger)
	missionSchedule   func(time.Duration, func()) // nil -> time.AfterFunc; overridable for deterministic tests
	// Position providers are deterministic test seams.  Production obtains age and
	// GPS validity from fleet.Drone's existing safety state.
	missionPosProvider       func() map[uint32][2]float64 // legacy fresh-sample seam
	missionPosSampleProvider func() map[uint32]mission.PositionSample

	// S09-F Return ownership is separate from mission GOTO/HOLD ownership. The
	// batch claims all intended RTL targets before the first ACK wait. It is only
	// reachable through Engine.EnableReturnPolicyPreFlip (never from a live token).
	missionReturnBatch *cmdBatch
	missionReturnRunID uint64

	// S09-C test seam; production falls back to s.swarm. Eligibility is derived
	// from fleet online/failsafe state unless explicitly supplied by a test.
	missionSwarm           missionSwarmCoordinator
	missionSwarmReturn     missionSwarmReturnCoordinator
	swarmSuccessorEligible func(uint32) bool
	missionFailsafeActive  func(uint32) bool

	auth *authInterceptor
	ctx  context.Context
}

func (s *Server) missionSwarmCoordinator() missionSwarmCoordinator {
	if s == nil {
		return nil
	}
	if s.missionSwarm != nil {
		return s.missionSwarm
	}
	if s.swarm == nil {
		return nil
	}
	return s.swarm
}

func (s *Server) missionSwarmReturnCoordinator() missionSwarmReturnCoordinator {
	if s == nil {
		return nil
	}
	if s.missionSwarmReturn != nil {
		return s.missionSwarmReturn
	}
	if s.swarm == nil {
		return nil
	}
	return s.swarm
}

func (s *Server) missionParticipantFailsafeActive(id uint32) bool {
	if s == nil || id == 0 {
		return false
	}
	if s.missionFailsafeActive != nil {
		return s.missionFailsafeActive(id)
	}
	return s.mgr != nil && s.mgr.FailsafeActive(id)
}

func New(ctx context.Context, cfg config.Config, mgr *fleet.Manager,
	agg *telemetry.Aggregator, cmd *command.Service, sw *swarm.Manager, bus *events.Bus,
	val SessionValidator) *Server {
	s := &Server{cfg: cfg, mgr: mgr, agg: agg, cmd: cmd, swarm: sw, events: bus,
		mission:          mission.NewEngine(nil),
		missionSessionID: newCoreMissionSessionID(),
		reservations:     make(map[uint32]*cmdLease),
		auth:             newAuthInterceptor(val, cfg.Profile), ctx: ctx}
	if cmd != nil {
		s.missionExec = &missionCommandSink{ctx: ctx, cmd: cmd}
	}
	if s.missionExec != nil {
		switch missionAuthorityMode(cfg.Profile) {
		case "core-single":
			s.missionAuthority = true
			s.mission.EnableAuthority()
		case "core-single-wait":
			s.missionAuthority = true
			s.mission.EnableWaitAuthority()
		}
	}
	if durable, ok := val.(mission.DurableStore); ok {
		s.missionStore = durable
		s.restoreDurableMission()
	}
	return s
}

const benchAuthorityConfirmation = "PROPS-REMOVED-BENCH"

// missionAuthorityMode is deliberately narrow. F4/F5 authority is available in
// SITL directly, and on the actual-FC HIL bench only behind a second exact
// physical-bench confirmation token. Production can never enable this rollout
// gate. Separate exact mission tokens ensure deploying WAIT-capable code cannot
// silently widen a previously approved waypoint-only scope.
func missionAuthorityMode(profile string) string {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("SWARMGOD_MISSION_AUTHORITY")))
	if v != "core-single" && v != "core-single-wait" {
		return ""
	}

	// Authority rollout must never rely on Config.Default() silently choosing
	// SITL. Require the deployment profile to be explicitly supplied and to
	// match the validated Config value before an authority token can take effect.
	configuredProfile := strings.ToLower(strings.TrimSpace(os.Getenv("SWARMGOD_PROFILE")))
	profile = strings.ToLower(strings.TrimSpace(profile))
	if configuredProfile == "" || configuredProfile != profile {
		return ""
	}

	switch profile {
	case "sitl":
		return v
	case "hil":
		if strings.TrimSpace(os.Getenv("SWARMGOD_BENCH_CONFIRM")) == benchAuthorityConfirmation {
			return v
		}
	}
	return ""
}

func missionAuthorityEnabled(profile string) bool {
	return missionAuthorityMode(profile) != ""
}

// missionAuthorityActiveLocked reports whether Core currently owns an active
// mission run. Caller must hold missionDispatchMu.
func (s *Server) missionAuthorityActiveLocked() bool {
	if s == nil || !s.missionAuthority || s.mission == nil {
		return false
	}
	snap := s.mission.Snapshot()
	return (snap.Active && snap.Authority) || snap.ReturnState == mission.ReturnStatePending ||
		snap.ReturnState == mission.ReturnStateReturning
}

// swarmNavigationBusy reports whether a Go swarm-owned navigation loop can
// still emit commands (formation active/stopping or RETURN/LAND active).
func (s *Server) swarmNavigationBusy() bool {
	return s != nil && s.missionAuthority && s.swarm != nil && s.swarm.NavigationBusy()
}

// swarmLeaderFormationCompatible proves the split-ownership precondition:
// Mission may own only the fixed leader while an already-running formation with
// that same leader owns the followers. Starting formation after Mission starts is
// forbidden because form-up itself commands the leader.
func (s *Server) swarmLeaderFormationCompatible(plan mission.MissionPlan) bool {
	coordinator := s.missionSwarmCoordinator()
	if s == nil || coordinator == nil || plan.Mode != mission.ModeSwarmLeader || plan.LeaderID == 0 {
		return false
	}
	return coordinator.FollowerAuthorityReadyFor(plan.LeaderID, plan.Participants)
}

func missionSnapshotOwnsDrone(snap mission.Snapshot, id uint32) bool {
	if snap.ReturnState == mission.ReturnStatePending || snap.ReturnState == mission.ReturnStateReturning {
		for _, participant := range snap.ReturnParticipants {
			if participant == id {
				return true
			}
		}
	}
	if !snap.Active || !snap.Authority {
		return false
	}
	if snap.Mode == mission.ModeSwarmLeader {
		return snap.CurrentLeaderID != 0 && id == snap.CurrentLeaderID
	}
	for _, participant := range snap.Participants {
		if participant == id {
			return true
		}
	}
	return false
}

// missionOwnsDroneLocked reports whether the currently active authoritative
// Core run owns navigation for id. Caller must hold missionDispatchMu so the
// result cannot race StartMission/CancelMission/authority dispatch.
func (s *Server) missionOwnsDroneLocked(id uint32) bool {
	if s == nil || !s.missionAuthority || s.mission == nil {
		return false
	}
	snap := s.mission.Snapshot()
	return missionSnapshotOwnsDrone(snap, id)
}

func missionOwnershipReject(command string, id uint32, requestID string) *pb.CommandResult {
	return &pb.CommandResult{
		Ok: false, Command: command, DroneId: id, RequestId: requestID,
		Message: "active Core mission owns navigation — cancel mission first",
	}
}

// cancelMissionForOperatorTargetsLocked makes HOLD/STOP an explicit operator
// takeover. Caller must hold missionDispatchMu. The authoritative run is made
// terminal before any stop/hold command can be emitted, so it cannot resume and
// overwrite the operator command afterwards.
func (s *Server) cancelMissionForOperatorTargetsLocked(ids []uint32) bool {
	if s == nil || !s.missionAuthority || s.mission == nil || len(ids) == 0 {
		return false
	}
	snap := s.mission.Snapshot()
	returnActive := snap.ReturnState == mission.ReturnStatePending ||
		snap.ReturnState == mission.ReturnStateReturning
	if (!snap.Active || !snap.Authority) && !returnActive {
		return false
	}
	owned := false
	for _, id := range ids {
		if missionSnapshotOwnsDrone(snap, id) {
			owned = true
			break
		}
	}
	if !owned {
		return false
	}
	if snap.Active && snap.Authority {
		_ = s.mission.Cancel(snap.RunID)
	}
	if returnActive {
		if coordinator := s.missionSwarmReturnCoordinator(); coordinator != nil {
			coordinator.RevokeReturnNavigation()
		}
		s.invalidateReturnLocked(snap.RunID, "automatic Return invalidated by operator takeover")
	}
	s.cancelInflightMissionSendLocked() // stop any in-flight mission GOTO/HOLD send
	return true
}

func (s *Server) swarmSuccessorIsEligible(id uint32) bool {
	if s == nil || id == 0 {
		return false
	}
	if s.swarmSuccessorEligible != nil {
		return s.swarmSuccessorEligible(id)
	}
	if s.mgr == nil || s.mgr.FailsafeActive(id) {
		return false
	}
	drone := s.mgr.Drone(id)
	return drone != nil && drone.Online(s.cfg.LinkLostSec)
}

// applySwarmMissionTakeoversLocked performs S09-C's selective exclusion and
// succession while the API ownership lock blocks new Mission command claims.
// Existing mission/follower final-write guards are revoked before a new leader
// becomes dispatchable; no lock is held over an FC ACK.
func (s *Server) applySwarmMissionTakeoversLocked(ids []uint32) bool {
	if s == nil || s.mission == nil || !s.mission.AuthoritySwarmLeaderEnabled() {
		return false
	}
	coordinator := s.missionSwarmCoordinator()
	handled := false
	for _, id := range ids {
		snap := s.mission.Snapshot()
		if !snap.Active || !snap.Authority || snap.Mode != mission.ModeSwarmLeader {
			break
		}
		active := false
		for _, participant := range snap.ActiveParticipants {
			if participant == id {
				active = true
				break
			}
		}
		if !active {
			continue
		}
		handled = true
		if id == snap.CurrentLeaderID {
			s.cancelInflightMissionSendLocked()
		}
		transition, ok := s.mission.BeginSwarmOperatorTakeover(id, s.swarmSuccessorIsEligible)
		if !ok || transition.Duplicate {
			continue
		}
		if transition.Interrupted {
			s.cancelInflightMissionSendLocked()
			if coordinator != nil {
				coordinator.RevokeFormationNavigation()
			}
			continue
		}
		rebound := coordinator != nil && coordinator.RebindMissionMembership(
			transition.OldLeaderID, transition.NewLeaderID,
			transition.ActiveParticipants, transition.ExcludedParticipants)
		reason := fmt.Sprintf("SWARM membership committed: leader D%d active=%v excluded=%v",
			transition.NewLeaderID, transition.ActiveParticipants, transition.ExcludedParticipants)
		if !rebound {
			reason = "SWARM formation ownership rebind failed; mission interrupted"
		}
		s.mission.CompleteSwarmOperatorTakeover(
			transition.RunID, transition.Generation, rebound, reason)
		if !rebound {
			s.cancelInflightMissionSendLocked()
			if coordinator != nil {
				coordinator.RevokeFormationNavigation()
			}
		}
	}
	return handled
}

// stopSwarmForTargetsLocked preempts follower formation authority before an
// operator takeover command can be sent.  Stop waits for the loop to finish, and
// tick revalidates ownership at its final send boundary, so a stale formation
// target cannot overwrite HOLD/LAND/RTL/KILL. Caller holds missionDispatchMu.
func (s *Server) stopSwarmForTargetsLocked(ids []uint32) bool {
	if s == nil || len(ids) == 0 {
		return false
	}
	if s.swarm != nil {
		// A takeover on an aircraft flying back (REJOIN) aborts that run first —
		// it is not a formation member yet, so the ownership check below misses it.
		for _, id := range ids {
			s.swarm.AbortRejoin(id)
		}
	}
	if s.applySwarmMissionTakeoversLocked(ids) {
		return true
	}
	if s.swarm == nil {
		return false
	}
	state := s.swarm.State()
	if !state.GetActive() {
		return false
	}
	owned := make(map[uint32]bool)
	owned[state.GetLeaderId()] = true
	for _, edge := range state.GetEdges() {
		owned[edge.GetFollowerId()] = true
	}
	for _, id := range ids {
		if owned[id] {
			s.swarm.RevokeFormationNavigation()
			return true
		}
	}
	return false
}

// missionCommandSink is the narrow adapter that prevents Mission Engine from
// bypassing command.Service/safety.  A rejected or failed command is surfaced as
// an error so the engine terminally FAILS the run and never retries blindly.
type missionCommandSink struct {
	ctx context.Context
	cmd *command.Service
}

func (s *missionCommandSink) Goto(ctx context.Context, droneID uint32, lat, lon, alt float64) error {
	if s == nil || s.cmd == nil {
		return fmt.Errorf("mission command service unavailable")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	result := s.cmd.Goto(ctx, droneID, lat, lon, alt)
	if result == nil {
		return fmt.Errorf("mission GOTO returned no result")
	}
	if !result.Ok {
		return fmt.Errorf("mission GOTO rejected: %s", result.Message)
	}
	return nil
}

func (s *missionCommandSink) Hold(ctx context.Context, droneID uint32) error {
	if s == nil || s.cmd == nil {
		return fmt.Errorf("mission command service unavailable")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	result := s.cmd.Hold(ctx, droneID)
	if result == nil {
		return fmt.Errorf("mission HOLD returned no result")
	}
	if !result.Ok {
		return fmt.Errorf("mission HOLD rejected: %s", result.Message)
	}
	return nil
}

func (s *missionCommandSink) RTL(ctx context.Context, droneID uint32) error {
	if s == nil || s.cmd == nil {
		return fmt.Errorf("mission command service unavailable")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	result := s.cmd.MissionReturnRTL(ctx, droneID)
	if result == nil {
		return fmt.Errorf("mission RTL returned no result")
	}
	if !result.Ok {
		return fmt.Errorf("mission RTL rejected: %s", result.Message)
	}
	return nil
}

// ── helpers สำหรับคำสั่งแบบ multi-target ──
func targetIDs(t *pb.Target) []uint32 {
	if t == nil {
		return nil
	}
	// A target is a set semantically.  Preserve first-occurrence order for
	// deterministic aggregate results, but never let a duplicate ID reserve,
	// preempt, or send twice inside one operator intent.
	ids := make([]uint32, 0, len(t.DroneIds))
	seen := make(map[uint32]struct{}, len(t.DroneIds))
	for _, id := range t.DroneIds {
		if _, duplicate := seen[id]; duplicate {
			continue
		}
		seen[id] = struct{}{}
		ids = append(ids, id)
	}
	return ids
}

// aggregate สร้างผลระดับบน (ok/message สรุป) + เก็บผลรายลำใน PerDrone (spec §8.3)
// reqID = parent request id → stamp ทั้งบนสุดและทุก child (§8.2/§16.1)
func aggregate(cmd, reqID string, results []*pb.CommandResult) *pb.CommandResult {
	for _, r := range results { // stamp parent id ลงทุกผลรายลำ
		if r != nil {
			r.RequestId = reqID
		}
	}
	if len(results) == 0 {
		return &pb.CommandResult{Ok: false, Command: cmd,
			Message: "no target drone selected", RequestId: reqID}
	}
	allOk := true
	var msgs []string
	for _, r := range results {
		if !r.Ok {
			allOk = false
			msgs = append(msgs, fmt.Sprintf("UAV_%d: %s", r.DroneId, r.Message))
		}
	}
	top := &pb.CommandResult{
		Ok: allOk, Command: cmd, RequestId: reqID,
		PerDrone: results, // ← ผลรายลำครบ ไม่ collapse (§8.3)
	}
	if allOk {
		top.Message = fmt.Sprintf("%d drone(s) ACCEPTED", len(results))
	} else {
		top.Message = strings.Join(msgs, "; ")
	}
	return top
}

func setupAllowedUnaryMethod(fullMethod string) bool {
	switch fullMethod {
	case "/swarmgod.v1.SwarmGodService/Connect",
		"/swarmgod.v1.SwarmGodService/Disconnect",
		"/swarmgod.v1.SwarmGodService/GetFleetSnapshot",
		"/swarmgod.v1.SwarmGodService/GetMissionState",
		"/swarmgod.v1.SwarmGodService/ParamGet",
		"/swarmgod.v1.SwarmGodService/ParamList",
		"/swarmgod.v1.SwarmGodService/GetSwarmState":
		return true
	default:
		return false
	}
}

// setupSafetyUnary makes the real-aircraft bootstrap profile fail closed at the
// RPC boundary. setup exists only so the operator can open the UI, connect the
// FC, and inspect telemetry/GPS before choosing a real field Home. No flight,
// mission, swarm, payload, geofence, parameter-write, or mode-changing RPC may
// execute until Core restarts in hil/production with explicit HOME configuration.
func (s *Server) setupSafetyUnary() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo,
		handler grpc.UnaryHandler) (any, error) {
		if s != nil && s.cfg.TelemetryOnly() && !setupAllowedUnaryMethod(info.FullMethod) {
			return nil, status.Error(codes.FailedPrecondition,
				"setup profile is telemetry-only; set SWARMGOD_HOME_LOC and restart Core in hil/production before flight commands")
		}
		return handler(ctx, req)
	}
}

// Serve เปิด gRPC listener (บล็อกจน ctx ยกเลิก)
// Phase 1: plaintext localhost + warning; Phase 6: mTLS (ดู SECURITY.md)
func (s *Server) Serve(ctx context.Context) error {
	var opts []grpc.ServerOption
	mode := "PLAINTEXT (dev — run gencerts for mTLS)"
	creds, tlsErr := s.loadMTLS()
	if tlsErr == nil {
		opts = append(opts, grpc.Creds(creds))
		mode = "mTLS (client cert required)"
	} else if s.auth.strict {
		return fmt.Errorf("production requires mTLS: %w", tlsErr)
	} else {
		log.Printf("[api] warning: mTLS unavailable, dev plaintext fallback: %v", tlsErr)
	}
	lis, err := net.Listen("tcp", s.cfg.GRPCAddr)
	if err != nil {
		return err
	}
	opts = append(opts,
		// Auth remains first/fail-closed. setupSafetyUnary is the real-aircraft
		// telemetry-only bootstrap gate and runs before any command handler/audit.
		// Correlation remains observe-only after both authorization gates admit RPC.
		grpc.ChainUnaryInterceptor(s.auth.unary(), s.setupSafetyUnary(), s.correlationAuditUnary()),
		grpc.ChainStreamInterceptor(s.auth.stream()),
	)
	gs := grpc.NewServer(opts...)
	pb.RegisterSwarmGodServiceServer(gs, s)
	authMode := "dev (token optional)"
	if s.auth.strict {
		authMode = "STRICT (token required)"
	}
	log.Printf("[api] gRPC listening on %s [%s] auth=%s", s.cfg.GRPCAddr, mode, authMode)

	go func() {
		<-ctx.Done()
		log.Println("[api] graceful stop")
		gs.GracefulStop()
	}()
	// F4 Stage A/B1: feed telemetry into the SHADOW mission engine and mirror
	// Core-owned battery/link failsafe ALARMs into mission interruption.  Neither
	// observer issues flight commands; fleet remains the owner of failsafe RTL.
	go s.runMissionObserver(ctx)
	go s.runMissionSafetyObserver(ctx)
	return gs.Serve(lis)
}

// loadMTLS โหลด cert สำหรับ mTLS; production ต้องถือ error นี้เป็น fatal
func (s *Server) loadMTLS() (credentials.TransportCredentials, error) {
	cert, err := tls.LoadX509KeyPair(s.cfg.TLSCert, s.cfg.TLSKey)
	if err != nil {
		return nil, fmt.Errorf("load server certificate: %w", err)
	}
	caPEM, err := os.ReadFile(s.cfg.CACert)
	if err != nil {
		return nil, fmt.Errorf("read CA certificate: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("CA certificate contains no valid PEM certificate")
	}
	return credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{cert},
		ClientAuth:   tls.RequireAndVerifyClientCert, // ← บังคับ client cert
		ClientCAs:    pool,
		MinVersion:   tls.VersionTLS12,
	}), nil
}

// ── Connect: เพิ่มโดรน + เปิด MAVLink ─────────────────────────
func (s *Server) Connect(ctx context.Context, req *pb.ConnectRequest) (*pb.CommandResult, error) {
	proto := req.Protocol
	if proto == "" {
		proto = "tcp"
	}
	err := s.mgr.Connect(s.ctx, req.DroneId, req.Name, proto, req.Host, req.Port)
	if err != nil {
		return &pb.CommandResult{
			Ok: false, DroneId: req.DroneId, Command: "Connect", Message: err.Error(),
		}, nil
	}
	return &pb.CommandResult{
		Ok: true, DroneId: req.DroneId, Command: "Connect",
		Message: "connected",
	}, nil
}

func (s *Server) Disconnect(ctx context.Context, req *pb.DisconnectRequest) (*pb.CommandResult, error) {
	s.mgr.Disconnect(req.DroneId)
	return &pb.CommandResult{Ok: true, DroneId: req.DroneId, Command: "Disconnect"}, nil
}

// ── SubscribeEvents: server-stream alarms/failsafe/failover ───
func (s *Server) SubscribeEvents(_ *pb.EventSubscribeRequest, stream pb.SwarmGodService_SubscribeEventsServer) error {
	id, ch := s.events.Subscribe()
	defer s.events.Unsubscribe(id)
	for {
		select {
		case <-stream.Context().Done():
			return nil
		case ev, ok := <-ch:
			if !ok {
				return nil
			}
			if err := stream.Send(ev); err != nil {
				return err
			}
		}
	}
}

// ── GetFleetSnapshot: state ทุกลำครั้งเดียว ───────────────────
func (s *Server) GetFleetSnapshot(ctx context.Context, _ *pb.FleetSnapshotRequest) (*pb.FleetSnapshot, error) {
	drones := s.mgr.Snapshot()
	return &pb.FleetSnapshot{Drones: drones}, nil
}

// ── flight commands (ผ่าน command.Service → safety + audit) ───
// idempotency: ทุก mutating command ห่อด้วย s.cmd.Idempotent(req.RequestId, id, fn)
// → request_id เดียวกัน (retry/double-click) ไม่ยิงคำสั่งซ้ำ (spec §8.2)

func (s *Server) Arm(ctx context.Context, req *pb.ArmRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runNormalBatch(ctx, "Arm", req.RequestId, ids,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Arm(c, id, req.Force)
		}), nil
}

func (s *Server) Disarm(ctx context.Context, req *pb.DisarmRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "Disarm", req.RequestId, ids, takeoverPriorityDisarm,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Disarm(c, id, req.Confirmed)
		}), nil
}

func (s *Server) Kill(ctx context.Context, req *pb.KillRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	// KILL is the strongest explicit takeover; cancel mission ownership and
	// preempt any in-flight normal op before motor kill so stale mission/normal
	// callbacks can never re-command the vehicle. A takeover lease is never itself
	// preempted, so this send always completes.
	return s.runTakeoverBatch(ctx, "Kill", req.RequestId, ids, takeoverPriorityKill,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Kill(c, id, req.Confirmed)
		}), nil
}

func (s *Server) Takeoff(ctx context.Context, req *pb.TakeoffRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	result := s.idempotentBatch(req.RequestId, func() *pb.CommandResult {
		// Preserve the fail-closed ownership fast path before touching command.Service
		// (state-only authority tests intentionally construct a Server with cmd=nil).
		s.missionDispatchMu.Lock()
		for _, id := range ids {
			if s.missionOwnsDroneLocked(id) {
				s.missionDispatchMu.Unlock()
				return missionOwnershipReject("Takeoff", id, req.RequestId)
			}
		}
		s.missionDispatchMu.Unlock()
		if s.cmd == nil {
			return &pb.CommandResult{Ok: false, Command: "Takeoff", RequestId: req.RequestId,
				Message: "command service unavailable"}
		}

		// Phase 1: establish the ENTIRE batch intent before precheck or any long FC
		// wait. Precheck is read-only, but claiming after it would let a newer STOP
		// complete during precheck and then allow this older Takeoff to claim the
		// now-free slot — reviving a stale operator intent.
		// A later STOP/LAND/etc. can replace any target's exact claim, after which
		// this stale Takeoff can never regain that target even when the newer command
		// completes and releases its own lease.
		batch := s.prepareNormalBatch(ctx, "Takeoff", req.RequestId, ids)
		defer batch.release(s)
		if len(batch.rejected) != 0 {
			rs := make([]*pb.CommandResult, 0, len(ids))
			for _, id := range ids {
				if rejected := batch.rejected[id]; rejected != nil {
					rs = append(rs, rejected)
				} else {
					rs = append(rs, &pb.CommandResult{Ok: false, Command: "Takeoff", DroneId: id,
						RequestId: req.RequestId, Message: "batch not started because another target could not be reserved"})
				}
			}
			r := aggregate("Takeoff", req.RequestId, rs)
			r.Message = "takeoff batch not started: " + r.Message
			return r
		}

		// Phase 2: immutable/read-only safety precheck for every claimed target
		// before any ARM/takeoff side effect. If a newer takeover arrives during
		// precheck it replaces/cancels the claim; runClaimed below then refuses the
		// stale send even after that takeover has completed.
		preflight := make([]*pb.CommandResult, 0, len(ids))
		for _, id := range ids {
			preflight = append(preflight, s.cmd.TakeoffPrecheck(id, req.Altitude, req.Confirmed))
		}
		if checked := aggregate("Takeoff", req.RequestId, preflight); !checked.Ok {
			checked.Message = "precheck failed before arming: " + checked.Message
			return checked
		}

		rs := make([]*pb.CommandResult, 0, len(ids))
		started := make([]*cmdClaim, 0, len(ids))
		operatorPreempted := false
		rollbackSent := 0
		for _, id := range ids {
			claim := batch.claims[id]
			r := s.runClaimed("Takeoff", req.RequestId, claim,
				func(c context.Context, id uint32) *pb.CommandResult {
					return s.cmd.Takeoff(c, id, req.Altitude, req.Confirmed)
				})
			rs = append(rs, r)
			if r != nil && r.Ok {
				started = append(started, claim)
				continue
			}

			// Typed preemption/cancellation: the claim itself is the authority token.
			// A newer operator intent permanently replaces its lease; caller cancellation
			// cancels its context. In either case the old Takeoff has no authority to
			// emit rollback flight commands. Never infer this from human-readable errors.
			if !s.claimCurrent(claim) {
				operatorPreempted = s.claimSuperseded(claim)
				break
			}

			// Genuine FC rejection mid-batch: rollback only started targets whose
			// ORIGINAL Takeoff claim is still current. If a newer takeover already
			// owns a target, no stale rollback LAND is allowed to follow it.
			for _, startedClaim := range started {
				if !s.claimCurrent(startedClaim) {
					continue
				}
				rb := s.runClaimed("TakeoffRollbackLand", "", startedClaim,
					func(c context.Context, id uint32) *pb.CommandResult {
						return s.cmd.Land(c, id)
					})
				if rb != nil && rb.Ok {
					rollbackSent++
				}
			}
			break
		}
		out := aggregate("Takeoff", req.RequestId, rs)
		if !out.Ok && rollbackSent > 0 && !operatorPreempted {
			out.Message += fmt.Sprintf("; rollback LAND sent to %d started drone(s)", rollbackSent)
		}
		return out
	})
	return result, nil
}

func (s *Server) Land(ctx context.Context, req *pb.LandRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "Land", req.RequestId, ids, takeoverPriorityLand,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Land(c, id)
		}), nil
}

func (s *Server) ReturnToLaunch(ctx context.Context, req *pb.RtlRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "RTL", req.RequestId, ids, takeoverPriorityRTL,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.RTL(c, id)
		}), nil
}

func (s *Server) Hold(ctx context.Context, req *pb.HoldRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "Hold", req.RequestId, ids, takeoverPriorityNavigation,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Hold(c, id)
		}), nil
}

// Servo — RPC นี้เคยประกาศไว้ใน .proto แต่ไม่เคยถูก implement (เหมือนบั๊ก SetLeader เดิม)
// UnimplementedSwarmGodServiceServer จึงกลืนคำสั่งเงียบ ๆ: cockpit สั่งไปแล้ว servo ไม่ขยับ
// และไม่มี error กลับมาให้รู้ตัวด้วย
func (s *Server) Servo(ctx context.Context, req *pb.ServoRequest) (*pb.CommandResult, error) {
	id := req.DroneId
	switch req.Action {
	case pb.ServoRequest_RESET:
		// คืนทุกช่องให้รีโมทจริง
		return s.cmd.ServoRelease(id), nil
	case pb.ServoRequest_RELEASE:
		// เลิก override เฉพาะช่องนี้ → สวิตช์บนรีโมทกลับมาคุมช่องนั้นทันที
		return s.cmd.ServoOff(id, req.Channel), nil
	default: // SET — override ช่องนั้นด้วย PWM ที่ระบุ (core ส่งซ้ำให้เอง)
		return s.cmd.Servo(ctx, id, req.Channel, req.Pwm), nil
	}
}

// ParamSet — RPC นี้ประกาศไว้ใน .proto ตั้งแต่แรกแต่ไม่เคย implement
// (เคสเดียวกับ Servo/SetLeader เดิม: UnimplementedSwarmGodServiceServer กลืนคำสั่ง
//
//	เงียบ ๆ cockpit สั่งไปแล้วไม่มีอะไรเกิดขึ้น และไม่มี error กลับมาให้รู้ตัว)
//
// ใช้กับพารามิเตอร์ของ SIM_* ในโหมดทดลองเป็นหลัก — การแก้พารามิเตอร์ FC จริง
// ระหว่างบินอันตราย จึงบล็อกไว้ตอน armed
func (s *Server) ParamSet(ctx context.Context, req *pb.ParamSetRequest) (*pb.CommandResult, error) {
	return s.cmd.SetParam(req.DroneId, req.ParamId, req.Value), nil
}

func (s *Server) SetMode(ctx context.Context, req *pb.SetModeRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runNormalBatch(ctx, "SetMode", req.RequestId, ids,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.SetMode(c, id, req.Mode)
		}), nil
}

func (s *Server) Goto(ctx context.Context, req *pb.GotoRequest) (*pb.CommandResult, error) {
	// Ad-hoc GOTO is never an implicit takeover. Reservation is taken inside the
	// idempotent closure so a same request_id retry replays (not busy-rejects).
	return s.idempotentNormal(ctx, "Goto", req.RequestId, req.DroneId,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.Goto(c, id, req.Lat, req.Lon, req.Alt)
		}), nil
}

func (s *Server) RcMove(ctx context.Context, req *pb.RcMoveRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runNormalBatch(ctx, "RcMove", "", ids,
		func(c context.Context, id uint32) *pb.CommandResult {
			if req.Dir == pb.RcDirection_RC_DIR_STOP && s.swarm != nil {
				s.swarm.AbortRejoin(id) // STOP always wins over a REJOIN in flight
			}
			if rejected := s.formationFollowerMoveReject(id, req.Dir); rejected != nil {
				return rejected
			}
			return s.cmd.RcMove(c, id, req.Dir, req.Speed, req.YawRate)
		}), nil
}

// formationFollowerMoveReject refuses manual MOVE to an aircraft the formation
// loop still owns as a follower — the loop would drag it back every tick.
// RC_DIR_STOP always passes: stopping must never be blocked by ownership.
func (s *Server) formationFollowerMoveReject(id uint32, dir pb.RcDirection) *pb.CommandResult {
	if s.swarm == nil || dir == pb.RcDirection_RC_DIR_STOP {
		return nil
	}
	if s.swarm.RejoinInProgress(id) {
		return &pb.CommandResult{
			Ok: false, Command: "RcMove", DroneId: id,
			Outcome: pb.CommandOutcome_OUTCOME_SAFETY_REJECTED,
			Message: fmt.Sprintf("Drone %d กำลังกลับเข้าขบวน (REJOIN) — รอให้เสร็จ "+
				"หรือกด STOP / TAKE CONTROL เพื่อยกเลิก", id),
		}
	}
	owned, leader := s.swarm.FormationOwnsFollower(id)
	if !owned {
		return nil
	}
	return &pb.CommandResult{
		Ok: false, Command: "RcMove", DroneId: id,
		Outcome: pb.CommandOutcome_OUTCOME_SAFETY_REJECTED,
		Message: fmt.Sprintf("Drone %d is a SWARM follower of leader Drone %d — "+
			"MOVE the leader, or TAKE CONTROL Drone %d first", id, leader, id),
	}
}

func (s *Server) StopAll(ctx context.Context, req *pb.StopAllRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	// prepareTakeoverBatch revokes RETURN and formation write authority without
	// waiting for their goroutines to finish, then establishes every per-drone
	// E-STOP intent atomically before launching parallel FC sends. This keeps the
	// stale-write guarantee without putting a two-second swarm shutdown wait in
	// front of STOP ALL.
	batch := s.prepareTakeoverBatch(ctx, "StopAll", "", ids, takeoverPriorityStopAll)
	defer batch.release(s)

	rs := make([]*pb.CommandResult, len(ids))
	var wg sync.WaitGroup
	for i, id := range ids {
		if rejected := batch.rejected[id]; rejected != nil {
			rs[i] = rejected
			continue
		}
		claim := batch.claims[id]
		wg.Add(1)
		go func(idx int, c *cmdClaim) {
			defer wg.Done()
			rs[idx] = s.runClaimed("StopAll", "", c,
				func(execCtx context.Context, droneID uint32) *pb.CommandResult {
					return s.cmd.Stop(execCtx, droneID)
				})
		}(i, claim)
	}
	wg.Wait()
	return aggregate("StopAll", "", rs), nil
}

func (s *Server) ChangeAlt(ctx context.Context, req *pb.ChangeAltRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "ChangeAlt", req.RequestId, ids, takeoverPriorityNavigation,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.ChangeAlt(c, id, req.Altitude)
		}), nil
}

func (s *Server) EqualizeAlt(ctx context.Context, req *pb.EqualizeAltRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	return s.runTakeoverBatch(ctx, "EqualizeAlt", "", ids, takeoverPriorityNavigation,
		func(c context.Context, id uint32) *pb.CommandResult {
			return s.cmd.ChangeAlt(c, id, req.Altitude)
		}), nil
}

// ── geofence: ตั้งจาก cockpit → บังคับที่ core (safety.Envelope) ──
func (s *Server) SetGeofence(ctx context.Context, req *pb.SetGeofenceRequest) (*pb.CommandResult, error) {
	poly := make([][2]float64, 0, len(req.Points))
	for _, p := range req.Points {
		poly = append(poly, [2]float64{p.Lat, p.Lon})
	}
	result := s.cmd.Idempotent(req.RequestId, 0, func() *pb.CommandResult {
		return s.cmd.SetGeofence(poly, req.Confirmed)
	})
	if !result.Ok {
		s.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, 0, "geofence", "geofence rejected: "+result.Message)
		return result, nil
	}
	if len(poly) == 0 {
		s.events.Publish(pb.EventLevel_EVENT_LEVEL_INFO, 0, "geofence", "geofence cleared")
		return result, nil
	}
	s.events.Publish(pb.EventLevel_EVENT_LEVEL_INFO, 0, "geofence",
		fmt.Sprintf("geofence set (%d vertices) — enforced", len(poly)))
	return result, nil
}

// ── swarm ──
// SetLeader ตั้งตัวแม่ (Head) ตามที่ผู้ใช้เลือก — ปักหมุดไว้ ไม่ให้ auto-pick ทับ
func (s *Server) SetLeader(ctx context.Context, req *pb.SetLeaderRequest) (*pb.CommandResult, error) {
	s.missionDispatchMu.Lock()
	if s.missionAuthorityActiveLocked() {
		snap := s.mission.Snapshot()
		if snap.Mode == mission.ModeSwarmLeader {
			s.missionDispatchMu.Unlock()
			return &pb.CommandResult{Ok: false, Command: "SetLeader", DroneId: req.LeaderId,
				Message: "active SWARM_LEADER mission has a fixed leader — cancel mission first"}, nil
		}
	}
	s.missionDispatchMu.Unlock()
	if err := s.swarm.SetLeader(req.LeaderId); err != nil {
		return &pb.CommandResult{Ok: false, Command: "SetLeader",
			DroneId: req.LeaderId, Message: err.Error()}, nil
	}
	return &pb.CommandResult{Ok: true, Command: "SetLeader", DroneId: req.LeaderId,
		Message: fmt.Sprintf("leader = Drone %d", req.LeaderId)}, nil
}

func (s *Server) SwarmControl(ctx context.Context, req *pb.SwarmControlRequest) (*pb.CommandResult, error) {
	switch req.Action {
	case pb.SwarmControlRequest_START:
		// Formation navigation and Core mission authority are mutually exclusive.
		// Serialize the check+Start so an external StartMission cannot race it.
		s.missionDispatchMu.Lock()
		defer s.missionDispatchMu.Unlock()
		if rejected := s.swarmNavigationReservationRejectLocked(
			"SwarmStart", req.RequestId, nil, true); rejected != nil {
			return rejected, nil
		}
		if s.missionAuthorityActiveLocked() {
			snap := s.mission.Snapshot()
			if snap.Mode == mission.ModeSwarmLeader && s.swarmLeaderFormationCompatible(snap.Plan) {
				return &pb.CommandResult{Ok: true, Command: "SwarmStart",
					Message: "formation already owns SWARM_LEADER followers"}, nil
			}
			return &pb.CommandResult{Ok: false, Command: "SwarmStart",
				Message: "active Core mission owns navigation — cancel mission first"}, nil
		}
		if err := s.swarm.Start(s.ctx); err != nil {
			return &pb.CommandResult{Ok: false, Command: "SwarmStart", Message: err.Error()}, nil
		}
		return &pb.CommandResult{Ok: true, Command: "SwarmStart", Message: "formation started"}, nil
	case pb.SwarmControlRequest_RETURN:
		// RETURN/LAND is an explicit takeover and then becomes a swarm-owned
		// navigation sequence. Keep the ownership lock until that sequence is
		// registered so StartMission will observe NavigationBusy and fail closed.
		s.missionDispatchMu.Lock()
		defer func() { _ = s.persistMissionState(false) }()
		defer s.missionDispatchMu.Unlock()
		if rejected := s.swarmNavigationReservationRejectLocked(
			"SwarmReturn", req.RequestId, req.DroneIds, len(req.DroneIds) == 0); rejected != nil {
			return rejected, nil
		}
		if len(req.DroneIds) == 0 {
			if s.missionAuthorityActiveLocked() {
				snap := s.mission.Snapshot()
				_ = s.mission.Cancel(snap.RunID)
			}
		} else {
			s.cancelMissionForOperatorTargetsLocked(req.DroneIds)
		}
		return s.cmd.Idempotent(req.RequestId, 0, func() *pb.CommandResult {
			if err := s.swarm.ReturnAndLand(
				s.ctx, req.DroneIds, req.ReturnBaseAlt, req.ReturnGap); err != nil {
				return &pb.CommandResult{Ok: false, Command: "SwarmReturn", Message: err.Error()}
			}
			return &pb.CommandResult{Ok: true, Command: "SwarmReturn",
				Message: "กลับฐาน+ลงจอดกันชน...", RequestId: req.RequestId}
		}), nil
	case pb.SwarmControlRequest_REJOIN:
		// REJOIN flies one TAKE CONTROL aircraft back into its reserved slot (climb
		// over → cross → descend), then the follower loop owns it again.
		if len(req.DroneIds) != 1 || req.DroneIds[0] == 0 {
			return &pb.CommandResult{Ok: false, Command: "SwarmRejoin",
				Message: "REJOIN requires exactly one drone"}, nil
		}
		id := req.DroneIds[0]
		s.missionDispatchMu.Lock()
		defer s.missionDispatchMu.Unlock()
		if rejected := s.swarmNavigationReservationRejectLocked(
			"SwarmRejoin", req.RequestId, req.DroneIds, false); rejected != nil {
			return rejected, nil
		}
		if s.missionAuthorityActiveLocked() {
			return &pb.CommandResult{Ok: false, Command: "SwarmRejoin", DroneId: id,
				Message: "active Core mission owns navigation — cancel mission first"}, nil
		}
		return s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			slot, err := s.swarm.Rejoin(id)
			if err != nil {
				return &pb.CommandResult{Ok: false, Command: "SwarmRejoin", DroneId: id,
					Message: err.Error()}
			}
			return &pb.CommandResult{Ok: true, Command: "SwarmRejoin", DroneId: id,
				RequestId: req.RequestId,
				Message:   fmt.Sprintf("Drone %d กำลังกลับเข้าขบวน → ช่อง %d", id, slot+1)}
		}), nil
	case pb.SwarmControlRequest_STOP:
		// Targeted STOP is the explicit TAKE CONTROL transition used by the Cockpit.
		// It changes ownership only; it does not emit a flight/navigation command.
		// Empty drone_ids preserves the historical whole-formation STOP behavior.
		if len(req.DroneIds) > 0 {
			if len(req.DroneIds) != 1 || req.DroneIds[0] == 0 {
				return &pb.CommandResult{Ok: false, Command: "TakeControl",
					Message: "TAKE CONTROL requires exactly one drone"}, nil
			}
			id := req.DroneIds[0]
			s.missionDispatchMu.Lock()
			if s.missionAuthorityActiveLocked() {
				snap := s.mission.Snapshot()
				if snap.Mode != mission.ModeSwarmLeader {
					s.missionDispatchMu.Unlock()
					return &pb.CommandResult{Ok: false, Command: "TakeControl", DroneId: id,
						Message: "active Core mission owns navigation — TAKE CONTROL is only selective for SWARM_LEADER"}, nil
				}
				alreadyExcluded := false
				for _, excluded := range snap.ExcludedParticipants {
					if excluded == id {
						alreadyExcluded = true
						break
					}
				}
				if alreadyExcluded {
					s.missionDispatchMu.Unlock()
					return &pb.CommandResult{Ok: true, Command: "TakeControl", DroneId: id,
						Message: fmt.Sprintf("Drone %d already in individual control", id)}, nil
				}
				if !s.applySwarmMissionTakeoversLocked([]uint32{id}) {
					s.missionDispatchMu.Unlock()
					return &pb.CommandResult{Ok: false, Command: "TakeControl", DroneId: id,
						Message: fmt.Sprintf("Drone %d is not an active SWARM_LEADER participant", id)}, nil
				}
				s.missionDispatchMu.Unlock()
				_ = s.persistMissionState(false)
				return &pb.CommandResult{Ok: true, Command: "TakeControl", DroneId: id,
					Message: fmt.Sprintf("Drone %d excluded from SWARM_LEADER for individual control", id)}, nil
			}
			leader, members, stopped, err := s.swarm.TakeControl(id)
			s.missionDispatchMu.Unlock()
			if err != nil {
				return &pb.CommandResult{Ok: false, Command: "TakeControl", DroneId: id,
					Message: err.Error()}, nil
			}
			if stopped {
				return &pb.CommandResult{Ok: true, Command: "TakeControl", DroneId: id,
					Message: fmt.Sprintf("Drone %d individual control; formation stopped (<2 members remain)", id)}, nil
			}
			return &pb.CommandResult{Ok: true, Command: "TakeControl", DroneId: id,
				Message: fmt.Sprintf("Drone %d individual control; leader Drone %d; members=%v", id, leader, members)}, nil
		}
		fallthrough
	default: // HOLD / untargeted STOP
		s.missionDispatchMu.Lock()
		s.swarm.RevokeReturnNavigation()
		s.swarm.RevokeFormationNavigation()
		if snap := s.mission.Snapshot(); snap.ReturnState == mission.ReturnStatePending ||
			snap.ReturnState == mission.ReturnStateReturning {
			s.invalidateReturnLocked(snap.RunID, "automatic Return invalidated by operator Swarm STOP")
		}
		s.missionDispatchMu.Unlock()
		return &pb.CommandResult{Ok: true, Command: "SwarmStop", Message: "formation navigation revoked"}, nil
	}
}

func (s *Server) SetSwarmConfig(ctx context.Context, req *pb.SwarmConfig) (*pb.CommandResult, error) {
	count := len(s.mgr.IDs()) // ตรวจระยะห่างตามจำนวนโดรนที่ต่ออยู่
	if err := s.swarm.SetConfig(req.Spacing, req.HeadingMode, req.Formation, count); err != nil {
		// safety reject → ตอบกลับ + event เตือน
		s.events.Publish(pb.EventLevel_EVENT_LEVEL_WARN, 0, "swarm", "formation ปฏิเสธ: "+err.Error())
		return &pb.CommandResult{Ok: false, Command: "SwarmConfig", Message: err.Error()}, nil
	}
	return &pb.CommandResult{Ok: true, Command: "SwarmConfig", Message: "formation ตั้งค่าแล้ว"}, nil
}

func (s *Server) GetSwarmState(ctx context.Context, _ *pb.SwarmStateRequest) (*pb.SwarmState, error) {
	return s.swarm.State(), nil
}

// ── SubscribeTelemetry: server-stream telemetry ───────────────
func (s *Server) SubscribeTelemetry(req *pb.SubscribeTelemetryRequest, stream pb.SwarmGodService_SubscribeTelemetryServer) error {
	// filter เฉพาะ id ที่ขอ (ว่าง = ทุกลำ)
	want := map[uint32]bool{}
	for _, id := range req.DroneIds {
		want[id] = true
	}
	id, ch := s.agg.Subscribe()
	defer s.agg.Unsubscribe(id)
	log.Printf("[api] telemetry subscriber #%d connected", id)

	for {
		select {
		case <-stream.Context().Done():
			log.Printf("[api] telemetry subscriber #%d disconnected", id)
			return nil
		case t, ok := <-ch:
			if !ok {
				return nil
			}
			if len(want) > 0 && !want[t.DroneId] {
				continue
			}
			if err := stream.Send(t); err != nil {
				return err
			}
		}
	}
}
