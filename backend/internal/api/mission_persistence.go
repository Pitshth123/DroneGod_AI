package api

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	pb "github.com/swarmgod/backend/gen/swarmgod/v1"
	"github.com/swarmgod/backend/internal/mission"
)

func newCoreMissionSessionID() string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err == nil {
		return hex.EncodeToString(raw[:])
	}
	// This is identity, not a credential. The fallback still prevents ordinary
	// same-process/session reuse if the platform RNG is unavailable.
	return fmt.Sprintf("fallback-%d-%d", os.Getpid(), time.Now().UnixNano())
}

func (s *Server) publishMissionPersistenceFault(message string) {
	if message == "" {
		return
	}
	log.Printf("[core] CRITICAL mission persistence: %s", message)
	if s != nil && s.events != nil {
		s.events.Publish(pb.EventLevel_EVENT_LEVEL_ALARM, 0,
			"mission-persistence", message)
	}
}

// restoreDurableMission runs during Server construction. It only installs
// command-inert Engine state and never calls any dispatcher, command.Service,
// swarm manager or payload action engine.
func (s *Server) restoreDurableMission() {
	if s == nil || s.mission == nil || s.missionStore == nil {
		return
	}
	record, err := s.missionStore.LoadMissionRecord()
	if errors.Is(err, mission.ErrNoDurableMission) {
		return
	}
	if err != nil {
		var loadErr *mission.PersistenceLoadError
		if errors.As(err, &loadErr) {
			s.mission.SetIncompatibleRecovery(loadErr.RunID, loadErr.SchemaVersion, loadErr.Error())
		} else {
			s.mission.SetIncompatibleRecovery(0, 0, err.Error())
		}
		s.missionPersistMu.Lock()
		s.missionPersistenceFault = err.Error()
		s.missionPersistMu.Unlock()
		s.publishMissionPersistenceFault(err.Error())
		return
	}
	if record == nil {
		reason := "mission persistence returned an empty record"
		s.mission.SetIncompatibleRecovery(0, 0, reason)
		s.missionPersistMu.Lock()
		s.missionPersistenceFault = reason
		s.missionPersistMu.Unlock()
		s.publishMissionPersistenceFault(reason)
		return
	}
	if err := s.mission.RestoreDurableMission(*record); err != nil {
		s.mission.SetIncompatibleRecovery(record.RunID, record.SchemaVersion, err.Error())
		s.missionPersistMu.Lock()
		s.missionPersistenceFault = err.Error()
		s.missionPersistMu.Unlock()
		s.publishMissionPersistenceFault(err.Error())
		return
	}
	s.missionPersistMu.Lock()
	s.missionPersistedRunID = record.RunID
	s.missionPersistedRevision = record.Revision
	s.missionPersistenceFault = ""
	s.missionPersistMu.Unlock()

	// A first startup after an unfinished run converts it to INTERRUPTED recovery
	// evidence and persists that conversion. Failure here remains safe because the
	// Engine is already terminal/recovery-required and emits no command.
	snap := s.mission.Snapshot()
	if snap.RecoveryRequired && snap.Revision > record.Revision {
		if err := s.persistMissionState(false); err != nil {
			s.publishMissionPersistenceFault(err.Error())
		}
	}
}

// persistMissionState writes only when the run revision changed. When failActive
// is true, a required write failure terminally suppresses further mission
// progression but deliberately emits no LAND/RTL or other aircraft command.
func (s *Server) persistMissionState(failActive bool) error {
	if s == nil || s.mission == nil || s.missionStore == nil {
		return nil
	}
	record, ok := s.mission.DurableRecord(s.missionSessionID)
	if !ok {
		return nil
	}

	s.missionPersistMu.Lock()
	if s.missionPersistedRunID == record.RunID &&
		s.missionPersistedRevision == record.Revision {
		s.missionPersistMu.Unlock()
		return nil
	}
	err := s.missionStore.SaveMissionRecord(*record)
	if err == nil {
		s.missionPersistedRunID = record.RunID
		s.missionPersistedRevision = record.Revision
		s.missionPersistenceFault = ""
	} else {
		s.missionPersistenceFault = err.Error()
	}
	s.missionPersistMu.Unlock()
	if err == nil {
		return nil
	}

	s.publishMissionPersistenceFault(err.Error())
	if failActive {
		s.missionDispatchMu.Lock()
		current := s.mission.Snapshot()
		returnActive := current.ReturnState == mission.ReturnStatePending ||
			current.ReturnState == mission.ReturnStateReturning
		if (current.Active || returnActive) && current.RunID == record.RunID &&
			current.Revision == record.Revision {
			if s.mission.PersistenceFault(record.RunID, record.Revision,
				"mission persistence fault: "+err.Error()) {
				s.cancelInflightMissionSendLocked()
				s.invalidateReturnLocked(record.RunID, "mission persistence fault: "+err.Error())
			}
		}
		s.missionDispatchMu.Unlock()
	}
	return err
}

func (s *Server) missionDurableForDispatch() bool {
	if s == nil || s.missionStore == nil {
		return true
	}
	if err := s.persistMissionState(true); err != nil {
		return false
	}
	snap := s.mission.Snapshot()
	if !snap.Active {
		return false
	}
	s.missionPersistMu.Lock()
	durable := s.missionPersistedRunID == snap.RunID &&
		s.missionPersistedRevision == snap.Revision
	s.missionPersistMu.Unlock()
	return durable
}

func (s *Server) missionDurableForReturnDispatch() bool {
	if s == nil || s.mission == nil || !s.mission.ReturnDispatchPending() {
		return false
	}
	if s.missionStore == nil {
		return true
	}
	if err := s.persistMissionState(false); err != nil {
		snap := s.mission.Snapshot()
		s.mission.SuppressReturn(snap.RunID, "Return persistence fault: "+err.Error())
		_ = s.persistMissionState(false)
		return false
	}
	snap := s.mission.Snapshot()
	if snap.ReturnState != mission.ReturnStatePending {
		return false
	}
	s.missionPersistMu.Lock()
	durable := s.missionPersistedRunID == snap.RunID &&
		s.missionPersistedRevision == snap.Revision
	s.missionPersistMu.Unlock()
	return durable
}

func (s *Server) snapshotWithPersistenceStatus() mission.Snapshot {
	snap := s.mission.Snapshot()
	s.missionPersistMu.Lock()
	if s.missionPersistedRunID == snap.RunID {
		snap.PersistedRevision = s.missionPersistedRevision
	}
	snap.PersistenceFault = s.missionPersistenceFault
	s.missionPersistMu.Unlock()
	snap.CoreSessionID = s.missionSessionID
	return snap
}
