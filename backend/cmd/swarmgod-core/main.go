// swarmgod-core — entry point ของ Go core (gRPC server + MAVLink fleet manager)
//
//	go run ./cmd/swarmgod-core
//
// ประกอบ: store (SQLite) → audit (JSONL + async index) → safety envelope →
// fleet manager (MAVLink, 1 goroutine/ลำ) → telemetry aggregator → swarm
// manager → command service → gRPC server (mTLS ถ้ามี certs, ไม่งั้น plaintext dev)
package main

import (
	"context"
	"io"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/swarmgod/backend/internal/api"
	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/command"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/fleet"
	"github.com/swarmgod/backend/internal/safety"
	"github.com/swarmgod/backend/internal/store"
	"github.com/swarmgod/backend/internal/swarm"
	"github.com/swarmgod/backend/internal/telemetry"
)

func main() {
	cfg := config.Load()
	if err := cfg.Validate(); err != nil {
		log.Fatalf("[core] unsafe configuration: %v", err)
	}

	if err := os.MkdirAll(cfg.LogDir, 0o755); err != nil {
		log.Fatalf("[core] mkdir log dir %s: %v", cfg.LogDir, err)
	}
	if err := os.MkdirAll(cfg.AuditDir, 0o755); err != nil {
		log.Fatalf("[core] mkdir audit dir %s: %v", cfg.AuditDir, err)
	}
	closeLog := setupLogging(cfg.LogDir)
	defer closeLog()

	log.Printf("[core] SwarmGod core starting — profile=%s grpc=%s", cfg.Profile, cfg.GRPCAddr)

	st, err := store.Open(cfg.DBPath)
	if err != nil {
		log.Fatalf("[core] open store %s: %v", cfg.DBPath, err)
	}
	defer st.Close()

	aud, err := audit.New(cfg.AuditDir)
	if err != nil {
		log.Fatalf("[core] audit: %v", err)
	}
	defer aud.Close()
	aud.SetIndexer(st.NewAsyncIndexer(1024))

	bus := events.New()
	env := safety.New(cfg)
	agg := telemetry.New()
	mgr := fleet.NewManager(cfg, agg, aud, bus, env, st)
	sw := swarm.NewManager(cfg, mgr, env, aud, bus)
	cmdSvc := command.NewService(mgr, env, aud)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go mgr.Run(ctx)         // telemetry broadcast ticker
	go mgr.RunFailsafe(ctx) // link-loss + battery failsafe
	go mgr.RunRegistry(ctx) // vehicle registry reconcile (best-effort)

	srv := api.New(ctx, cfg, mgr, agg, cmdSvc, sw, bus, st)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("[core] shutdown signal received")
		cancel()
	}()

	if err := srv.Serve(ctx); err != nil {
		log.Fatalf("[core] gRPC serve: %v", err)
	}
	log.Println("[core] stopped")
}

// setupLogging ส่ง log output ไปทั้ง stdout และไฟล์ logs/core-YYYYMMDD.log (หมุนตามวันที่เริ่มโปรเซส)
// launcher.py เก็บ stdout ซ้ำไว้อีกชั้น (core-stdout.log) เผื่อ core panic ก่อนถึงบรรทัดนี้
func setupLogging(dir string) func() {
	name := "core-" + time.Now().Format("20060102") + ".log"
	f, err := os.OpenFile(filepath.Join(dir, name), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		log.Printf("[core] open log file: %v (stdout only)", err)
		return func() {}
	}
	log.SetOutput(io.MultiWriter(os.Stdout, f))
	return func() { f.Close() }
}
