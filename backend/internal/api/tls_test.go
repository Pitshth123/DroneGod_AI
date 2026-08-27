package api

import (
	"context"
	"strings"
	"testing"

	"github.com/swarmgod/backend/internal/config"
)

func TestProductionServeFailsClosedWithoutMTLS(t *testing.T) {
	cfg := config.Default()
	cfg.Profile = "production"
	cfg.GRPCAddr = "127.0.0.1:0"
	cfg.TLSCert = t.TempDir() + "/missing-server.crt"
	cfg.TLSKey = t.TempDir() + "/missing-server.key"
	cfg.CACert = t.TempDir() + "/missing-ca.crt"

	s := &Server{cfg: cfg, auth: newAuthInterceptor(fakeVal{}, cfg.Profile)}
	err := s.Serve(context.Background())
	if err == nil || !strings.Contains(err.Error(), "production requires mTLS") {
		t.Fatalf("production must fail closed when TLS is missing, got %v", err)
	}
}
