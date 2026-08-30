# V3-S11 — Failure Injection Expansion

**Status: COMPLETE (verified 2026-08-29).**

S11 proves that injected failures end in a **deterministic SAFE STATE** — not that
the system keeps flying. This was a **continuation**: most families were already
covered by the existing F7/S04/S07–S10 suite and the PRE-FLIP test-only mission
modes. This session audited that coverage, reused it rather than duplicating it, and
added deterministic tests only for the genuine gaps. **No production bug was found**
— every safety invariant already held.

---

## 1. Coverage matrix

| Family | Verdict | Evidence |
|--------|---------|----------|
| **A. Command gateway delay/error** | COVERED | `TestMissionSendRunsWithLockReleased`, `TestTakeoverShortSectionNotBlockedByInFlightNormalOp`, `TestCommandServiceRefusesPreemptedSend`, `TestGotoContextGuardCancelWinsBeforeTransportWrite` / `TestSendCmdGuardCancelWinsBeforeCommandLongWrite` (fleet), `TestAuthorityRejectFailsMissionAndNeverRetries` (mission), `TestRejectedManualGotoDoesNotCorruptAuthorityProgression` |
| **B. Emergency contention/preemption** | COVERED | `TestKillPreemptsWeakerTakeover`, `TestWeakerTakeoverCannotPreemptKill`, `TestEveryOverlappingOlderBatchStaysInvalidAfterNewerStopCompletes`, `TestSequentialOldLandCannotSendD2AfterStopAllCompleted`, `TestStartMissionFailsClosedWhileParticipantReserved`, `TestArmTakeoffAndSwarmStartRejectWhileCoreOwnsNavigation` |
| **C. Duplicate/late command response** | COVERED | `TestSameRequestIdNormalReplaysNotBusyRejects`, `TestCompletedSameRequestIdReplaysWithoutResend`, `TestOldNormalBatchCannotResumeLaterTargetAfterTakeoverCompletes`, `TestIdempotentDedup`, `TestIdempotentInflightWaitsBeyondLegacyTenSecondWindow` |
| **D. Persistence crash boundaries** | PARTIAL → completed | reused: `TestCoreRestartActiveMissionBecomesRecoveryWithZeroCommands`, `TestCoreRestartWaitDoesNotContinueTimerHoldOrWaypoint`, `TestCoreRestartSeparateIndexesVisibleWithZeroDispatch`, `TestRecoveryClearAndLostStartRetryAcrossRestart`, `TestPersistenceWriteFailureStopsAuthorityWithoutInventingFlightAction`. **NEW:** GROUPED progression + recovery-clear DELETE boundary (§3) |
| **E. Corrupted persistence expansion** | PARTIAL → completed | reused: `TestMissionPersistenceCorruptUnknownAndTruncatedFailSafe` (store), `TestCorruptPersistenceStartsVisibleIncompatibleRecoveryWithZeroCommands` (api). **NEW:** semantic-inconsistency matrix (§3) |
| **F. UI freeze / native teardown** | COVERED | `test_grpc_lifecycle.py` (stale-callback suppression, channel-not-closed-while-worker-stuck, closes-owned-client-even-when-replaced, repeated create/close), `test_pinger_lifecycle.py`, `test_terminal_reconnect_never_reconstructs_or_emits_navigation`, `test_restart_*` |
| **G. Telemetry burst/stale/out-of-order** | PARTIAL → completed | reused: `TestGroupedMultiStalePositionTreatedUnavailable`, `TestGroupedMultiPollingSameStaleSnapshotNeverRefreshesIt`, `TestGroupedMultiProductionSamplesFreshStaleInvalidAndPartial`, `TestSeparateApiAuthorityRequiresFreshStartPositions`. **NEW:** no-write-churn + out-of-order freshness (§3) |
| **H. Multi-drone partial failure** | COVERED | GROUPED: `TestGroupedMultiBarrierHoldsUntilAllArrive`, `TestGroupedMultiParticipantRejectDoesNotFailRun`, `TestGroupedMultiApiParticipantFailsafeInterruptsAndCancels`. SEPARATE: `TestSeparateOneFinishesOthersContinue`, `TestSeparateStaleParticipantBlocksOnlyCompletion` |
| **I. Leader/follower failure** | COVERED (takeover BLOCKED) | `TestSwarmLeaderFailsafeParityLeaderHaltsFollowerExcluded`, `TestSwarmLeaderFollowerFailsafeDoesNotInterruptRoute`, `TestDurableSwarmLeaderRecoveryDoesNotRestoreLeaderClaim`, `TestFollowerSendBoundaryBlocksTakeoverThenRejectsStaleSend`, `TestCoreRestartSwarmLeaderAndPayloadNeverStartCommands`. Follower operator-takeover = **DECISION REQUIRED** (§5) |
| **J. WAVE interruption** | COVERED | `TestWaveCompletedRunCallbackCannotAffectNewRun`, `TestWaveCancelledRunCallbackCannotAffectNewRun`, `TestWaveGroupOneTakeoffCannotAffectGroupTwoTakeoff`, `TestWaveStaleOldGroupLandedAndNextIgnored`, `TestWaveStaleTimeoutPhaseTokenCannotAffectNewerPhase`, `TestWaveCancelAndFailsafeAreTerminal` |
| **K. Payload interruption** | COVERED | `TestActionCancelThenNewRunRejectsOldCallback`, `TestActionCancelAndFailsafeDuringDwellRequireCleanup`, `TestActionCancelDuringReleaseAndCleanupFailureObservable`, `TestActionHoldFailureStillReleasesAllLikeLegacyFinish`, `TestActionServoFailureReleasesAllParticipantsThenAdvances`, `TestActionRunTokensIsolateHoldServoAndRelease` |
| **L. Manual takeover during dispatch** | COVERED | `TestOperatorTakeoverCancelsInFlightMissionSend`, `TestGroupedMultiApiInFlightSendCancelledByTakeover`, `TestGroupedMultiTakeoverAtTransitionEmitsNoNextGoto`, `TestSeparateApiOperatorTakeoverTerminal`, `TestSwarmLeaderLeaderTakeoverCancelsMission`, `TestOperatorTakeoverCancelsOwnedCoreRunBeforeHoldStop` |

---

## 2. Reused evidence (not re-implemented)

Families A, B, C, F, H, I, J, K, L were verified COVERED by existing tests and left
unchanged, per the "audit and reuse rather than duplicate" rule. See the matrix above
for the specific tests relied upon.

---

## 3. New deterministic scenarios added

Each declares Initial state → Injected failure → Expected Safe State → Commands that
MUST NOT happen. Full contract text is in each test's doc comment.

### E — semantic persistence corruption fails closed
`backend/internal/mission/persistence_test.go` →
**`TestDurableRecordFailsClosedOnSemanticCorruption`** (10 sub-cases).
Structurally-valid records with contradictory fields — mode/leader mismatch,
participants not matching plan, invalid authority scope, terminal-retains-authority,
recovery-required-not-INTERRUPTED, transition revision ahead of run, grouped WAIT with
non-zero scope, SEPARATE index out-of-range / incomplete, SEPARATE WAIT unknown scope.
- **Expected Safe State:** `ValidateDurableMissionRecord` errors, `RestoreDurableMission`
  refuses, no live run installed (Active=false, Authority=false). The API funnel to
  visible incompatible recovery with zero command is already proven by
  `TestCorruptPersistenceStartsVisibleIncompatibleRecoveryWithZeroCommands`.

### G — no persistence write-churn on inert telemetry
`backend/internal/api/mission_persistence_test.go` →
**`TestNoPersistenceWriteChurnWithoutMissionProgression`** (+ `countingDurableStore`).
- **Injected:** 10 far-from-waypoint telemetry batches, then one arrival.
- **Expected Safe State:** zero extra SQLite writes for the inert burst; exactly one
  write for the single real progression. **MUST NOT:** write churn on inert telemetry.

### G — out-of-order telemetry freshness
`backend/internal/mission/failure_injection_test.go` →
**`TestFailureInjectionOutOfOrderTelemetryDoesNotMoveFreshnessBackward`**.
- **Injected:** a fresh sample at T, then a later-arriving but OLDER observation.
- **Expected Safe State:** recorded position/timestamp stay at the newer sample; a
  genuinely fresh later sample still updates. **MUST NOT:** freshness regress to the
  older sample (which could seed a false centroid/arrival).

### D — GROUPED durable progression survives restart accurately
`backend/internal/api/mission_persistence_test.go` →
**`TestCoreRestartGroupedProgressionSurvivesAsAccurateRecovery`**.
- **Injected:** core-single run advances past WP0 to WP1, then Core restarts.
- **Expected Safe State:** recovery_required, Active=false, Authority=false,
  current_index preserved at 1, previous state RUNNING. **MUST NOT:** any GOTO/HOLD or
  guessed continuation on restart. (Mirrors the SEPARATE guarantee for the live mode.)

### D — recovery-clear DELETE boundary stays idle
`backend/internal/api/mission_persistence_test.go` →
**`TestRecoveryClearDeleteBoundaryStaysIdleAfterRestart`**.
- **Injected:** corrupt record → incompatible recovery → operator clear (deletes the
  record) → Core restart.
- **Expected Safe State:** IDLE, run_id 0, no recovery, no command. **MUST NOT:** the
  deleted mission reappearing as recovery or authority.

---

## 4. Bugs found / production fixes

- **Bugs found:** NONE. Every injected failure already reached a deterministic safe
  state; the new tests are additive evidence.
- **Production fixes:** NONE. No source behavior was changed for S11.

---

## 5. Intentionally blocked

> **Superseded 2026-08-30:** the SWARM follower/leader operator-takeover policy below
> was ratified and implemented PRE-FLIP in V3-S09-C. The blocked status recorded here
> was accurate at the time of this stage; see the S09-C section of the roadmap.

- **SWARM_LEADER follower operator-takeover policy — DECISION REQUIRED.** No test
  asserts an invented takeover semantic. Already-specified leader/stale-callback/split-
  ownership/no-auto-restore behavior is tested (family I); the undecided takeover branch
  is left explicitly unproven pending a product decision.
- **`rtl_after` — BLOCKED** (live product behavior undefined). Out of S11 scope; not
  invented.

---

## 6. Test results

```
cd backend
go test ./internal/mission -run TestDurableRecordFailsClosedOnSemanticCorruption -count=1                 # ok
go test ./internal/api -run 'TestNoPersistenceWriteChurn...|TestCoreRestartGroupedProgression...|TestRecoveryClearDeleteBoundary...' -count=1  # ok
go test ./internal/mission -run TestFailureInjectionOutOfOrderTelemetry... -count=1                        # ok
go test ./internal/mission ./internal/api ./internal/command ./internal/fleet ./internal/swarm ./internal/store -count=1  # all ok
go test ./... -count=1                                                                                     # all ok
go vet ./...                                                                                               # clean
```
```
cd frontend
python -m pytest tests/test_grpc_lifecycle.py tests/test_pinger_lifecycle.py tests/test_command_gateway.py tests/test_mission_shadow.py -q   # 57 passed
```
Repo root `git diff --check` → clean (only pre-existing CRLF warnings in untouched files).
Race detector NOT run; no HIL/FC/real-flight.

---

## 7. Authority gate

Only **`core-single`** and **`core-single-wait`** remain live. `core-grouped-multi`,
`core-separate`, `core-swarm-leader`, `core-wave`, `core-payload` remain OFF. S11
flips no authority. S09 remains PRE-FLIP (authority NOT flipped); S10 remains COMPLETE.
