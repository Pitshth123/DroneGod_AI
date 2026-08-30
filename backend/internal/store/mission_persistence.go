package store

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/swarmgod/backend/internal/mission"
)

func missionChecksum(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

// AllocateMissionRunID returns a monotonic id from the canonical SQLite store.
// Gaps are intentional: a rejected/duplicate Start may consume an id, but an old
// client can never collide with a later process's run.
func (s *Store) AllocateMissionRunID() (uint64, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("store: allocate mission run id: %w", err)
	}
	defer tx.Rollback()
	var id int64
	if err := tx.QueryRowContext(ctx, `
UPDATE mission_run_sequence
SET last_run_id = last_run_id + 1
WHERE singleton_id = 1
RETURNING last_run_id
`).Scan(&id); err != nil {
		return 0, fmt.Errorf("store: allocate mission run id: %w", err)
	}
	if id <= 0 {
		return 0, errors.New("store: invalid allocated mission run id")
	}
	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("store: commit mission run id: %w", err)
	}
	return uint64(id), nil
}

// SaveMissionRecord atomically replaces the singleton last-run evidence. Older
// run/revision writes are ignored so a slow stale callback cannot overwrite a
// newer terminal transition or a newer run.
func (s *Store) SaveMissionRecord(record mission.DurableMissionRecord) error {
	if err := mission.ValidateDurableMissionRecord(record); err != nil {
		return fmt.Errorf("store: refuse invalid mission record: %w", err)
	}
	if record.RunID > math.MaxInt64 || record.Revision > math.MaxInt64 {
		return errors.New("store: mission run/revision exceeds SQLite integer range")
	}
	payload, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("store: marshal mission record: %w", err)
	}
	checksum := missionChecksum(payload)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("store: begin mission record: %w", err)
	}
	defer tx.Rollback()

	var existingRun, existingRevision int64
	err = tx.QueryRowContext(ctx,
		`SELECT run_id, revision FROM mission_state WHERE singleton_id = 1`).
		Scan(&existingRun, &existingRevision)
	switch {
	case err == sql.ErrNoRows:
		// no previous record
	case err != nil:
		return fmt.Errorf("store: read existing mission record: %w", err)
	case uint64(existingRun) > record.RunID:
		return fmt.Errorf("%w: stored run %d is newer than %d",
			mission.ErrStaleDurableRecord, existingRun, record.RunID)
	case uint64(existingRun) == record.RunID && uint64(existingRevision) > record.Revision:
		return fmt.Errorf("%w: stored revision %d is newer than %d",
			mission.ErrStaleDurableRecord, existingRevision, record.Revision)
	case uint64(existingRun) == record.RunID && uint64(existingRevision) == record.Revision:
		return tx.Commit()
	}

	if _, err := tx.ExecContext(ctx, `
INSERT INTO mission_state(singleton_id, schema_version, run_id, revision, record_json, checksum, updated_at)
VALUES(1,?,?,?,?,?,?)
ON CONFLICT(singleton_id) DO UPDATE SET
    schema_version=excluded.schema_version,
    run_id=excluded.run_id,
    revision=excluded.revision,
    record_json=excluded.record_json,
    checksum=excluded.checksum,
    updated_at=excluded.updated_at
`, record.SchemaVersion, int64(record.RunID), int64(record.Revision),
		payload, checksum, nowUTC().Format(time.RFC3339Nano)); err != nil {
		return fmt.Errorf("store: write mission record: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `
UPDATE mission_run_sequence SET last_run_id = ?
WHERE singleton_id = 1 AND last_run_id < ?
`, int64(record.RunID), int64(record.RunID)); err != nil {
		return fmt.Errorf("store: advance mission sequence: %w", err)
	}
	if record.OperationID != "" {
		if _, err := tx.ExecContext(ctx, `
INSERT OR IGNORE INTO mission_operations(operation_id, run_id, plan_id, created_at)
VALUES(?,?,?,?)
`, record.OperationID, int64(record.RunID), record.Plan.PlanID,
			nowUTC().Format(time.RFC3339Nano)); err != nil {
			return fmt.Errorf("store: record mission operation: %w", err)
		}
		var operationRun int64
		if err := tx.QueryRowContext(ctx,
			`SELECT run_id FROM mission_operations WHERE operation_id = ?`,
			record.OperationID).Scan(&operationRun); err != nil {
			return fmt.Errorf("store: verify mission operation: %w", err)
		}
		if uint64(operationRun) != record.RunID {
			return fmt.Errorf("store: operation_id %q already belongs to run %d",
				record.OperationID, operationRun)
		}
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("store: commit mission record: %w", err)
	}
	return nil
}

func (s *Store) LoadMissionRecord() (*mission.DurableMissionRecord, error) {
	var schema int64
	var runID, revision int64
	var payload []byte
	var checksum string
	err := s.db.QueryRow(`
SELECT schema_version, run_id, revision, record_json, checksum
FROM mission_state WHERE singleton_id = 1
`).Scan(&schema, &runID, &revision, &payload, &checksum)
	if err == sql.ErrNoRows {
		return nil, mission.ErrNoDurableMission
	}
	if err != nil {
		return nil, fmt.Errorf("store: load mission record: %w", err)
	}
	loadErr := func(err error) (*mission.DurableMissionRecord, error) {
		return nil, &mission.PersistenceLoadError{
			RunID: uint64(runID), SchemaVersion: uint32(schema), Err: err,
		}
	}
	if runID <= 0 || revision <= 0 || schema < 0 || schema > math.MaxUint32 {
		return loadErr(errors.New("invalid mission persistence envelope"))
	}
	if missionChecksum(payload) != checksum {
		return loadErr(errors.New("mission persistence checksum mismatch"))
	}
	var record mission.DurableMissionRecord
	if err := json.Unmarshal(payload, &record); err != nil {
		return loadErr(fmt.Errorf("decode mission persistence: %w", err))
	}
	if record.SchemaVersion != uint32(schema) || record.RunID != uint64(runID) ||
		record.Revision != uint64(revision) {
		return loadErr(errors.New("mission persistence envelope/payload mismatch"))
	}
	if err := mission.ValidateDurableMissionRecord(record); err != nil {
		return loadErr(err)
	}
	return &record, nil
}

func (s *Store) DeleteMissionRecord(runID uint64) error {
	if runID == 0 || runID > math.MaxInt64 {
		return errors.New("store: invalid mission run id for delete")
	}
	result, err := s.exec(
		`DELETE FROM mission_state WHERE singleton_id = 1 AND run_id = ?`, int64(runID))
	if err != nil {
		return fmt.Errorf("store: delete mission record: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("store: mission delete result: %w", err)
	}
	if affected == 0 {
		return errors.New("store: mission record changed before recovery clear")
	}
	return nil
}

func (s *Store) LookupMissionOperation(operationID string) (uint64, bool, error) {
	if operationID == "" {
		return 0, false, nil
	}
	var runID int64
	err := s.db.QueryRow(
		`SELECT run_id FROM mission_operations WHERE operation_id = ?`, operationID).
		Scan(&runID)
	if err == sql.ErrNoRows {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, fmt.Errorf("store: lookup mission operation: %w", err)
	}
	if runID <= 0 {
		return 0, false, errors.New("store: invalid mission operation run id")
	}
	return uint64(runID), true, nil
}
