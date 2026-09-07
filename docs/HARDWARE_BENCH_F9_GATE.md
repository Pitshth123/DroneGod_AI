# F9 Hardware Bench Gate

Date prepared: 2026-08-27
Status: **F9A PREPARATION DONE / F9B ACTUAL-FC IN PROGRESS — H02 5.1–5.3 PASS, 5.4+ PENDING (2026-09-03)**

Current actual-FC checkpoint and measured evidence: `docs/H02_ACTUAL_FC_BENCH_PROGRESS_20260903.md`. This is not an F9/H02 exit PASS; waypoint/WAIT/Cancel/Core-loss/preemption/takeover evidence remains pending.

F9 is intentionally split so software preparation does not wait for the real FC. F9A prepares and validates the bench tooling; F9B is the mandatory execution on the actual flight controller/airframe. SITL never substitutes for F9B.

F9B mission authority uses the existing `hil` profile behind a second physical-bench interlock. The server accepts exact `core-single` / `core-single-wait` tokens in HIL only when `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH` is also present. `production` remains unable to enable this rollout gate. `scripts/run_f9b_hil_core.bat` refuses to start unless both that confirmation and an explicit `SWARMGOD_HOME_LOC` are provided.

## F9A — software-preparable bench tooling (DONE)

### 1. Read-only parameter snapshot/audit

Tool: `scripts/bench_param_audit.py`

- File mode is completely offline/read-only.
- `--endpoint` mode uses MAVLink parameter request messages only; it never sends `PARAM_SET`.
- Full snapshot is preferred over individual-name reads because parameter responses can be dropped/version-dependent.
- Policy supports current/legacy ArduPilot naming (`MAV_GCS_SYSID` / `SYSID_MYGCS`, `ARMING_SKIPCHK` / `ARMING_CHECK`).
- Baseline rejects clearly disabled critical protections but does **not** invent the team's exact failsafe action.

Policy: `docs/f9_expected_params.json`

Required baseline evidence:

- Core GCS system id matches the FC's accepted GCS id.
- GCS/Core disappearance failsafe is not disabled.
- pre-arm checks are not globally bypassed.
- critical battery has an onboard action.
- FS_OPTIONS / low-battery / fence configuration is captured for review.

### 2. Telemetry timing recorder

Tool: `backend/cmd/benchprobe`

Default behavior is observation-only. Optional `--connect-port` establishes Core↔FC transport but never Arm/Takeoff/Goto/Hold/Land or writes parameters.

Evidence includes:

- sample count and effective Hz
- receive interval p50/p95/max
- telemetry source-age p50/p95/max
- link quality range / packet drop / RSSI availability
- configured LinkWarn/LinkLost evidence values
- fail-closed timing-margin boolean
- current Core mission state/run/authority snapshot

F9A SITL validation (no flight): 101 samples / 10.00 Hz, interval p95 101 ms, max 107 ms, source-age p95 1 ms, max 3 ms, mission IDLE.

### 3. Guarded COMMAND_ACK timing harness

Tool: `backend/cmd/benchack`

- Default = observe only; sends no command.
- Execution requires both `--execute` and exact confirmation `--confirm PROPS-REMOVED-BENCH`.
- Refuses execution while armed.
- Refuses if initial mode is UNKNOWN and cannot be restored.
- Only performs disarmed SetMode→ACK timing and restores the original mode.
- Static regression test forbids Arm/Disarm/Kill/Takeoff/Land/RTL/Hold/Goto/ChangeAlt/RcMove RPCs in this tool.

F9A SITL harness validation while disarmed: STABILIZE→GUIDED and restore both ACKed in ~5 ms; no arm/takeoff/navigation command was issued. SITL timing is harness validation only, not hardware timing evidence.

### 4. Evidence merger

Tool: `scripts/f9_evidence_report.py`

Combines parameter audit JSON + telemetry timing JSON into one conservative report. It can mark **F9A preparable evidence** PASS/FAIL but always leaves **F9 overall PENDING F9B**. Hardware-only evidence can never be auto-filled by this report.

Self-tests:

- `scripts/bench_param_audit_selftest.py`
- `scripts/f9_evidence_report_selftest.py`
- `go test ./internal/bench ./cmd/benchprobe ./cmd/benchack`

### 5. F9A live SITL characterization

A read-only snapshot of current ArduCopter SITL found:

- `MAV_GCS_SYSID=255`
- `ARMING_SKIPCHK=0`
- `FS_GCS_ENABLE=0` **FAIL**
- `BATT_FS_CRT_ACT=0` **FAIL**
- `FS_OPTIONS=16`
- `FENCE_ENABLE=0`

This is expected to fail the F9 policy and matches the earlier Core-crash observation: the simulated FC continued the last GUIDED target after Core disappeared. The tooling correctly refuses to turn good telemetry timing into a false safety PASS.

## F9B — actual FC/airframe bench verification (PENDING FC)

### Entry conditions

- F8 single-drone GROUPED+WAIT software/SITL gate is green.
- F9A tools/tests are green.
- No WAVE/payload claim unless F6B is separately migrated and tested.
- Propellers removed or otherwise made physically safe according to the team's bench procedure.
- Actual FC parameter/config snapshot is captured **before changing anything**.
- Manual/safety takeover path is available and understood by the operator.

### Required actual-bench evidence

1. **Parameter/config snapshot**
   - Capture actual FC parameters with `bench_param_audit.py`.
   - Review exact GCS-loss action and FS_OPTIONS semantics against the team's intended field policy.
   - Do not change parameters until the original snapshot is archived.

2. **Core ↔ FC COMMAND_ACK timing**
   - Run `benchack` observation-only first.
   - With the bench physically safe and vehicle disarmed, run the guarded mode ACK test.
   - Record set/restore ACK time and reject/timeout behavior.

3. **Telemetry timing**
   - Run `benchprobe` for a representative duration over the intended real transport.
   - Confirm LinkWarnSec/LinkLostSec leave sensible margin and do not false-trigger.

4. **Core-owned waypoint**
   - Execute only the approved single-drone GROUPED scope.
   - Confirm one command per waypoint, no Python mission GOTO, and real arrival timing.

5. **Core-owned WAIT/HOLD**
   - Enter WAIT without Python/Qt authority.
   - Close cockpit while WAIT is active; Core retains run/WAIT.
   - Reopen/query same run; no duplicate Start/GOTO/HOLD.

6. **Cancel**
   - Cancel in transit and near transition boundaries.
   - Terminal state must latch; later telemetry cannot resume progression.

7. **Core/GCS loss and onboard FC failsafe — mandatory blocker**
   - Verify the actual FC's intended onboard GCS/Core-loss parameters and observed behavior.
   - Stop Core in a controlled bench-safe state.
   - FC behavior must match the team's approved safety policy.
   - Restart Core: mission must be IDLE/no auto-resume.

8. **Battery/link preemption**
   - Induce bench-safe alarm conditions where feasible.
   - Fleet failsafe remains sole flight-action owner; mission becomes INTERRUPTED.
   - No later mission GOTO/HOLD after interruption.

9. **Manual/safety takeover**
   - Verify the actual operator STOP/E-STOP/manual takeover procedure.
   - Mission authority yields and does not auto-resume.

## Suggested F9B evidence commands

Run from the project root/backend as appropriate; use actual endpoint/core values selected for the bench. Before starting the HIL Core, the operator/session must explicitly provide `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH` and the real bench `SWARMGOD_HOME_LOC`; the launcher does not invent either value.

```text
scripts\run_f9b_hil_core.bat

python scripts/bench_param_audit.py --endpoint <mavlink-endpoint> --policy docs/f9_expected_params.json --snapshot-out <snapshot.txt> --json-out <params.json>

go run ./cmd/benchprobe -core <core> -drone 1 -duration 60s -out <telemetry.json>

go run ./cmd/benchack -core <core> -drone 1

go run ./cmd/benchack -core <core> -drone 1 -execute -confirm PROPS-REMOVED-BENCH -out <ack.json>

python scripts/f9_evidence_report.py --params-json <params.json> --telemetry-json <telemetry.json> --out <report.md>
```

## F9 exit criteria

F9 PASS requires **actual FC** evidence for all of the following:

- Core command/ACK timing verified.
- Telemetry timing verified.
- Waypoint and WAIT operate without Python authority.
- UI disconnect/reconnect creates no duplicate command.
- Cancel remains terminal.
- Actual onboard FC failsafe behavior matches the intended safety policy when Core/GCS disappears.
- Core restart does not resume the prior mission.
- Manual/safety takeover path verified.

Until F9B passes: **NO CONTROLLED REAL-FLIGHT GATE.**
