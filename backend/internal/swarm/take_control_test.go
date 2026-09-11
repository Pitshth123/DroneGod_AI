package swarm

import "testing"

// Manual MOVE routing (cockpit INDIVIDUAL / SWARM): a follower the formation
// loop still owns must not accept operator velocity — the loop would drag it
// back every tick. Only the leader, TAKE CONTROL-excluded aircraft, or aircraft
// outside an active formation are free for manual MOVE.
func TestFormationOwnsFollowerOnlyForActiveNonLeaderMembers(t *testing.T) {
	m := &Manager{active: true, ready: true, leaderID: 1,
		manualExcluded: map[uint32]bool{3: true}}
	for _, tc := range []struct {
		id    uint32
		owned bool
	}{{0, false}, {1, false}, {2, true}, {3, false}, {4, true}} {
		owned, leader := m.FormationOwnsFollower(tc.id)
		if owned != tc.owned {
			t.Fatalf("Drone %d owned=%v, want %v", tc.id, owned, tc.owned)
		}
		if owned && leader != 1 {
			t.Fatalf("Drone %d must report leader Drone 1, got %d", tc.id, leader)
		}
	}
	// Form-up (ready=false) is still moving followers one by one.
	m.ready = false
	if owned, _ := m.FormationOwnsFollower(2); !owned {
		t.Fatal("followers are formation-owned during form-up too")
	}
}

func TestFormationOwnsFollowerReleasedWhenFormationNotDriving(t *testing.T) {
	for name, mutate := range map[string]func(*Manager){
		"inactive": func(m *Manager) { m.active = false },
		"stopping": func(m *Manager) { m.stopping = true },
		"halted":   func(m *Manager) { m.halted = true },
		"mission":  func(m *Manager) { m.missionLeaderID = 1 }, // Mission ownership path guards this
	} {
		m := &Manager{active: true, ready: true, leaderID: 1}
		mutate(m)
		if owned, _ := m.FormationOwnsFollower(2); owned {
			t.Fatalf("%s: formation must not claim Drone 2", name)
		}
	}
}
