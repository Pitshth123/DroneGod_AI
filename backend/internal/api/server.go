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

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/command"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/mission"
	"github.com/swarmgod/backend/internal/swarm"
	"github.com/swarmgod/backend/internal/telemetry"
)

type Server struct {
	pb.UnimplementedSwarmGodServiceServer
	cfg     config.Config
	mgr     *fleet.Manager
	agg     *telemetry.Aggregator
	cmd     *command.Service
	swarm   *swarm.Manager
	events  *events.Bus
	mission *mission.Engine // SHADOW mission model (F2/F3) — no flight authority yet
	auth    *authInterceptor
	ctx     context.Context
}

func New(ctx context.Context, cfg config.Config, mgr *fleet.Manager,
	agg *telemetry.Aggregator, cmd *command.Service, sw *swarm.Manager, bus *events.Bus,
	val SessionValidator) *Server {
	return &Server{cfg: cfg, mgr: mgr, agg: agg, cmd: cmd, swarm: sw, events: bus,
		mission: mission.NewEngine(nil),
		auth:    newAuthInterceptor(val, cfg.Profile), ctx: ctx}
}

// ── helpers สำหรับคำสั่งแบบ multi-target ──
func targetIDs(t *pb.Target) []uint32 {
	if t == nil {
		return nil
	}
	return t.DroneIds
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
		grpc.ChainUnaryInterceptor(s.auth.unary()),
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
	// F4 Stage A: feed telemetry into the SHADOW mission engine (read-only; issues
	// no command; inert until a mission is started via StartMission).
	go s.runMissionObserver(ctx)
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
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Arm(ctx, id, req.Force)
		}))
	}
	return aggregate("Arm", req.RequestId, rs), nil
}

func (s *Server) Disarm(ctx context.Context, req *pb.DisarmRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Disarm(ctx, id, req.Confirmed)
		}))
	}
	return aggregate("Disarm", req.RequestId, rs), nil
}

func (s *Server) Kill(ctx context.Context, req *pb.KillRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Kill(ctx, id, req.Confirmed)
		}))
	}
	return aggregate("Kill", req.RequestId, rs), nil
}

func (s *Server) Takeoff(ctx context.Context, req *pb.TakeoffRequest) (*pb.CommandResult, error) {
	ids := targetIDs(req.Target)
	// สองเฟส: ตรวจด่านถาวรของทุกลำก่อน เพื่อไม่ให้ลำต้น ๆ ARM/บินไปแล้ว
	// จึงค่อยพบว่าลำท้ายแบตต่ำหรือไม่พร้อม.
	var preflight []*pb.CommandResult
	for _, id := range ids {
		preflight = append(preflight, s.cmd.TakeoffPrecheck(id, req.Altitude, req.Confirmed))
	}
	if checked := aggregate("Takeoff", req.RequestId, preflight); !checked.Ok {
		checked.Message = "precheck failed before arming: " + checked.Message
		return checked, nil
	}
	var rs []*pb.CommandResult
	started := make([]uint32, 0, len(ids))
	for _, id := range ids {
		r := s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Takeoff(ctx, id, req.Altitude, req.Confirmed)
		})
		rs = append(rs, r)
		if r.Ok {
			started = append(started, id)
			continue
		}
		// FC ปฏิเสธกลางชุด: สั่ง LAND เฉพาะลำที่เริ่มสำเร็จแล้วทันที.
		for _, rollbackID := range started {
			_ = s.cmd.Land(ctx, rollbackID)
		}
		break
	}
	result := aggregate("Takeoff", req.RequestId, rs)
	if !result.Ok && len(started) > 0 {
		result.Message += fmt.Sprintf("; rollback LAND sent to %d started drone(s)", len(started))
	}
	return result, nil
}

func (s *Server) Land(ctx context.Context, req *pb.LandRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Land(ctx, id)
		}))
	}
	return aggregate("Land", req.RequestId, rs), nil
}

func (s *Server) ReturnToLaunch(ctx context.Context, req *pb.RtlRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.RTL(ctx, id)
		}))
	}
	return aggregate("RTL", req.RequestId, rs), nil
}

func (s *Server) Hold(ctx context.Context, req *pb.HoldRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.Hold(ctx, id)
		}))
	}
	return aggregate("Hold", req.RequestId, rs), nil
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
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.SetMode(ctx, id, req.Mode)
		}))
	}
	return aggregate("SetMode", req.RequestId, rs), nil
}

func (s *Server) Goto(ctx context.Context, req *pb.GotoRequest) (*pb.CommandResult, error) {
	return s.cmd.Idempotent(req.RequestId, req.DroneId, func() *pb.CommandResult {
		return s.cmd.Goto(ctx, req.DroneId, req.Lat, req.Lon, req.Alt)
	}), nil
}

func (s *Server) RcMove(ctx context.Context, req *pb.RcMoveRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.RcMove(ctx, id, req.Dir, req.Speed, req.YawRate))
	}
	return aggregate("RcMove", "", rs), nil
}

func (s *Server) StopAll(ctx context.Context, req *pb.StopAllRequest) (*pb.CommandResult, error) {
	// ยกเลิกทั้ง formation และ return/land ก่อน ไม่ให้ goroutine เก่ายิงคำสั่งทับ E-STOP
	s.swarm.CancelReturn()
	s.swarm.Stop()
	ids := targetIDs(req.Target)
	rs := make([]*pb.CommandResult, len(ids))
	var wg sync.WaitGroup
	for i, id := range ids {
		wg.Add(1)
		go func(idx int, droneID uint32) {
			defer wg.Done()
			rs[idx] = s.cmd.Stop(ctx, droneID)
		}(i, id)
	}
	wg.Wait()
	return aggregate("StopAll", "", rs), nil
}

func (s *Server) ChangeAlt(ctx context.Context, req *pb.ChangeAltRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.Idempotent(req.RequestId, id, func() *pb.CommandResult {
			return s.cmd.ChangeAlt(ctx, id, req.Altitude)
		}))
	}
	return aggregate("ChangeAlt", req.RequestId, rs), nil
}

func (s *Server) EqualizeAlt(ctx context.Context, req *pb.EqualizeAltRequest) (*pb.CommandResult, error) {
	var rs []*pb.CommandResult
	for _, id := range targetIDs(req.Target) {
		rs = append(rs, s.cmd.ChangeAlt(ctx, id, req.Altitude))
	}
	return aggregate("EqualizeAlt", "", rs), nil
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
		if err := s.swarm.Start(s.ctx); err != nil {
			return &pb.CommandResult{Ok: false, Command: "SwarmStart", Message: err.Error()}, nil
		}
		return &pb.CommandResult{Ok: true, Command: "SwarmStart", Message: "formation started"}, nil
	case pb.SwarmControlRequest_RETURN:
		return s.cmd.Idempotent(req.RequestId, 0, func() *pb.CommandResult {
			if err := s.swarm.ReturnAndLand(
				s.ctx, req.DroneIds, req.ReturnBaseAlt, req.ReturnGap); err != nil {
				return &pb.CommandResult{Ok: false, Command: "SwarmReturn", Message: err.Error()}
			}
			return &pb.CommandResult{Ok: true, Command: "SwarmReturn",
				Message: "กลับฐาน+ลงจอดกันชน...", RequestId: req.RequestId}
		}), nil
	default: // HOLD / STOP
		s.swarm.CancelReturn()
		s.swarm.Stop()
		return &pb.CommandResult{Ok: true, Command: "SwarmStop", Message: "formation stopped"}, nil
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
