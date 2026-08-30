# V3-S10 — Persistence + Restartability

**Status: COMPLETE (verified 2026-08-29).**

> **Completion statement (proven by tests):** *An unfinished Core mission survives a
> Core restart as accurate durable recovery evidence, but never survives as automatic
> flight authority.*

This session was a **continuation**: the durable persistence architecture below was
already implemented by a prior session. This document records the final
implementation and the one remaining defect that was fixed to reach completion.

---

## 1. Architecture (already in place, preserved)

Mission durability rides on the existing Core SQLite store — no parallel store, no
replacement.

| File | Role |
|------|------|
| `backend/internal/mission/persistence.go` | `DurableMissionRecord`, `DurableRecord()`, `RestoreDurableMission()`, schema/checksum validation, `PersistenceFault()` |
| `backend/internal/store/mission_persistence.go` | SQLite `mission_state` singleton + operation-id history; monotonic `AllocateMissionRunID`; `LookupMissionOperation`; revision/terminal overwrite guards |
| `backend/internal/store/migrations.go` | `mission_state` / `mission_operations` schema |
| `backend/internal/api/mission_persistence.go` | `restoreDurableMission()`, revision-gated `persistMissionState()`, `missionDurableForDispatch()`, fault surfacing |
| `proto/swarmgod/v1/mission.proto` | `MissionStateResponse` S10 recovery fields (17–27) |

Persisted evidence: `run_id`, `operation_id`, immutable plan, participants, state,
GROUPED `current_index`, SEPARATE per-drone `sep_index`, WAIT snapshots (frozen
remaining), last transition/reason/timestamp, `revision`, participant rejections,
recovery fields, origin/writer session ids, schema version + checksum.

Restart safety (unchanged, preserved): an unfinished persisted mission restores as
**INTERRUPTED / `recovery_required=true`, `active=false`, `authority=false`**, and
deliberately does **not** restore command context, timers, flight claims, send
guards, generation tokens, or transport authority. A lost Start reply or stale
CancelMission from a previous Core session cannot create duplicate authority or
cancel a newer run. Persistence write failure suppresses progression + raises an
operator-visible ALARM without inventing LAND/RTL.

---

## 2. Root cause of the remaining failure

Failing test on takeover: `TestCoreRestartSeparateIndexesVisibleWithZeroDispatch`
(`backend/internal/api`). Observed-before-restart SEPARATE indexes `{D1:1, D2:0}`
came back as `{D1:0, D2:0}` after restart. Recovery safety itself was already
correct (Active/Authority=false, RECOVERY_REQUIRED, no auto GOTO/HOLD) — the defect
was **inaccurate durable progression evidence**, not restart authority.

`persistMissionState` is intentionally revision-gated: it skips the SQLite write when
`run_id`+`revision` are unchanged, so raw telemetry never becomes a DB write. The bug
was that a **SEPARATE per-drone advance did not create a revision boundary**:

- `Engine.advance` GROUPED branch calls `setState(...,"advance",...)` → `Revision++`.
- `Engine.advance` SEPARATE branch set `run.sepIndex[scope]` then called
  `recomputeActiveState`, which bumps revision **only if the RUNNING/WAITING label
  changes**. A lone drone advancing while another keeps flying stays RUNNING →
  `Revision` unchanged → `persistMissionState` skipped the write → the moved index was
  lost on restart.

The same bug class also affected **WAIT entry during concurrent SEPARATE transit**
(`reachWaypoint` registered `run.waits[scope]` but only called `setState(WAITING)`
when nothing else was transiting; a concurrent hold left the run RUNNING at an
unchanged revision). This path is not reachable under the live authority tokens
(SEPARATE WAIT is deferred at the eligibility gate) but is real at the engine model
level, so it was fixed for correctness parity.

Verified against the checklist — GROUPED `current_index`, WAIT exit (`Poll`→`advance`),
terminal transitions, participant rejections, and recovery conversions already create
proper revision boundaries via `transition`/`setState` and were left unchanged.

---

## 3. Changes made this session

Minimal, engine-local:

- **`backend/internal/mission/engine.go` — `advance` (SEPARATE branch):** a lone
  per-drone advance now records an explicit `"advance"` transition to the reconciled
  active state (WAITING only when every unfinished scope is now holding), creating a
  durable revision boundary so the Server persists the moved `sep_index`.
- **`backend/internal/mission/engine.go` — `reachWaypoint`:** a concurrent SEPARATE
  WAIT entry (run stays RUNNING) now also records a revision boundary so the hold is
  persisted.
- **`backend/internal/mission/util.go` — `sepIndexReason`:** helper labeling the
  SEPARATE per-drone progression boundary (`WP N (D<id>)`).
- **Tests:** `backend/internal/mission/persistence_test.go` gains
  `TestSeparateProgressionAndWaitCreateDurableRevisionBoundary` (lone advance + concurrent
  WAIT each bump `Revision`).

Telemetry with no meaningful progression still produces **no** revision change and
**no** DB write — `advance`/`reachWaypoint` only run on real arrival / WAIT-exit, not
on every `Observe`.

No changes to SQLite, the persistence API surface, the recovery model, or the
frontend (its `CORE RECOVERY REQUIRED` presentation and fail-closed
`authority_slot_idle` were already correct).

---

## 4. Test evidence

```
cd backend
go test ./internal/api -run TestCoreRestartSeparateIndexesVisibleWithZeroDispatch -count=1   # ok
go test ./internal/mission ./internal/store ./internal/api -count=1                          # ok / ok / ok
go test ./... -count=1                                                                       # all ok
go vet ./...                                                                                 # clean
```

Frontend (focused, not the full slow Qt GUI suite):

```
cd frontend
python -m pytest tests/test_mission_shadow.py -q                                            # 36 passed
```

Key covering tests: `TestCoreRestartSeparateIndexesVisibleWithZeroDispatch`,
`TestSeparateProgressionAndWaitCreateDurableRevisionBoundary`,
`TestCoreRestartActiveMissionBecomesRecoveryWithZeroCommands`,
`TestCoreRestartWaitDoesNotContinueTimerHoldOrWaypoint`,
`TestCorruptPersistenceStartsVisibleIncompatibleRecoveryWithZeroCommands`,
`TestPersistenceWriteFailureStopsAuthorityWithoutInventingFlightAction`,
`TestRecoveryClearAndLostStartRetryAcrossRestart`,
`TestStaleCancelCannotCancelNewerRunSend`,
`TestMissionRunIDMonotonicAcrossStoreRestart`,
`TestMissionPersistenceCorruptUnknownAndTruncatedFailSafe`.

---

## 5. Authority gate

Only **`core-single`** and **`core-single-wait`** remain live authority tokens. S10
did **not** flip S09 authority. `core-grouped-multi`, `core-separate`,
`core-swarm-leader`, `core-wave`, `core-payload` remain OFF. S09 remains PRE-FLIP
(authority NOT flipped); SWARM follower operator-takeover = DECISION REQUIRED;
`rtl_after` = BLOCKED. None of these block S10.

> **Superseded 2026-08-30:** both items above were later resolved — SWARM
> operator-takeover/leader-succession (S09-C) and the Return Policy (S09-F) are now
> ratified and implemented PRE-FLIP. The statuses recorded here were accurate at the
> time of this stage; the authority gate itself is unchanged.
