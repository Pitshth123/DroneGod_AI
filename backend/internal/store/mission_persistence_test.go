package store

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/swarmgod/backend/internal/mission"
)

func storeMissionPlan() mission.MissionPlan {
	return mission.MissionPlan{
		PlanID: "store-plan", Mode: mission.ModeGrouped, Participants: []uint32{1},
		Routes: []mission.Route{{DroneID: 0, Points: []mission.Waypoint{
			{Seq: 0, Lat: 14, Lon: 100, Alt: 20},
			{Seq: 1, Lat: 14.001, Lon: 100, Alt: 20},
		}}},
	}
}

func storeMissionRecord(t *testing.T, runID uint64) (mission.DurableMissionRecord, *mission.Engine) {
	t.Helper()
	clock := func() time.Time { return time.Unix(1_700_000_000, 0) }
	engine := mission.NewEngine(clock)
	engine.EnableAuthority()
	if _, err := engine.StartOpWithRunID(storeMissionPlan(), "store-operation", runID); err != nil {
		t.Fatal(err)
	}
	record, ok := engine.DurableRecord("store-session")
	if !ok {
		t.Fatal("missing durable record")
	}
	return *record, engine
}

func TestMissionPersistenceRoundtripAndOperationLookup(t *testing.T) {
	s := openTest(t)
	id1, err := s.AllocateMissionRunID()
	if err != nil {
		t.Fatal(err)
	}
	id2, err := s.AllocateMissionRunID()
	if err != nil {
		t.Fatal(err)
	}
	if id2 != id1+1 {
		t.Fatalf("mission run ids not monotonic: %d then %d", id1, id2)
	}
	record, _ := storeMissionRecord(t, id2)
	if err := s.SaveMissionRecord(record); err != nil {
		t.Fatal(err)
	}
	loaded, err := s.LoadMissionRecord()
	if err != nil {
		t.Fatal(err)
	}
	if loaded.RunID != record.RunID || loaded.Revision != record.Revision ||
		loaded.Plan.PlanID != record.Plan.PlanID ||
		loaded.WriterSessionID != record.WriterSessionID {
		t.Fatalf("mission roundtrip mismatch: got=%+v want=%+v", loaded, record)
	}
	runID, found, err := s.LookupMissionOperation(record.OperationID)
	if err != nil || !found || runID != record.RunID {
		t.Fatalf("operation lookup: run=%d found=%v err=%v", runID, found, err)
	}
}

func TestMissionRunIDMonotonicAcrossStoreRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "restart.db")
	first, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	id1, err := first.AllocateMissionRunID()
	if err != nil {
		t.Fatal(err)
	}
	first.Close()

	second, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	id2, err := second.AllocateMissionRunID()
	if err != nil {
		t.Fatal(err)
	}
	if id2 <= id1 {
		t.Fatalf("restart reused run_id: first=%d second=%d", id1, id2)
	}
}

func TestMissionPersistenceOlderRevisionCannotOverwriteTerminal(t *testing.T) {
	s := openTest(t)
	record, engine := storeMissionRecord(t, 10)
	if err := s.SaveMissionRecord(record); err != nil {
		t.Fatal(err)
	}
	if err := engine.Cancel(10); err != nil {
		t.Fatal(err)
	}
	terminal, _ := engine.DurableRecord("store-session")
	if err := s.SaveMissionRecord(*terminal); err != nil {
		t.Fatal(err)
	}
	if err := s.SaveMissionRecord(record); !errors.Is(err, mission.ErrStaleDurableRecord) {
		t.Fatalf("old active record overwrite = %v, want stale error", err)
	}
	loaded, err := s.LoadMissionRecord()
	if err != nil {
		t.Fatal(err)
	}
	if loaded.State != mission.StateCancelled || loaded.Revision != terminal.Revision {
		t.Fatalf("terminal record overwritten: %+v", loaded)
	}
}

func TestMissionPersistenceCorruptUnknownAndTruncatedFailSafe(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(*testing.T, *Store, mission.DurableMissionRecord)
	}{
		{name: "checksum", mutate: func(t *testing.T, s *Store, _ mission.DurableMissionRecord) {
			if _, err := s.db.Exec(`UPDATE mission_state SET checksum='bad'`); err != nil {
				t.Fatal(err)
			}
		}},
		{name: "truncated", mutate: func(t *testing.T, s *Store, _ mission.DurableMissionRecord) {
			payload := []byte("{")
			if _, err := s.db.Exec(
				`UPDATE mission_state SET record_json=?, checksum=?`,
				payload, missionChecksum(payload)); err != nil {
				t.Fatal(err)
			}
		}},
		{name: "unknown schema", mutate: func(t *testing.T, s *Store, record mission.DurableMissionRecord) {
			record.SchemaVersion = mission.DurableMissionSchemaVersion + 1
			payload, err := json.Marshal(record)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := s.db.Exec(
				`UPDATE mission_state SET schema_version=?, record_json=?, checksum=?`,
				record.SchemaVersion, payload, missionChecksum(payload)); err != nil {
				t.Fatal(err)
			}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := openTest(t)
			record, _ := storeMissionRecord(t, 20)
			if err := s.SaveMissionRecord(record); err != nil {
				t.Fatal(err)
			}
			tc.mutate(t, s, record)
			_, err := s.LoadMissionRecord()
			var loadErr *mission.PersistenceLoadError
			if !errors.As(err, &loadErr) {
				t.Fatalf("corruption must return typed fail-safe error: %v", err)
			}
			if loadErr.RunID != record.RunID {
				t.Fatalf("load error lost run identity: %+v", loadErr)
			}
		})
	}
}

func TestMissionPersistenceDeleteRequiresExactRun(t *testing.T) {
	s := openTest(t)
	record, _ := storeMissionRecord(t, 30)
	if err := s.SaveMissionRecord(record); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteMissionRecord(31); err == nil {
		t.Fatal("stale recovery clear must not delete another run")
	}
	if _, err := s.LoadMissionRecord(); err != nil {
		t.Fatalf("stale delete removed record: %v", err)
	}
	if err := s.DeleteMissionRecord(30); err != nil {
		t.Fatal(err)
	}
	if _, err := s.LoadMissionRecord(); !errors.Is(err, mission.ErrNoDurableMission) {
		t.Fatalf("deleted mission still loads: %v", err)
	}
}
