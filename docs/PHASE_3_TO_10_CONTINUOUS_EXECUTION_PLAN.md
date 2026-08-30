# DroneGod_AI — Continuous Execution Plan: V1 Phase 3 → Phase 10

> **SUPERSEDED FOR PLANNING BY `docs/V3_MASTER_ROADMAP.md` (2026-08-28).**
> This file is retained as historical execution detail/evidence only. New status and next-step reporting must use V3 IDs (`V3-Sxx`, `V3-Hxx`, `V3-Rxx`) to avoid V1 Phase / V2 F-gate numbering ambiguity.

Date: 2026-08-27
Active project: `C:\Users\PC\Desktop\v2 swam\DroneGod_AI`
Legacy reference: `C:\Users\PC\Desktop\v2 swam\DroneGod` (**READ ONLY**)

> This document is the execution contract for continuing software architecture work while waiting for the physical Flight Controller (FC).
>
> **Important:** V1 **Phase 10 = SITL Endurance**. It is **not** Release Gate **F10 Controlled Real Flight**. Release F10 remains locked until the physical FC bench gate F9B is completed with real hardware evidence.

---

## 1. Continuous-Execution Rule

Work proceeds continuously from the current Phase 3 checkpoint through Phase 10 without asking for a new “continue” instruction after every phase.

A phase may advance only after its defined regression gate passes on the same frozen source revision/state.

Pause automatic progression only when one of these conditions is true:

1. a regression cannot be repaired without changing flight semantics;
2. a safety ambiguity requires a policy decision rather than an implementation repair;
3. a change would require real FC evidence to claim success;
4. a destructive migration would remove the legacy fallback before soak/evidence exists;
5. a test reveals possible dual flight authority or possible emergency-command suppression.

Normal compile/test failures, resource leaks, race conditions, flaky lifecycle tests, missing characterization tests, and implementation defects are **not** reasons to stop; repair them and rerun the gate.

---

## 2. Global Safety Invariants — Must Hold Through Every Phase

These invariants supersede optimization goals:

- Go Core remains authoritative for the currently migrated Core-mission scope.
- Python legacy mission execution remains authoritative only for explicitly non-migrated scopes.
- Two flight/navigation authorities must never command the same target concurrently.
- E-STOP / KILL / failsafe / operator takeover must never be weakened to gain performance.
- Telemetry presentation optimization must never throttle Core safety/mission telemetry ingestion.
- A UI restart must not silently start a new mission.
- A Core restart must never silently auto-resume a previous flight mission.
- New retry/dedup behavior must be observe-only before enforcement unless semantics are proven.
- Existing safety-positive failsafe suppression in `command.Service` must be retained.
- F9B stays `PENDING ACTUAL FC`; Release F10 stays `LOCKED` throughout this software plan.

Every phase must preserve the current no-dual-authority guarantees around:

- map GOTO
- Field Tablet GOTO / MOVE / MODE
- Selected Drone Card HOLD / LAND / RTL / ARM / DISARM
- Cockpit HOLD / LAND / RTL / GO ALT / ARM / TAKEOFF / MODE
- stale Python timers/callbacks
- Core Mission ↔ Go Swarm START / RETURN-LAND

---

# PHASE 3 — Telemetry Store / Read Model / Render Coalescing

## Goal

Separate telemetry ingestion from UI rendering so high telemetry rate does not force equivalent Qt repaint rate, while flight/business logic remains behaviorally unchanged.

## Current implementation direction

```text
Go telemetry stream
      ↓
Python _on_telemetry
      ├── raw _last_telem        ← legacy flight/business compatibility
      └── TelemetryStore         ← immutable presentation/read model
                  ↓
         render coalescing
                  ↓
 Fleet / Selected Card / Field Tablet / Map3D presentation
```

## Required work

- immutable primitive `TelemetryStore`
- shadow write for every accepted packet
- sampled raw-vs-store parity comparison
- store cleanup on disconnect/remove
- telemetry ingest-rate metric
- render skip/coalescing metric
- immediate render for discrete/operator-visible changes
- bounded coalescing for continuous position/speed/heading repaint
- low-risk presentation reads from Store
- Map3D presentation from Store
- keep Map2D legacy target/waypoint coupling on raw telemetry in this phase
- deterministic CoreClient/gRPC lifecycle shutdown

## Explicitly out of scope for Phase 3

Do not migrate these reads to the Store:

- waypoint progression
- GOTO decision logic
- collision-avoidance position source
- failsafe decisions
- servo flight/business logic
- mission authority decisions

## Exit gate — ✅ MET (2026-08-28)

- ✅ targeted TelemetryStore/lifecycle/Map tests PASS — `test_grpc_lifecycle` 8/8, `test_map3d` 53/53, Phase 3 telemetry suite 132/132
- ✅ mission/waypoint/safety adjacent tests PASS (covered by full frontend run)
- ✅ full frontend regression PASS — `python -m pytest -q tests` = **1031 passed / 0 failed** (deterministic; re-run green)
- ✅ full Go `go test ./...` PASS (Go untouched this phase)
- ✅ `go vet ./...` PASS
- ✅ no native teardown crash/resource leak in final suite — the ~28 % `0xC0000005` crash is fixed (`_StreamThread` run/stop serialization + join-before-channel-close); leaked-GroundStation timer/thread pollution closed by `tests/conftest.py` autouse teardown
- ✅ migration progress doc updated — see `ARCHITECTURE_MIGRATION_PROGRESS.md` top checkpoint

**PHASE 3 = DONE. PHASE 4 = NOT STARTED (held at user request — do not begin Command Gateway without explicit authorization).**

---

# PHASE 4 — Command Gateway (Pass-through First)

## Goal

Create one observable frontend command boundary without changing command semantics.

```text
UI / Presenter / Controller
          ↓
    CommandGateway
          ↓
      CoreClient
          ↓
        gRPC
```

## Stage 4A — Pure pass-through gateway

Gateway may add only:

- command name/family
- monotonic start/end timestamp
- duration
- operator/action context
- normalized success/error result
- correlation metadata
- structured command log/metric hooks

Gateway must **not** add:

- automatic retry
- command reordering
- suppression
- dedup enforcement
- new timeout semantics
- hidden safety policy

## Stage 4B — Migrate call sites incrementally

Order:

1. read-only/non-flight RPC wrappers if applicable
2. configuration/support commands
3. low-risk manual command wrappers
4. flight-critical command wrappers only with characterization tests
5. swarm/mission commands last

Keep existing `GroundStation` UI methods as compatibility wrappers during migration.

## Emergency-command hardening checkpoint

Before migration of E-STOP/KILL/HOLD/StopAll through a common gateway, measure and audit whether emergency paths can wait behind:

- `missionDispatchMu`
- normal FC ACK waits
- swarm/mission ownership locks
- reconnect/teardown locks

If emergency latency can be serialized behind normal command ACK wait, repair priority/lock scope without bypassing safety checks.

## Exit gate

- pass-through behavior tests prove exact call order/arguments
- no implicit retry exists
- no failsafe or authority guard bypass
- normalized errors preserve existing user-visible semantics
- emergency contention tests exist
- full frontend + Go regression green

---

# PHASE 5 — Operation ID / Command ID / Correlation / Observe-First Dedup

## Goal

Make every operator action traceable end-to-end and make duplicate behavior measurable before enforcing suppression.

## Identity model

```text
operation_run_id = one operator/mission operation
command_id       = one concrete command attempt
sequence         = order within the operation where needed
request_id       = transport/Core idempotency identity where supported
```

## Stage 5.1 — Correlation only

- generate/carry IDs
- log UI → Gateway → Core → audit/result
- no behavior change

## Stage 5.2 — Duplicate observation

Core/Gateway records duplicate candidates caused by:

- double click
- stale callback
- timeout + operator retry
- reconnect
- repeated web/tablet action

No rejection yet unless current Core semantics already enforce it.

## Stage 5.3 — Command-class enforcement

Classify commands before dedup enforcement:

- idempotent
- non-idempotent
- replace-current
- cancel-current
- emergency/takeover

Enforce only per proven class with tests.

## Exit gate

- duplicate command tests
- timeout/retry tests
- stale-run callback tests
- reconnect identity tests
- audit trace from frontend action to Core result
- no new duplicate flight command introduced
- full regression green

---

# PHASE 6 — Go Mission Engine Expansion, One Scope at a Time

## Goal

Continue reducing Python mission authority, but never perform a bulk cutover.

The current migrated Core scope remains the verified starting baseline.

## Migration order

### 6A — multi-drone GROUPED

Shadow/frozen-plan parity → targeted Core authority → SITL soak → retain legacy fallback until verified.

### 6B — SEPARATE routes

Per-drone index/arrival/wait state must be explicit in Core.

### 6C — SWARM/leader-coupled mission behavior

Must preserve mutual exclusion with Go formation/RETURN and current no-dual-authority rules.

### 6D — WAVE orchestration

Move group transition state, cancellation and safety interrupt ownership to Core only after shadow parity.

### 6E — waypoint payload / Servo actions

Must explicitly define acknowledgement, cancellation and stale-action semantics before authority cutover.

## Mandatory migration pattern for every sub-scope

```text
Characterize legacy
→ Shadow only
→ Compare legacy vs Core state
→ Authority gate off by default
→ Core authority in SITL
→ Failure injection
→ Soak
→ only then consider removing legacy executor for that scope
```

## Exit gate

For each migrated scope:

- Python stale callback has no authority
- Core failsafe overrides mission
- operator takeover is terminal-first
- restart/rebind deterministic
- no silent auto-resume
- legacy behavior parity documented or intentional deviation recorded
- full regression green

Phase 6 may be marked complete only when the selected software scope for this migration cycle is fully migrated or explicitly documented as deferred behind hardware evidence.

---

# PHASE 7 — Mission Persistence / Safe Recovery

## Goal

Persist enough mission state for deterministic recovery **without automatic flight resume**.

## Persist

- run_id
- immutable mission/plan snapshot
- current state
- current waypoint/group/index
- participants
- last transition
- created_at / updated_at
- terminal/interruption reason

## Never persist as executable authority

- Qt timers
- Python callbacks
- QObject references
- raw transient UI state
- stale client request handles

## Recovery policy

UI restart:

```text
Core still running
→ UI queries current state
→ UI reconstructs presentation
→ no StartMission replay
```

Core restart:

```text
load persisted non-terminal run
→ RECOVERY_REQUIRED / INTERRUPTED
→ no flight command
→ operator explicitly decides next action
```

## Exit gate

- crash/restart persistence tests
- corrupted/partial state handling
- atomic transaction/write tests
- schema/version handling
- no silent auto-resume proof
- full regression green

---

# PHASE 8 — Core Service + Restartable UI Lifecycle

## Goal

Make process lifecycle operationally reliable:

```text
UI lifecycle ≠ Core lifecycle
```

## Required work

- Core health/status contract
- frontend reconnect backoff
- UI startup state sync
- explicit stale-session detection
- Core/UI version/build compatibility check
- clear unavailable/incompatible UI state
- deterministic shutdown of gRPC channels/threads
- separate Core and UI lifecycle logs
- supervisor starts monitor-only if introduced

A Core process restart policy must not imply mission resume.

## Exit gate

- kill UI → Core remains alive
- restart UI → reconnect + reconstruct state
- incompatible Core/UI rejected clearly
- Core restart → safe interrupted/recovery state
- repeated open/close lifecycle stress without thread/channel growth
- full regression green

---

# PHASE 9 — Failure Injection

## Goal

Turn known architectural assumptions into executable safety evidence.

Every scenario must define **Expected Safe State before the test runs**.

## UI failure scenarios

- temporary Qt main-thread freeze in harness
- forced UI close/kill
- UI restart during Core mission
- telemetry flood/burst
- Map/WebEngine failure where testable

## RPC/network scenarios

- delayed RPC response
- dropped connection
- duplicate request
- timeout then late response
- reconnect during mission
- stale response after cancel

## Core scenarios

- graceful Core stop
- forced process exit in SITL/test harness
- restart with persisted active/non-terminal run

## Telemetry scenarios

- stale telemetry
- burst telemetry
- one drone stale while others healthy
- out-of-order snapshot where the transport/test path can simulate it

## Mission scenarios

- cancel during WAIT
- safety interrupt at state boundary
- leader failure
- follower failure
- WAVE group transition interruption
- manual takeover during mission dispatch

## Exit gate

- every scenario has Expected Safe State
- no unexpected command after terminal cancel/interrupt
- no duplicate authority
- failure result/evidence recorded
- full regression green

---

# PHASE 10 — SITL Endurance / Performance / Leak Detection

## Goal

Find resource growth and timing/race problems not visible in unit/integration tests.

## Endurance ladder

Run incrementally; do not jump directly to 24 h:

```text
30 minutes
→ 2 hours
→ 6 hours
→ 12 hours
→ 24 hours only if environment/time permits and earlier gates are stable
```

A completed 30-minute and 2-hour gate is enough to establish Phase-10 software readiness for continued endurance work; longer runs remain progressive evidence rather than a prerequisite for physical FC bench preparation unless a leak is observed.

## Metrics

- Python RSS / private bytes
- Go RSS
- Python thread count
- gRPC/background thread count
- Go goroutine count where exposed
- UI heartbeat/stall count
- telemetry ingest rate
- telemetry age
- render rate / render skip percentage
- RPC latency/error rate
- reconnect count
- mission state transition count
- log growth

## Workload profiles

1. idle connected telemetry
2. 5-drone telemetry burst/profile
3. 10-drone telemetry burst/profile
4. repeated UI selection/map/Field Tablet presentation updates
5. repeated mission start/cancel in SITL-safe scope
6. reconnect churn in harness

## Pass criteria

Establish thresholds from measured baseline, not guessed absolute numbers.

Mandatory qualitative criteria:

- no unbounded memory growth trend
- no unbounded thread/goroutine growth
- no increasing telemetry age/backlog trend
- no cumulative render queue growth
- no growing native gRPC channel/thread count after UI lifecycle churn
- no mission/command authority regression

## Phase 10 output

Produce an endurance evidence report containing:

- environment/build identity
- scenario duration
- before/after/min/max metrics
- observed slopes/trends
- errors/reconnects
- pass/fail verdict
- unresolved risks

Phase 10 completion is **software endurance evidence only**.

It does not unlock real-flight Release F10.

---

# 11. Verification Gate Used Between Phases

Unless a phase has stricter requirements, use this gate before advancing:

```text
1. targeted tests for changed subsystem
2. adjacent safety/mission/command tests
3. full frontend regression
4. go test ./...
5. go vet ./...
6. git diff --check
7. update migration/evidence docs
```

If source changes while a long-running suite is executing, that suite becomes stale evidence and must be rerun on the frozen source.

Native crashes are not ignored as flaky until lifecycle/resource causes have been investigated and either repaired or reproduced as environment-only behavior with evidence.

---

# 12. Evidence / Documentation Rules

At each phase checkpoint record:

- source files changed
- tests added/modified
- targeted result
- full frontend result
- Go result
- vet result
- known limitations
- intentional legacy deviations
- exact next phase

Primary progress file:

`docs/ARCHITECTURE_MIGRATION_PROGRESS.md`

This file remains the execution contract:

`docs/PHASE_3_TO_10_CONTINUOUS_EXECUTION_PLAN.md`

---

# 13. Hardware / Release Boundary

This plan deliberately stops before claiming physical-flight readiness.

```text
V1 Phase 10 software endurance
        ↓
F9B actual physical FC bench
        ↓
real ACK/timing/failsafe/reconnect evidence
        ↓
only then evaluate Release F10 controlled real flight
```

Until real FC evidence exists:

- F9B = `PENDING ACTUAL FC`
- Release F10 = `LOCKED`

---

# 14. Current Execution Status

At plan creation time:

- Phase 3 implementation is active.
- TelemetryStore targeted/lifecycle tests after gRPC ownership repair: `95 passed`.
- Mission/waypoint/safety adjacent checkpoint before the lifecycle-only repair: `399 passed`.
- Full Go regression: PASS.
- `go vet ./...`: PASS.
- Full frontend final rerun after lifecycle repair is currently in progress.

Automatic next action after Phase 3 gate is green:

**Begin Phase 4 — Command Gateway pass-through + emergency latency characterization.**
