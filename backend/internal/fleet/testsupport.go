package fleet

import (
	"context"

	"github.com/swarmgod/backend/internal/audit"
	"github.com/swarmgod/backend/internal/config"
	"github.com/swarmgod/backend/internal/events"
	"github.com/swarmgod/backend/internal/safety"
)

// This file exposes connection-free construction so higher layers (command
// post-mission Return, swarm form-up) can be exercised against the REAL Drone
// failsafe/final-write guard paths in tests, without opening a live MAVLink
// connection. It performs no I/O, opens no goroutines, and is not referenced by
// any production wiring — only by cross-package tests. Kept out of a _test.go
// file solely because Go test helpers cannot be shared across packages otherwise.

// NewManagerForTest builds a Manager with initialized registries and no MAVLink
// dials. Failsafe state starts inactive, matching a freshly connected fleet.
func NewManagerForTest(cfg config.Config, env *safety.Envelope, aud *audit.Logger, bus *events.Bus) *Manager {
	return &Manager{
		cfg:     cfg,
		audit:   aud,
		events:  bus,
		env:     env,
		drones:  make(map[uint32]*Drone),
		cancels: make(map[uint32]context.CancelFunc),
		fsLink:  make(map[uint32]int),
		fsBatt:  make(map[uint32]bool),
		herded:  make(map[uint32]bool),
	}
}

// RegisterDroneForTest inserts a connection-free drone backed by conn and returns
// it, so a test can feed telemetry via HandleFrame and read Nav(). conn receives
// exactly the transport writes a real link would, which is how a test counts (or
// blocks) the final FC write.
func (m *Manager) RegisterDroneForTest(id uint32, name string, conn sender) *Drone {
	d := newDrone(id, name, "test", 0, conn)
	m.mu.Lock()
	m.drones[id] = d
	m.mu.Unlock()
	return d
}

// LatchBatteryFailsafeForTest establishes the battery failsafe latch exactly as
// failsafeTick does on a real battery-critical, so an interleaving test can win
// the latch at a chosen instant relative to a navigation write.
func (m *Manager) LatchBatteryFailsafeForTest(id uint32) {
	m.fsMu.Lock()
	m.fsBatt[id] = true
	m.fsMu.Unlock()
}
