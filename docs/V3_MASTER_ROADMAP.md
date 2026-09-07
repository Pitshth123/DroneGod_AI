# DroneGod_AI — V3 Master Roadmap

Date created: 2026-08-28
Last status sync: **2026-09-06**
Active project: `C:\Users\staff\OneDrive\Desktop\DroneNew`
Legacy reference: `C:\Users\PC\Desktop\v2 swam\DroneGod` (**READ ONLY**)

> **V3 is now the canonical roadmap.**
>
> V1 and V2 are preserved as implementation/evidence history, but new planning, status reporting, and next-step decisions should use the V3 IDs in this document only.
>
> This removes the old ambiguity between names such as `V1 Phase 8`, `V2 F8`, `F9A/F9B`, and `F10`.

---

# 1. V3 Naming Rule

From this point forward:

- Software architecture work uses `V3-Sxx` (Stage).
- Physical hardware verification uses `V3-Hxx` (Hardware Gate).
- Real-flight release uses `V3-Rxx` (Release Gate).
- Old V1/V2 phase/F numbers are evidence/history only.

Status vocabulary:

- `DONE` — implemented and evidence exists for the stated scope.
- `PARTIAL` — useful implementation/evidence exists, but the V3 stage is not complete system-wide.
- `PENDING` — planned, not yet implemented as a V3 stage.
- `BLOCKED` — cannot be completed until a known blocker is resolved.
- `LOCKED` — intentionally prohibited from execution.

Important scope rule:

`DONE` never means "all drone mission modes are migrated" unless the row explicitly says so. Several V2 accomplishments are DONE only for the guarded single-drone GROUPED / WAIT scope.

---

# 2. One-Page Master Status

| V3 ID | Area | Status | What is already done | What remains |
|---|---|---:|---|---|
| **V3-S01** | Baseline + Responsibility/Ownership Map | **DONE** | Legacy behavior frozen/read-only; responsibility map; regression baselines; Go/Python ownership characterized | Keep as reference only |
| **V3-S02** | UI Observability + Presentation Extraction | **DONE** | HealthMonitor, Summary Presenter, Fleet Presenter, Map presentation adapter, UI heartbeat/metrics | Optional cosmetic/controller extraction only |
| **V3-S03** | Mission Core Model + RPC Foundation | **DONE** | Go Mission Engine, MissionPlan/state machine, arrival observation, run_id, Start/Cancel/Query RPC, shadow path | No foundation work remains |
| **V3-S04** | Guarded Go Mission Authority | **DONE (limited scope)** | Core authority for single-drone GROUPED and WAIT; reconnect/rebind; fail-closed lost Start; failsafe interrupt; SITL evidence | Scope expansion belongs to V3-S09 |
| **V3-S05** | No-Dual-Authority + Legacy Parity Hardening | **DONE** | Manual GOTO/RC/MODE/TAKEOFF guards; operator takeover; Mission↔Swarm exclusion; stale callbacks blocked; legacy unsupported modes retained | Preserve these invariants in every later stage |
| **V3-S06** | Telemetry Read Model + UI/gRPC Lifecycle | **DONE** | TelemetryStore, shadow parity, render coalescing, low-risk readers, deterministic Qt/gRPC stream shutdown; lifecycle stress; full frontend 1031/1031 PASS | Preserve as completed foundation; no further S06 work required |
| **V3-S07** | Command Gateway + Emergency Priority | **DONE** | Stable per-target claims; atomic cancellation/final-write guards; explicit takeover priority; typed Takeoff rollback ownership; long-running `request_id` replay | Preserve invariants during later stages; no S07 implementation remains |
| **V3-S08** | Command Identity + Correlation + Dedup | **DONE** | Six-class `CommandLedger`; semantic fingerprints; neutral suppressed result; application-wide mutating Gateway coverage including Preflight and mission mutation RPCs; request/result-linked Core audit | Preserve classification, correlation isolation, and coverage inventory |
| **V3-S09** | Mission Authority Expansion | **FINAL REVIEW PASS — 0 CRITICAL / 0 HIGH — READY TO FREEZE (PRE-FLIP)** | A–F pre-flip implementation exists and the current source passed fresh independent re-review. Fleet failsafe final-write ownership now covers Mission GOTO/HOLD, steady-state Formation, all form-up leader/follower writes, generic automatic Return RTL, and SWARM_RETURN GOTO/LAND/fallback RTL while keeping ACK waits outside `fsMu`. Typed-nil coordinators fail closed; accepted-target ordering, Return outcomes, current-membership succession, no-auto-rejoin, and command-inert restart remain verified. No new live token; only `core-single` / `core-single-wait` remain live. | **Record/complete the S09 PRE-FLIP software baseline freeze as a separate administrative step.** Freeze does **not** authorize authority flip. H02 base validation is already in progress under the existing narrow authority; after H02 base PASS, validate and flip deferred S09 scopes one at a time through the staged SITL/failure/soak/hardware/controlled-flight plan. |
| **V3-S10** | Persistence + Restartability | **COMPLETE (verified 2026-08-29)** | Durable SQLite mission persistence with monotonic run_id / operation_id history; an unfinished Core mission survives restart as command-inert RECOVERY_REQUIRED / INTERRUPTED evidence (Active=false, Authority=false, zero flight command); corrupt/truncated/unknown-schema fail safe; persistence faults are operator-visible; reconnect is presentation-only. See [V3_S10_PERSISTENCE_RESTARTABILITY.md](V3_S10_PERSISTENCE_RESTARTABILITY.md) | — |
| **V3-S11** | Failure Injection Expansion | **COMPLETE (verified 2026-08-29)** | Architecture-wide failure families audited as already-covered by the existing suite; deterministic gaps closed (semantic persistence corruption fail-closed, no write-churn on inert telemetry, GROUPED durable progression across restart, recovery-clear DELETE boundary, out-of-order telemetry freshness). Every new scenario declares an Expected Safe State; no production bug found. (At S11 time the SWARM follower-takeover policy had not yet been ratified; it was later ratified and implemented PRE-FLIP in S09-C on 2026-08-30.) See [V3_S11_FAILURE_INJECTION_EXPANSION.md](V3_S11_FAILURE_INJECTION_EXPANSION.md) | — |
| **V3-S12** | SITL Endurance + Performance | **CURRENT OPERATIONAL GATE COMPLETE** | Canonical duration/scenario JSON harness plus actual 6h idle-connected PASS, 15m reconnect-churn PASS, and 15m client-lifecycle PASS; 216,002 telemetry / 723 RPC / 0 RPC errors / 0 stream errors / 0 UI stalls on the 6h run | 12h/24h, long 5/10-drone and prepared-airborne Start/Cancel campaigns are **DEFERRED EXTENDED VALIDATION**, not current blockers; watch the one transient non-blocking `SQLITE_BUSY` observation |
| **V3-H01** | Hardware Bench Preparation | **DONE** | benchprobe, benchack, parameter audit, evidence merger, HIL interlock | Maintain tooling only |
| **V3-H02** | Actual FC/Airframe Bench | **IN PROGRESS — 5.1–5.3 PASS / 5.4–5.9 PENDING** | Actual FC `192.168.9.184:5760` reached; parameter baseline, real telemetry timing and guarded COMMAND_ACK evidence captured; operator-visible HIL path subsequently reached GUIDED → ARM → DISARM; session token issuance completed without recording the secret value | Capture post-token SYSTEM TEST evidence, then prove Core-owned waypoint, WAIT/HOLD, Cancel, controlled Core/GCS-loss failsafe, battery/link preemption, manual/STOP/E-STOP takeover and restart/no-auto-resume. No real-flight clearance is implied. |
| **V3-R01** | Controlled Real Flight Release | **LOCKED** | None claimed | Unlock only after V3-H02 PASS and explicit release review |

---

# 3. Current Position — H02 Actual-FC Bench Is the Active Gate

**Current status stamp (2026-09-06):**

- V3-S01–S08: **DONE**.
- V3-S09: **FINAL REVIEW PASS — 0 Critical / 0 High — READY FOR PRE-FLIP FREEZE; authority not flipped**. No separate evidence currently proves that the administrative freeze itself was completed.
- V3-S10: **COMPLETE**.
- V3-S11: **COMPLETE**.
- V3-S12: **CURRENT OPERATIONAL GATE COMPLETE** with 6h + reconnect + client-lifecycle evidence.
- V3-H01: **DONE**.
- V3-H02: **IN PROGRESS**. 5.1–5.3 are evidenced PASS; operator-visible HIL later reached GUIDED/ARM/DISARM; 5.4–5.9 remain pending.
- V3-R01: **LOCKED**.

The active project effort is therefore H02 baseline completion, not reopening S07/S08/S10/S11/S12. Advanced S09 live authority scopes remain OFF and must be promoted one at a time only after the H02 base gate and their own validation ladders.

## Historical closure detail — V3-S07 + V3-S08

**Status: DONE (verified 2026-08-29). V3-S09 FRESH INDEPENDENT PRE-FLIP RE-REVIEW PASS — 0 CRITICAL / 0 HIGH — READY TO FREEZE.**

The independent source review, repair, and frozen-source verification of S07/S08
is complete. S06 remains a completed foundation. V3-S09 PRE-FLIP implementation
now exists for A–F. A fresh independent current-source re-review verified the earlier
Mission GOTO/HOLD, steady-state Formation, and typed-nil repairs and independently
closed two later Critical gaps: form-up now composes the per-aircraft fleet failsafe
guard for the leader pin plus every follower transit/horizontal/final-slot write, and
generic SINGLE/GROUPED post-mission RTL now composes the same final-write guard without
replacing its Return lease guard. `fleet.sendCmd` exits the guard before waiting for
COMMAND_ACK. SWARM_RETURN and accepted-target takeover fixes remain intact; Return
outcomes, current-membership succession, no-auto-rejoin, restart command-inert behavior,
and the narrow live-authority gate were rechecked. The independent verdict is
`FINAL REVIEW PASS` with **0 Critical / 0 High**. S09 is ready for the separate
administrative PRE-FLIP freeze, but is not marked frozen here and no authority flip is
authorized.

### Current closure evidence

- exact `old LAND [D1,D2] → STOP ALL D2 → old LAND D2 refused` behavior test
- equivalent RTL/HOLD/DISARM/ChangeAlt/Equalize and overlapping-target tests
- atomic command cancellation/final-transport-write guard
- Takeoff claims established before read-only precheck, preventing stale revival
- typed claim ownership for Takeoff preemption and rollback (no message parsing)
- same `request_id` waits beyond the former ten-second window and executes once
- protobuf replay uses `proto.Clone`
- all GroundStation mutation paths, including Preflight and mission Start/Cancel,
  route through the Gateway; read-only state/streams/lifecycle remain outside
- Gateway correlation headers are context-isolated and stale reserved headers are
  stripped/replaced; Core correlation remains observe-only and after auth
- final gates: targeted frontend 85 passed; targeted backend passed; full
  frontend 1077 passed in 40:53; full Go tests and `go vet` exited successfully
- fresh S09 re-review: form-up and generic automatic Return final-write regressions
  PASS ×10; prior failsafe/takeover/Return/succession/typed-nil blockers PASS ×10;
  targeted six-package gate, restart/live gate, full `go test ./... -count=1`,
  `go vet ./...`, and `git diff --check` PASS on the current source

### Completed S06 foundation retained

### Already implemented

Frontend-only presentation architecture:

```text
Go telemetry
    ↓
GroundStation._on_telemetry
    ├── raw _last_telem     → flight/business compatibility
    └── TelemetryStore      → immutable presentation snapshot
              ↓
       TelemetryRenderGate
              ↓
Fleet / Selected Card / Field Tablet / Map3D
```

Completed implementation includes:

- immutable primitive `TelemetryStore`
- shadow write for every accepted packet
- sampled raw/store parity checks
- ingest counters/rate
- disconnect/forget cleanup
- presentation-only render coalescing
- immediate repaint for discrete visible state changes
- Fleet card read-model migration
- Selected Drone card migration
- Field Tablet migration
- Map3D presentation migration
- online-count repaint cache
- health metrics for ingest/render/skip/shadow mismatch
- raw telemetry preserved for flight/business paths

### Safety boundary intentionally preserved

These remain on raw telemetry and must **not** be moved merely to finish S06:

- `_fleet_positions()`
- `_collision_positions()`
- waypoint progression / `_wp_advance*`
- `_on_target_reached`
- Map2D `updateDrone()` timing
- GOTO business logic
- `_auto_guided_after_land`
- servo sync/prime
- `_last_alt`
- `_home_pos`
- failsafe decisions
- preflight/takeoff decisions

### Completion evidence

The earlier Windows native `access violation` was traced to a `run()`/`stop()` race in the telemetry/event gRPC streaming QThreads. The repair introduced a shared `_StreamThread` lifecycle with serialized stream-open/stop state, idempotent cancellation, deterministic join, and GroundStation shutdown ordering that closes the owned gRPC channel only after both stream workers have terminated.

Verification recorded in the migration checkpoint:

- lifecycle regression `test_grpc_lifecycle`: **8/8 PASS**
- repeated GroundStation create/close stress: **50 cycles in one Python process PASS**
- Map3D: **53/53 PASS**
- Phase-3 telemetry/read-model integration set: **132/132 PASS**
- full frontend: **1031/1031 PASS**, repeated green
- backend `go test ./...`: **PASS**
- backend `go vet ./...`: **PASS**

The TelemetryStore safety boundary remains unchanged: presentation readers use the immutable store, while flight/business logic continues to use raw telemetry where required.

The S06 evidence above is historical completed-foundation evidence, not the
current work item.

---

# 4. V3-S01 — Baseline + Responsibility / Ownership Map

**Status: DONE**

Combined from early V1 baseline work and V2 F0/F1 audit/contract work.

Completed:

- legacy `DroneGod` frozen as read-only behavior reference
- ownership/responsibility map between Python UI and Go Core
- mission boundary characterization
- safety/failsafe command path characterization
- existing UI/mission behavior tests preserved
- legacy parity document established
- Mission Core cutover contract established

No new implementation should be added to S01. Future stages must consult this evidence when behavior differs.

---

# 5. V3-S02 — UI Observability + Presentation Extraction

**Status: DONE**

Combined from V1 Phase 1 and Phase 2A/2B/2C.

Completed:

- HealthMonitor / UI heartbeat
- observe-only stall detection
- Summary Presenter
- Fleet Presenter
- Map presentation adapter
- compatibility boundaries retained around GroundStation

Remaining work such as cosmetic cleanup or more presenters is optional and should not block safety architecture.

---

# 6. V3-S03 — Mission Core Model + RPC Foundation

**Status: DONE**

Combined mainly from V2 F2/F3.

Completed:

- `backend/internal/mission` domain model
- MissionPlan / state machine
- GROUPED/SEPARATE/SWARM_LEADER shadow semantics
- WAIT model
- Core-generated run identity
- arrival observation in Go
- StartMission / CancelMission / GetMissionState RPC
- operation_id idempotency for mission start
- stale-safe cancel/query behavior
- telemetry-driven mission observation

This stage created the foundation only. Authority scope is handled by later stages.

---

# 7. V3-S04 — Guarded Go Mission Authority

**Status: DONE FOR GUARDED SCOPE**

Combined from V2 F4/F5/F6/F7/F8 work.

Current supported authoritative scope:

### `core-single`

- GROUPED only
- exactly one participant
- route required
- no WAIT
- no payload action
- no WAVE
- no `rtl_after`

### `core-single-wait`

- same single-drone GROUPED boundary
- WAIT allowed
- no payload action
- no WAVE
- no `rtl_after`

Completed for that scope:

- Go Core issues mission GOTO through command.Service/safety path
- Go owns arrival/progression
- Go owns WAIT/HOLD state
- Python suppresses mission GOTO while Core authority is active
- browser target callback cannot advance Core-owned run
- lost/ambiguous Start reply is fail-closed + Query recovery
- UI restart/rebind reconstructs frozen Core state without duplicate Start
- battery/link ALARM interrupts mission state while fleet failsafe owns flight action
- Core restart never silently resumes mission
- repeated SITL evidence exists

This does **not** mean multi-drone/WAVE/etc. are migrated. Those are V3-S09.

---

# 8. V3-S05 — No-Dual-Authority + Legacy Parity Hardening

**Status: DONE**

This is the V2 safety hardening that must become a permanent V3 invariant.

Completed:

- manual map/tablet GOTO blocked during Core mission ownership
- tablet/manual RC movement blocked during Core mission ownership
- stale queued GOTO/RC repeat suppressed
- manual MODE/ARM/TAKEOFF paths guarded
- HOLD/LAND/RTL/DISARM operator takeover behavior
- selected-card and cockpit paths aligned
- Core Mission ↔ Go Swarm START/RETURN mutual exclusion
- explicit profile/authority activation gate hardened
- legacy unsupported plans remain Python-owned only after Core mission slot is proven idle
- WAVE legacy entrypoint protected by Core-slot-idle check
- safety-positive GOTO/HOLD failsafe rejection retained

These rules must not be rewritten inside the Command Gateway. The Gateway must call through them, not replace them.

---

# 9. V3-S07 — Command Gateway + Emergency Priority

**Status: DONE (verified 2026-08-29).**

This is the old V1 Phase 4 plus the emergency-contention hardening discovered after V2.

Goal:

```text
Cockpit / Map / Tablet / Quick Action
                ↓
         CommandGateway
                ↓
           CoreClient
                ↓
              Go API
                ↓
     existing V2 authority/safety rules
```

## Required work

### S07-A — Pass-through Gateway

Add one frontend command boundary that records:

- command family/name
- source (`cockpit`, `map`, `tablet`, etc.)
- targets
- monotonic start/end
- duration
- normalized success/error
- correlation metadata

It must initially change **no command semantics**.

No automatic retry, suppression, reordering, or new policy in the first cut.

### S07-B — Incremental call-site migration

Move CoreClient command calls behind the Gateway gradually with characterization tests.

### S07-C — Emergency priority hardening

Audit/measure whether E-STOP/KILL/HOLD/LAND/RTL/StopAll can wait behind normal command or mission dispatch locks/ACK waits.

Known concern to verify:

- some V2 API paths serialize through `missionDispatchMu`
- normal FC ACK waits can be long
- an emergency path must not be accidentally serialized behind a long normal operation

Repair lock scope/priority without bypassing authority/failsafe/safety checks.

## Exit

- pass-through parity proven
- command timing observable
- emergency contention tests exist
- no authority guard bypass
- full regression green

## Implementation record (2026-08-28)

**Part A — frontend CommandGateway** (`frontend/swarmgod_gui/core/command_gateway.py`)
- One observable command boundary records command/source/targets/monotonic start-end/duration/result plus S08 identity. It does not own flight authority, bypass Go safety, invent retries, or queue/reorder flight commands.
- Provider pattern (`CommandGateway(lambda: self.client)`) — never captures a `CoreClient`, so tests that swap `win.client = FakeClient()` still route correctly.
- **System-wide mutating coverage:** cockpit `_run_cmd`, quick-card `_card_cmd`, legacy/map/tablet GOTO/HOLD/STOP/RC, E-STOP, swarm config/start/stop/return, SetLeader/resync, servo set/release/prime, geofence, SITL ParamSet, auto-after-land mode, demo takeoff, Preflight live checks, and mission Start/Cancel mutations all dispatch through `CommandGateway` (or a helper that immediately dispatches through it).
- Connect/disconnect, telemetry/event streams, state reads, and channel lifecycle remain outside the Gateway because they are not flight/control-mutating operator commands. Mission Start/Cancel retain their mission ownership semantics while their actual mutating wire calls also pass through the Gateway.

**Part B — emergency priority / contention** (Go)
- Root cause: lock scope plus per-drone, just-in-time reservations allowed old multi-target intent to survive long enough to issue a stale later-target command after a newer emergency had already completed.
- **Whole-batch intent model** (`backend/internal/api/command_reservation.go`): accepted targets are claimed before the first long FC wait. A newer accepted command replaces the exact per-target claim; the old claim remains permanently stale even after the newer lease is released. This closes `old LAND [D1,D2] → newer STOP ALL D2 → stale LAND D2` and equivalent Takeoff/RTL/HOLD/ChangeAlt races.
- Takeoff establishes all claims before its read-only precheck, so a newer takeover completing during precheck cannot be followed by a revived older Takeoff send. Duplicate IDs in one target set are canonicalized before reservation so one intent sends at most once per drone.
- Takeover priority is explicit: Navigation < RTL < Land < Disarm < StopAll < Kill. Stronger active takeover cannot be overwritten by weaker intent; equal/lower intent follows latest-intent semantics.
- `SetMode`/`RcMove` and other normal batches establish ownership in a short critical section and execute FC sends with `missionDispatchMu` released. `StartMission` still fails closed on conflicting reservations.
- Core mission GOTO/HOLD also sends with the ownership lock released; cancellable context plus `missionSendGuard` closes the final cancellation→FC-write race without holding the lock over ACK waits.
- Manual/batch command claims carry the equivalent final-write guard: takeover acceptance is atomic with the old command's transport write, but never holds the ownership lock across an FC ACK wait.
- Takeoff rollback no longer infers takeover from human-readable error text. It uses claim ownership (`claimSuperseded` / `claimCurrent`), and rollback LAND itself is permitted only through a still-current original Takeoff claim.
- `command.Service.Takeoff` inter-step waits are context-aware, so takeover cancellation aborts the GUIDED→arm→takeoff sequence promptly.
- Core `request_id` idempotency now uses a per-key completion channel: a same-key retry waits/replays the original regardless of command duration; the former 10-second fallback that could re-execute `fn()` is removed. Protobuf results are cloned with `proto.Clone` to preserve replay shape without copying internal locks.

**Tests:** deterministic stale-later-target batch tests, Takeoff rollback claim tests, >10-second in-flight idempotency test, request replay tests, mission-send cancellation tests, targeted `api/command/audit`, full `go test ./...`, and `go vet ./...`. `go test -race` remains unavailable on the current dev box because no C compiler is installed.

**Not done here:** mission-scope expansion, persistence/restartability expansion, hardware bench, and real-flight release. Those remain V3-S09+ / V3-H02 / V3-R01.

---

# 10. V3-S08 — Command Identity + Correlation + Dedup

**Status: DONE (verified 2026-08-29).**

Already exists in pieces:

- many frontend command methods create request UUIDs
- Go Idempotent covers several flight command classes
- Mission Start uses operation_id
- Core mission has run_id

What is missing is a system-wide identity model.

Target model:

```text
operator_operation_id
        ↓
command_id / attempt_id
        ↓
request_id
        ↓
Core audit/result
```

Required work:

1. Correlation only — no behavior change.
2. Observe duplicate candidates.
3. Classify command semantics:
   - idempotent
   - non-idempotent
   - replace-current
   - cancel-current
   - emergency/takeover
4. Enforce dedup only per proven class.
5. Test timeout + late reply + reconnect + double click + stale callback.

Do not treat the current request_id implementation as system-wide completion.

## Implementation record (2026-08-28)

**Identity model** (`frontend/swarmgod_gui/core/command_correlation.py`): a
thread-free, Qt-free `CommandLedger` assigns each dispatch an `operation_id`
(operator intent), a stable `command_id` (family + sorted targets), and a unique
`attempt_id`; the existing per-call `request_id` and Core `Idempotent` remain the
wire/backend retry-dedup layer beneath it.

**Classification** — six classes: `IDEMPOTENT` (ARM/TAKEOFF/MODE/EQUALIZE),
`TAKEOVER` (DISARM/LAND/RTL/HOLD), `REPLACE_CURRENT` (GOTO/GO ALT/CHANGE ALT/RC),
`CANCEL_CURRENT` (STOP ALL/CANCEL), `EMERGENCY` (KILL/E-STOP), and
`NON_IDEMPOTENT` (SERVO/unknown).

**Duplicate observation** — a `command_id` already in flight (or seen again) is
flagged `duplicate` on its record; `ledger.duplicates()` lists them. Observation
never grants authority or bypasses Core safety.

**Class-specific dedup enforcement** — integrated into `CommandGateway.dispatch`:
only an **IDEMPOTENT** command whose identical semantic `command_id` is already in
flight can be suppressed as a double-click. `TAKEOVER`, replace-current,
cancel-current, emergency, and non-idempotent commands are **never** suppressed.
A suppressed copy returns `DedupedResult(ok=None, in_progress=True)` and therefore
cannot be mistaken for FC success while the winning request is still running.

**Tests:** classification/fingerprint/dedup semantics, suppressed-result neutrality,
wire metadata attachment, concurrent context isolation, system-wide AST gateway
coverage, Preflight gateway coverage, Go metadata extraction, auth-before-
correlation ordering, request/result linkage, and idempotent replay joinability.
The targeted frontend command/correlation/preflight run is 85/85 green; the
complete frozen-source frontend suite is 1077/1077 green (40:53, exit 0).

**End-to-end correlation:** `CommandGateway` binds operation/command/attempt IDs
with a contextvar; the unary gRPC interceptor emits metadata; the Go interceptor
runs after auth and records the final `request_id`, command, outcome, allowed flag,
and handler error (when present). This remains observability-only and does not
participate in authorization, safety, authority, takeover ordering, or Core
idempotency.

**Not done (S09+):** mission-scope expansion, persistence/restartability expansion,
hardware bench, and real-flight release. V3-H02 remains blocked on actual FC and
V3-R01 remains locked.

## Independent review — round 2 fixes (2026-08-28)

A second independent source review found deeper issues (tests were green but did
not cover them). All are fixed:

1. **request_id idempotency × reservation (HIGH):** reserve/preempt happened
   *before* `s.cmd.Idempotent`, so a same-`request_id` retry busy-rejected
   (normal) or cancelled/preempted the ORIGINAL (takeover). Fixed by wrapping
   reserve/preempt+send **inside** the per-drone idempotent closure
   (`idempotentNormal` / `idempotentTakeover`): a same-`request_id` retry is
   deduped (wait/replay) before it can ever reach the reservation. All command
   handlers (Arm/Takeoff/Goto/SetMode/RcMove normal; Disarm/Kill/Land/RTL/Hold/
   StopAll/ChangeAlt/EqualizeAlt takeover) route through these. Tests:
   `TestSameRequestIdNormalReplaysNotBusyRejects`,
   `TestSameRequestIdTakeoverDoesNotCancelOriginal`,
   `TestCompletedSameRequestIdReplaysWithoutResend`.
2. **Takeover-vs-takeover ordering (HIGH):** explicit `takeoverPriority`
   (Navigation<RTL<Land<Disarm<StopAll<Kill). A stronger in-flight takeover
   (KILL) is never preempted/overwritten by a weaker one; equal/weaker are
   cancelled latest-intent-wins. Tests `TestWeakerTakeoverCannotPreemptKill`,
   `TestKillPreemptsWeakerTakeover`.
3. **New-mission-after-cancel single-flight (HIGH):** replaced a racy
   "still unwinding" reject with freeing the single-flight mission-send slot on
   cancel (the cancelled send is harmless — guard tripped + ctx cancelled, so
   command.Service refuses any FC write) and a generation check so the old send's
   return cannot clear a newer slot. Test `TestCancelFreesMissionSendSlotImmediately`.
4. **S08 semantic fingerprint (HIGH):** the dedup fingerprint now strips UI-only
   suffixes (`[tablet]`) and callers pass an explicit `dedup_key` with actual args
   (TAKEOFF exact altitude+confirmed) so a rounded display label cannot collide;
   ARM force / different-altitude are distinguished. Tests
   `test_ui_tablet_suffix_does_not_change_identity`,
   `test_takeoff_same_rounded_label_different_actual_altitude`,
   `test_arm_force_false_vs_true_do_not_collide`.
5. **DedupedResult is not success (HIGH):** `DedupedResult.ok=None`,
   `in_progress=True`; `app.py` `_run_cmd`/`_card_cmd` + `_on_cmd_result` now show
   a distinct "in progress (duplicate)" info toast — never a false success/failure.
   Test `test_double_click_dedup_one_client_call` updated to the new semantics.

**Verified present + safe:** mission GOTO/HOLD sends with the lock released
(cancellable + guarded at the final FC write); correlation runs AFTER auth and is
observability-only. The former round-2 MEDIUM gaps are now closed in round 3:
flight/control-mutating app, Preflight, and mission mutation calls are covered by the Gateway, and
Core correlation audit is linked to `request_id` plus final command outcome/result.

## Independent review — round 1 fixes (2026-08-28)

An independent source review reproduced defects the passing tests did not cover.
All five HIGH findings are fixed; MEDIUM items are recorded for follow-up.

**HIGH — fixed**
1. **S08 fingerprint (MODE):** `command_id` used only family+targets, so `MODE
   GUIDED` and `MODE LOITER` collided and the second was suppressed. Fixed:
   `CommandLedger.begin` now fingerprints on the full semantic label (or an
   explicit `dedup_key`), not the family.
2. **S08 fingerprint (TAKEOFF):** same root cause — `TAKEOFF 20m` in flight
   suppressed `TAKEOFF 30m`. Fixed by (1).
3. **S07 mission GOTO/HOLD under lock:** the Core mission send held
   `missionDispatchMu` across the FC ACK, so an emergency could wait behind it.
   Fixed: mission send now runs with the lock released via a cancellable
   `missionSendCancel`; `command.Service.Goto/Hold` refuse a preempted (cancelled-
   ctx) send before the FC write; operator takeover / CancelMission / failsafe
   interrupt cancel the in-flight send.
4. **S07 SetMode/RcMove under lock:** moved to the reservation model (reserve →
   release lock → send), so an emergency is not serialized behind them.
5. **S07 Takeoff rollback after preemption:** a preempted Takeoff could still emit
   a stale rollback LAND. Fixed with typed claim ownership; rollback is authorized
   only while the original claim remains current, never by parsing result text.

**Tests added:** frontend `test_mode_guided_vs_loiter_not_deduped`,
`test_takeoff_20_vs_30_not_deduped`, `test_identical_semantic_command_still_deduped`,
`test_explicit_dedup_key_overrides_label`, `test_gateway_mode_change_not_deduped`;
Go `TestMissionSendRunsWithLockReleased`, `TestOperatorTakeoverCancelsInFlightMissionSend`,
`TestCancelMissionCancelsInFlightSend`, `TestCommandServiceRefusesPreemptedSend`,
`TestSetModeAndRcMoveSendOutsideLock`, `TestTakeoffSkipsRollbackWhenPreempted`.

**Historical MEDIUM findings — closed by later review rounds**
- Gateway coverage expanded from `_run_cmd`/`_card_cmd` to all inventoried flight/control-mutating app paths, Preflight live checks, and mission Start/Cancel mutations.
- operation/command/attempt IDs now propagate as gRPC metadata and are linked in Core audit to `request_id` and final result/outcome.
- `DedupedResult.ok` is now `None` with `in_progress=True`; suppressed copies are not reported as success.
- takeover ordering is explicit and stale older multi-target intent is invalidated by exact per-target claim replacement.

---

# 11. V3-S09 — Mission Authority Expansion

**Status: FINAL REVIEW PASS — 0 CRITICAL / 0 HIGH — READY FOR PRE-FLIP FREEZE; AUTHORITY NOT FLIPPED**

This stage absorbs all old V1/V2 confusion around "what mission work is still Python?".

## Prerequisite authority already migrated in V3-S04 (not S09 progress)

- single-drone GROUPED
- single-drone GROUPED + WAIT

## Still Python-owned / to migrate separately

### S09-A — Multi-drone GROUPED

**Status: PRE-FLIP IMPLEMENTATION EXISTS — FRESH INDEPENDENT REVIEW PASS; authority NOT flipped.**

This delivered characterization → Core state model → shadow/parity → per-drone
dispatch scaffold → tests for multi-drone GROUPED, deliberately **without**
granting it live command authority. No profile token enables it; the existing
`core-single` / `core-single-wait` live authority scope remains the only configured
Core scope, and the frontend still routes multi-drone GROUPED to the Python executor.

**Independent-review status:** Round-2 repair changes for P0-1 through P1-10 are
present in the production implementation and characterization tests, with additional
source-review fixes applied afterward. The later fresh independent current-source
review accepted the PRE-FLIP A–F implementation with **0 Critical / 0 High**. This
is a software-review green only: the authority flip is still OFF and each live scope
must still complete its SITL/failure/soak/hardware/controlled-flight validation ladder.

#### Characterized legacy behavior (compatibility contract)

Established from the current Python executor (`app.py` `_wp_advance` /
`_on_target_reached`, `swarm_logic.group_goto_targets` / `movement_order`,
`map.html` `TGT_REACH_M = 3.0`), pinned headlessly in
`frontend/tests/test_grouped_multi_characterization.py`:

- **One shared route**, all selected participants, a single shared current-index.
- Per index, each drone flies to a **formation-offset target**
  `target[d] = pos[d] + (waypoint − group_centroid)` — the whole group is
  translated so its centroid lands on the waypoint, preserving every pairwise
  offset. It does **not** fly to the raw waypoint.
- No-position fallback: the raw waypoint (legacy `setdefault(d, (wp.lat,wp.lon))`).
- Per-drone GOTOs dispatched **front-of-travel first** (`movement_order`),
  staggered 150 ms in the UI, at **per-drone altitude** (`_last_alt`/`_alt_for`).
- **Arrival is judged per drone against its own offset target** within 3.0 m,
  **not** the raw shared waypoint.
- **Group barrier:** the shared index advances only when *every* participant has
  reached its offset target; an early arrival waits.
- WAIT is group-scoped (HOLD-all-once, deadline poll, then action, then advance).
- Cancellation bumps a generation so stale callbacks are dropped.

Single-participant is the degenerate case (centroid == own position → offset
target == raw waypoint), which is exactly why the already-live single-drone Core
authority stays correct while judging against the raw waypoint.

#### Supported scope (S09-A)

GROUPED, ≥ 2 participants, one shared route, per-drone frozen altitude,
formation-offset targets, front-of-travel order, per-drone-offset arrival barrier.

#### Unsupported scope — stays Python-owned

Multi-drone **WAIT**, payload/servo **actions**, **rtl_after**, **SEPARATE**,
**SWARM leader**, **WAVE**. Each keeps the verified Python executor until
separately migrated (S09-B..E).

#### Core authority predicate (exact)

`MissionPlan.ValidateAuthorityGroupedMulti()` accepts a plan **only** when: mode
GROUPED · participants ≥ 2 · valid shared route · `rtl_after == false` · no
waypoint action · no waypoint WAIT. It is a **separate** predicate from the
single-drone `validateAuthorityBase` (V1/V2), so widening multi can never widen
single (proved by `TestSingleDroneAuthorityStillRejectsMultiParticipant`).

Enablement is `Engine.EnableGroupedMultiAuthority()` — an opt-in that **no live
profile token maps to**. `server.go` still only wires `core-single` /
`core-single-wait`; `missionAuthorityMode` rejects any `core-grouped-multi`
string (`TestMissionAuthorityModeProfileGate`).

#### Python fallback predicate

`mission_shadow.authority_eligible` still returns `False` for
`len(participants) != 1` — unchanged. Multi-drone GROUPED never calls
authoritative `StartMission` and remains fully Python-owned in every live profile.

#### No-dual-authority mechanism

- In multi mode the single-drone `ClaimAuthorityGoto` / `ClaimAuthorityHold`
  claims are **inert**; only `ClaimAuthorityGroupedGotos` emits.
- The group claim is **atomic per index** (`groupClaimedIndex`): exactly one
  GOTO per participant per index, no re-dispatch until the barrier advances.
- Arrival is judged only after the group GOTOs for the current index are frozen,
  so a stray sample between advance and the next dispatch cannot advance early.
- Core owns each participant's navigation (`missionOwnsDroneLocked` true for every
  participant); Python suppresses its GOTO while authority is active.
- The per-drone dispatcher (`dispatchGroupedMultiAuthority`) is reached only via
  the test-only opt-in; the single-drone `claimMissionSendLocked` path is unchanged
  and always taken in production. Each participant send has its own cancel + guard.

#### Takeover behavior

Operator HOLD/LAND/RTL/STOP ALL/KILL/cancel make the run terminal *before* any
takeover command, so it cannot resume and overwrite the operator
(`cancelMissionForOperatorTargetsLocked` covers any participant;
`TestGroupedMultiApiOperatorTakeoverMakesRunTerminal`, engine-level
`TestGroupedMultiOperatorTakeoverDuringWaypoint`, and takeover-at-transition).

#### Failsafe behavior

Battery/link ALARM `Interrupt`s the mission (INTERRUPTED, no auto-resume) while
the fleet manager remains the sole owner of the failsafe RTL — no second failsafe
authority (`TestGroupedMultiApiFailsafeInterrupts`, engine
`TestGroupedMultiFailsafeInterrupt`).

#### Tests / evidence

- Characterization (headless Python): `test_grouped_multi_characterization.py` (11).
- Go port (`internal/mission/grouped_multi.go`) + parity to the Python contract:
  `grouped_multi_parity_test.go` (7).
- Adversarial engine suite `grouped_multi_test.go` covers the required 20: happy
  path, barrier, simultaneous arrival, stale telemetry, disconnect, GOTO reject,
  operator HOLD/STOP/KILL/cancel, takeover at transition, battery/link failsafe,
  terminal-ignores-later-telemetry, duplicate-Start/no-dual-authority,
  unsupported-stays-Python, no-dual-authority, exact command count/order.
- API contract: `internal/api/mission_grouped_multi_test.go` + gate assertion.
- Verification commands are mandatory for the Round-3 implementer report; this
  roadmap does not convert implementer test results into an Independent Review green
  verdict.

#### Remaining risks / open items (PRE-FLIP)

1. **Authority flip still deferred** (by design). `dispatchGroupedMultiAuthority`
   exists and is proven, but is reached only via the test-only opt-in
   (`EnableGroupedMultiAuthority`); no live token wires it. Flipping = adding the
   live token + frontend `authority_eligible` for multi + SITL → failure injection
   → soak → retire legacy.
2. **Telemetry freshness is navigation-sample based.** `Drone` now tracks
   `GLOBAL_POSITION_INT` and `GPS_RAW_INT` timestamps separately from generic
   `lastMsg`; mission seeding uses the older of `PositionAgeSec` / `GpsAgeSec`.
   A fresh HEARTBEAT therefore cannot refresh stale cached coordinates/fix evidence,
   and invalid/no-GPS/(0,0) positions are unavailable.
3. **WAIT / actions / rtl_after remain Python-owned** for multi-drone GROUPED and
   are rejected by `ValidateAuthorityGroupedMulti` (deferred to later substages).
4. Frontend `authority_eligible` unchanged — multi-drone GROUPED still runs on the
   legacy Python executor in every live profile.

### S09-B — SEPARATE

**Status: PRE-FLIP IMPLEMENTATION EXISTS — FRESH INDEPENDENT REVIEW PASS; authority NOT flipped.**

**Characterized legacy behavior** (`app.py` `_wp_advance_one` / `_on_target_reached`
SEPARATE branch; `waypoint_logic.check_route_conflicts`): each participant has its
OWN route and index and advances INDEPENDENTLY; a SEPARATE drone flies to its
route's **RAW** waypoint (NO formation offset — GROUPED-only) at per-drone altitude;
arrival is judged per drone against that raw waypoint; the run completes when every
route finishes. Because routes are independent they can cross, so a **route-conflict
preflight** gates execution: a pair conflicts only when altitude gap ≤ `alt_sep_m`
AND paths come within `min_dist_m`. Pinned headlessly in
`frontend/tests/test_separate_characterization.py` (exercises the real
`check_route_conflicts`).

**Supported scope:** SEPARATE, ≥ 2 unique participants, one route per participant,
per-drone altitude, independent progression.
**Deferred (Python-owned):** WAIT, actions, rtl_after (rejected by
`ValidateAuthoritySeparate`). Route-conflict geometry is now also implemented in
Core with the same Legacy thresholds (altitude gap ≤ 2m and route distance < 6m).
The API authority boundary additionally requires a fresh valid current position for
every participant and prepends those starts before checking current→WP1 segments.

**Core authority predicate:** `MissionPlan.ValidateAuthoritySeparate()` (mode
SEPARATE · ≥2 unique participants · exact route bijection · planned-route conflict
check · no action/WAIT/rtl_after). `StartMission` adds the fresh-start conflict
preflight whenever the in-process SEPARATE authority opt-in is enabled. Enabled only
via `Engine.EnableSeparateAuthority()` — no live token; separate from every other
scope (single-drone + GROUPED claims are inert in SEPARATE mode).

**Claim / no-dual-authority:** `ClaimAuthoritySeparateGotos` emits one raw-waypoint
GOTO per participant with an un-dispatched current index (`sepClaimedIndex` per
drone → no duplicate send), independently as each drone advances. The shadow
engine's existing `observeSeparate` arrival (raw waypoint) is already parity-correct,
so SEPARATE needs no offset machinery.

**Takeover / failsafe:** mode-agnostic — `cancelMissionForOperatorTargetsLocked`
(operator takeover) and `Interrupt` (battery/link, participant-only) make the run
terminal and suppress further claims.

**Tests / evidence:** `internal/mission/separate_multi_test.go` (independent
progression, one-finishes-others-continue, stale participant, per-drone alt/raw
target, reject best-effort, takeover/failsafe terminal, non-participant ignored,
duplicate-Start, no-dual-authority, unsupported rejected);
`internal/mission/separate_conflict_test.go` (same-alt crossing blocked, altitude
separation parity, current→WP1 crossing); `internal/api/mission_separate_test.go`
(fresh-start requirement, crossing-start rejection, ownership, takeover, failsafe);
`frontend/tests/test_separate_characterization.py` remains the Legacy reference.

**Open items (PRE-FLIP):** SEPARATE per-drone **dispatcher/live enablement** remains
separate flip-round work; frontend `authority_eligible` is unchanged (still
Python-owned). The fresh independent PRE-FLIP review has already accepted the current
source at 0 Critical / 0 High. What remains is authority-on SITL parity/failure testing,
soak, hardware evidence and controlled-flight validation before any live flip.

### S09-C — SWARM Leader Path

**Status: TAKEOVER/SUCCESSION PRE-FLIP COMPLETE — AUTHORITY NOT FLIPPED.**
(Operator-takeover policy ratified and implemented 2026-08-30; the remaining S09-C
open items are live-enablement validation, not product decisions.)

**Characterized legacy behavior** (`app.py` `_wp_advance` with `ids=[swarm_head]`):
when a swarm is active, the waypoint mission sends a GOTO **only to the Head/Leader**
at the raw waypoint; the Go swarm formation loop drags the followers. So the mission
is the navigation authority for the **leader only**, and `swarm.Manager` is the
authority for the **followers** — two authorities over *different* drones, never the
same one. Progression is driven by leader arrival (the shadow engine already models
this: `arrivalParticipantIDs == leader`).

**Supported scope:** SWARM_LEADER, ≥ 2 participants (leader + ≥1 follower), one
shared route, per-leader altitude.
**Deferred (Python-owned):** WAIT, actions, rtl_after.

**Core authority predicate:** `MissionPlan.ValidateAuthoritySwarmLeader()` (mode
SWARM_LEADER · ≥2 participants · leader is a participant · shared route · no
action/WAIT/rtl_after). Enabled only via `Engine.EnableSwarmLeaderAuthority()` — no
live token; single/GROUPED/SEPARATE claims are inert in this mode.

**Claim / no-dual-authority:** `ClaimAuthoritySwarmLeaderGoto` emits exactly one
GOTO for the **leader** (raw waypoint, leader alt), barrier once per index; follower
arrival never advances; `FollowerIDs()` names the drones the mission must never
command. Proven by `TestSwarmLeaderNeverCommandsFollower` /
`TestSwarmLeaderFollowerArrivalDoesNotAdvance`.

**Takeover / failsafe / terminal parity:** Mission owns only the fixed non-zero
leader; formation owns followers. Leader battery/link failsafe interrupts the route
and revokes formation navigation fail-closed. Follower battery/link failsafe excludes
that follower from formation movement while leader route progression continues,
matching current `swarm.Manager` behavior. A rejected leader GOTO is Legacy
best-effort: no blind retry and no terminal fail; the route stalls at its barrier
with a structured participant rejection. Natural route completion releases the
temporary Mission leader binding but does not stop the already-running formation,
matching Legacy waypoint finish.

Emergency/takeover preemption is now two-phase: `RevokeFormationNavigation` removes
formation write authority (including a final-write guard used during form-up) without
waiting for loop teardown, and `RevokeReturnNavigation` closes a final-write guard on
RETURN/LAND before cancelling its context. Starting RETURN also uses formation revoke
rather than synchronous `Stop()`, so a concurrent KILL cannot be trapped behind a
RETURN-start teardown wait. KILL/STOP ALL therefore do not sit behind the former
two-second loop-shutdown wait, while stale form-up/follower/return writes remain
blocked at the transport boundary.

**Tests / evidence:** `internal/mission/swarm_leader_test.go` (leader-only claim,
explicit rejection of `LeaderID=0`, follower-never-commanded, leader-driven barrier,
leader-vs-follower failsafe parity, no-dual-authority, unsupported rejected), plus
API/swarm split-ownership tests for leader-only Mission ownership, follower exclusion,
and takeover boundaries.

Succession evidence: follower-exclusion-only and leader-promotion inside the same
run/index, original-order election skipping ineligible members, multiple successions,
no-successor interruption without mode conversion, pre-promotion telemetry rejection,
terminated-run resurrection and stale-generation rebind replies
(`internal/mission/swarm_leader_test.go`); end-to-end operator-takeover through the
real locked sequence, rebind failure, and no-successor interruption
(`internal/api/mission_swarm_leader_test.go`); formation ownership handoff, stale
generation and refused inconsistent rebinds (`internal/swarm/split_ownership_test.go`);
restart after and DURING succession as command-inert recovery that never auto-rejoins
an excluded member (`internal/api/mission_persistence_test.go`).

**Operator takeover + leader succession (RATIFIED 2026-08-30, PRE-FLIP):**

```text
Follower takeover:
  exclude that follower only; the remaining swarm continues the SAME mission.

Leader takeover/removal:
  exclude the old Leader and promote the next eligible participant in ORIGINAL
  participant order (skipping excluded/operator-controlled/ineligible members).

Succession preserves the same run_id and current route/index; the remaining
formation is rebound to the new Leader.

Excluded participants never auto-rejoin the current run (telemetry recovery,
reconnect and UI restart included).

If no eligible successor / minimum formation remains: mission INTERRUPTED.

No automatic SWARM_LEADER → SINGLE conversion. No invented RTL/LAND/new mission.
```

Implementation is split across the three existing owners, with no parallel safety
system: `Engine.BeginSwarmOperatorTakeover` / `CompleteSwarmOperatorTakeover` (mission
membership + succession revision, progression frozen while a handoff is pending),
`Server.applySwarmMissionTakeoversLocked` (runs before the generic ownership cancel,
revokes the leader's in-flight send, then reconciles formation), and
`swarm.Manager.RebindMissionMembership` (atomic leader/member swap that bumps
`formationGen`, so the previous formation generation is stale). Mission ownership
follows the current runtime leader only (`missionSnapshotOwnsDrone`), so an excluded
drone is owned by neither mission nor formation. Durable evidence records the current
leader plus the active/excluded partition, and validation accepts a legitimately
promoted leader while still rejecting inconsistent membership.

**Open items (PRE-FLIP):** live enablement remains blocked — no `core-swarm-leader`
token is reachable from production wiring. The fresh independent review has already
re-checked the asynchronous revocation/final-write-guard design for form-up, steady
followers, RETURN/LAND, KILL and STOP ALL at 0 Critical / 0 High. Authority-on SITL,
failure injection, soak, hardware and controlled-flight validation of succession remain
outstanding before any live flip.

### S09-D — WAVE

**Status: PRE-FLIP IMPLEMENTATION EXISTS — FRESH INDEPENDENT REVIEW PASS; authority NOT flipped.**

WAVE has **no** Core `mission.Mode`; it is a higher-level orchestration of sequential
GROUPED runs (one per group). It is modeled as a self-contained shadow state machine
`internal/mission/wave.go` (`WaveEngine`), issuing no command.

**Characterized legacy behavior** (`app.py` `_wave_execute` / `_wave_start_next_group`
/ `_wave_begin_group_route` / `_wave_route_finished` / `_wave_tick`):
`takeoff → route → waiting_land → landed → next group … → complete`; groups sorted;
≥ 2 online groups required; a group advances only after ALL members are
**landed + disarmed** and RTL is inactive; a **5-minute per-phase timeout** aborts the
WHOLE wave (no next group); a **generation** counter invalidates stale QTimer
callbacks so nothing starts a next group after cancel/abort/timeout/failsafe;
`auto_next` (captured at start) skips the confirm for subsequent groups only.
(WAVE's legacy logic is Qt-coupled, so parity is pinned by mirroring this documented
state machine in `wave.go`, not a new headless Python test.)

**Model:** `WavePhase` (takeoff/route/waiting_land/landed) + `WaveState`
(RUNNING/COMPLETED/CANCELLED/INTERRUPTED/TIMED_OUT). Phase events take a `Token()`
(a monotonic run/group/phase token) so a stale callback is ignored even when a newer group re-enters the same phase. `StartNextGroup` requires the
landed proof; `Poll` enforces the timeout; `Cancel`/`Interrupt` are terminal and bump
the generation.

**Tests / evidence:** `internal/mission/wave_test.go` — ordering, first-group takeoff,
full sequential progression, next-group-blocked-until-landed-proof, out-of-phase
ignored, cancel-stops-next-group, battery/link interrupt, timeout aborts whole wave,
per-phase timeout reset, stale previous-group callback ignored, ≥2-groups validation,
auto_next captured, restart snapshot.

**Open items (PRE-FLIP):** per-group GROUPED **execution wiring** (reusing S09-A) and
the real takeoff/landed telemetry proofs are the flip-round integration; WAVE stays
Python-owned and Core-slot-guarded (S05) in every live profile.

### S09-E — Payload / Servo A/B waypoint actions

**Status: PRE-FLIP IMPLEMENTATION EXISTS — FRESH INDEPENDENT REVIEW PASS; authority NOT flipped.**

**Characterized legacy behavior** (`app.py` `_wp_run_action`): on arrival at a
waypoint with an action (after any WAIT):
`HOLD(ids) → servo_set(ch,pwm) per drone → wait 2s → servo_release(ch) per drone →
progression`. Each drone is set once and released once (no duplicate); a generation
guard drops stale callbacks after cancel/abort/failsafe. **Best-effort failure**: if
HOLD or a servo_set is rejected, the sequence still releases and STILL advances the
route (legacy `finish(False)` calls the progression callback) — it records the
failure but does not stop the mission. Legacy `finish()` launches RELEASE for every
participant. The PRE-FLIP Core model now mirrors that conservative cleanup scope:
once action handling has entered HOLD/servo processing, every participant remains an
exactly-once RELEASE target. This also covers lost/uncertain SET results so a payload
cannot remain engaged merely because a result never arrived.

**Model:** `internal/mission/payload_action.go` (`ActionEngine`): phases
`hold → servo → dwell → release → done`; per-drone SET results are tracked, while
cleanup is release-all exactly once after action handling begins. `Poll` enforces the
2s dwell; `Cancel`/`Interrupt` immediately block new SET and route progression while
keeping cleanup RELEASE valid. Cleanup failure is observable; `ShouldAdvance()`
stays false after cancel/failsafe.

**Tests / evidence:** `internal/mission/payload_action_test.go` — full sequence,
no-duplicate set/release, servo-reject-still-advances, hold-reject-still-advances,
cancel-before/during (no advance), stale-callback ignored, failsafe-during (no
advance).

**Open items (PRE-FLIP):** real command execution + ack wiring (through
command.Service servo path) and the tie-in to GROUPED/SEPARATE progression are the
flip-round integration; actions stay Python-owned and are rejected by every
authority predicate.

### S09-F — `rtl_after`

**Status: PRE-FLIP IMPLEMENTATION COMPLETE — RETURN POLICY RATIFIED;
AUTHORITY/LIVE VALIDATION PENDING (2026-08-30).**

`rtl_after` remains a compatibility input only. Plan construction resolves it to
one explicit domain owner: `NONE`, `RTL_ALL_AFTER_MISSION`, `SWARM_RETURN`, or
`WAVE_MANAGED_RETURN`. SEPARATE records `RETURN_ALL_ON_MISSION_COMPLETE` as its
approved default; `RETURN_EACH_ON_ROUTE_COMPLETE` is represented but rejected until
a return-corridor safety contract exists.

The handoff is deliberately ordered: the route reaches natural `COMPLETED`, mission
navigation becomes inactive, the resolved policy becomes `RETURN_PENDING`, and only
then may a distinct Return claim become `RETURNING`. Generic RTL uses the existing
`command.Service` safety/audit path, deterministic request identity, whole-batch
per-target claims, cancellable contexts, and final-transport-write guards. GROUPED
waits for the all-participant barrier. SEPARATE waits for all routes. SWARM_LEADER
delegates the entire participant set to the existing Swarm Return manager and never
issues a leader-only generic RTL. WAVE always owns exactly one managed route →
return/land → landed/disarmed lifecycle. Payload has no Return authority of its own.

Cancel, operator takeover, emergency, and fleet/FC failsafe permanently suppress a
pending/in-flight automatic Return. The dedicated automatic Return command entry
also checks the fleet failsafe latch before delegating to RTL, leaving fleet/FC as
the sole safety-flight-action owner. Return ACK waits occur with the ownership lock
released; stronger authority closes the old final-write guards before cancellation,
so no stale later-target write can begin.

S10 persistence now includes resolved policy, Return state/participants/reason, and
revision/timestamp evidence. `RETURN_PENDING` or `RETURNING` loaded by a new Core is
converted to `INTERRUPTED` + Return recovery-required evidence with `Active=false`,
`Authority=false`, and zero automatic commands. It is evidence, never resumability.

**Gate remains closed:** the pre-flip code is enabled only by an in-process test
method. No environment/profile token calls it. `core-single` and
`core-single-wait` therefore retain their old live behavior and reject Return input;
`core-grouped-multi`, `core-separate`, `core-swarm-leader`, `core-wave`, and
`core-payload` remain OFF. Any authority flip is a separate explicit task after
independent review and the validation ladder below.

Software evidence captured 2026-08-30: the exact six-package S09-F backend run
passed 6/6 packages; the full backend run passed 29/29 packages; `go vet ./...`
passed with zero diagnostics; and the affected frontend regression run passed
378/378 tests (one non-test-impacting pytest cache-permission warning). No SITL,
hardware, FC, or real-flight validation is claimed by this evidence.

Mandatory pattern for every scope:

```text
legacy characterize
→ shadow
→ compare
→ authority gate OFF by default
→ SITL authority
→ failure injection
→ soak
→ only then retire legacy executor for that scope
```

---

# 12. V3-S10 — Persistence + Restartability

**Status: COMPLETE (verified 2026-08-29).** Full implementation detail and test
evidence: [V3_S10_PERSISTENCE_RESTARTABILITY.md](V3_S10_PERSISTENCE_RESTARTABILITY.md).

Completion statement (proven by tests):

> *An unfinished Core mission survives a Core restart as accurate durable recovery
> evidence, but never survives as automatic flight authority.*

### Delivered

Durable mission persistence through the existing Core SQLite store (`mission_state`
singleton + operation-id history), with versioned records, checksum/integrity
verification, monotonic persistent `run_id`, and durable `operation_id` history:

- run_id / operation_id, immutable plan, participants
- state, GROUPED `current_index`, SEPARATE per-drone `sep_index`
- WAIT evidence (frozen remaining, timer NOT restarted)
- transition/reason/timestamp/revision boundaries
- participant rejections, terminal reason/state, recovery transitions

Core restart rule (implemented + tested):

```text
persisted non-terminal mission found
→ RECOVERY_REQUIRED / INTERRUPTED
→ Active=false, Authority=false
→ issue NO flight command (no GOTO/HOLD/TAKEOFF/LAND/RTL/SERVO/WAVE-next/resume)
→ operator explicitly decides next action
```

Fail-safe + safety guarantees (tested): corrupt / truncated / unknown-schema /
stale-revision records fail closed to visible incompatible recovery with zero
commands; persistence write failure terminally suppresses progression without
inventing LAND/RTL and raises an operator-visible ALARM; a lost Start reply or
stale CancelMission from a previous Core session cannot create duplicate authority
or cancel a newer run; explicit recovery clear is idempotent and retires the old
operation_id.

### Reconnect / lifecycle

Reconnect is presentation-only: the Python cockpit rebinds `GetMissionState` into a
command-free cache, renders a distinct **`CORE RECOVERY REQUIRED`** status, and
`authority_slot_idle` fails closed on `recovery_required` so legacy prep/auto-TAKEOFF
never starts. S06 lifecycle shutdown remains the deterministic teardown foundation
this builds on.

Only `core-single` and `core-single-wait` remain live authority tokens; S09 authority
is NOT flipped by S10.

---

# 13. V3-S11 — Failure Injection Expansion

**Status: COMPLETE (verified 2026-08-29).** Full coverage matrix, reused evidence,
and new deterministic scenarios: [V3_S11_FAILURE_INJECTION_EXPANSION.md](V3_S11_FAILURE_INJECTION_EXPANSION.md).

The audit found the architecture-wide failure families (command gateway delay/error,
emergency contention/preemption, duplicate/late responses, persistence crash
boundaries, corrupted state, UI teardown, telemetry stale/out-of-order, multi-drone
partial failure, leader/follower, WAVE, payload, manual takeover) already covered by
the existing suite (F7/S04/S07–S10 + PRE-FLIP test-only mission modes). Continuation
work added deterministic tests for the remaining gaps — semantic persistence
corruption fail-closed, no persistence write-churn on inert telemetry, GROUPED
durable progression accuracy across restart, the recovery-clear DELETE boundary, and
out-of-order telemetry freshness — with **no production bug found** (every safety
invariant already held). At S11 time the SWARM follower operator-takeover policy
had not yet been ratified, so those scenarios were deliberately not tested against
an invented policy; the policy was later ratified and implemented PRE-FLIP in S09-C
on 2026-08-30, which now carries its succession failure-injection evidence.
Only `core-single`/`core-single-wait` remain live; S11 flips no authority.

Do not confuse this with old `V2 F7 DONE`.

Old F7 is valid and remains DONE **for the guarded single-drone mission scope**.

Already covered includes:

- duplicate Start
- stale Cancel
- cancel at waypoint boundary
- battery/link interrupt during WAIT
- stale browser callback
- lost Start reply
- cockpit restart
- client disconnect/reconnect during transit/WAIT
- Core process kill / no auto-resume
- FC-link interruption in SITL

V3-S11 expands testing to the whole resulting architecture:

- Command Gateway delays/errors
- emergency contention/preemption
- duplicate/late command responses
- persistence crash boundaries
- corrupted persisted state
- UI freeze/native teardown
- telemetry burst/stale/out-of-order
- multi-drone mission partial failure
- leader/follower failure
- WAVE transition interruption
- payload action interruption
- manual takeover during dispatch

Every scenario must declare Expected Safe State before execution.

---

# 14. V3-S12 — SITL Endurance + Performance

**Status: CURRENT OPERATIONAL GATE COMPLETE (verified 2026-08-30).**

Canonical tooling and actual endurance evidence are documented in
[V3_S12_SITL_ENDURANCE_PERFORMANCE.md](V3_S12_SITL_ENDURANCE_PERFORMANCE.md).

The current gate was strengthened beyond the earlier 30-minute requirement and
actually completed as **6h idle-connected PASS + 15m reconnect-churn PASS + 15m
client-lifecycle PASS**. The 12h/24h and larger multi-drone long-duration runs are
retained as **DEFERRED EXTENDED ENDURANCE** for a later explicit validation campaign;
they do not block current project progression.

One non-blocking follow-up item was observed in Core stdout during the 6h soak: a
single `SQLITE_BUSY` registry-upsert warning. Telemetry/RPC continued, no harness
hard failure occurred, and the warning is recorded for later SQLite contention review.

Do not confuse this with old `V2 F8 DONE`.

V2 F8 evidence remains valuable:

- live missionverify 4/4 cycles
- duplicate Start/reconnect/WAIT/Cancel coverage
- 500 alternating terminal Engine cycles
- bounded Core memory/thread/handle sample stable

Current evidence and deferred soak plan:

```text
6 hours idle-connected   = PASS / CURRENT OPERATIONAL GATE
15m reconnect-churn      = PASS
15m client-lifecycle     = PASS
12 hours                 = DEFERRED EXTENDED ENDURANCE
24 hours                 = OPTIONAL SOAK
5/10-drone long soak     = DEFERRED EXTENDED VALIDATION
mission Start/Cancel soak= DEFERRED EXTENDED VALIDATION
```

The earlier 30-minute minimum was superseded by the completed 6-hour run; it is no longer a pending current gate.

Measure:

- Python RSS/private bytes
- Go RSS
- Python threads
- native gRPC/background threads
- Go goroutines where exposed
- UI heartbeat/stalls
- telemetry ingest rate/age
- render rate/skip percentage
- command/RPC latency/error rate
- reconnect count
- mission transitions
- log/database growth

Workloads:

- idle connected telemetry
- 5-drone telemetry
- 10-drone telemetry
- repeated UI/map/Field Tablet presentation
- repeated safe mission start/cancel
- reconnect churn
- repeated UI create/close lifecycle

Output is a V3 endurance evidence report. It is not a real-flight release.

---

# 15. V3-H01 — Hardware Bench Preparation

**Status: DONE**

This is old V2 F9A.

Completed tooling:

- `backend/cmd/benchprobe`
- `backend/cmd/benchack`
- `scripts/bench_param_audit.py`
- `docs/f9_expected_params.json`
- `scripts/f9_evidence_report.py`
- self-tests
- HIL launch guard
- props-removed bench confirmation interlock

No actual hardware readiness is claimed here.

---

# 16. V3-H02 — Actual FC / Airframe Bench

**Status: IN PROGRESS — 5.1–5.3 PASS / 5.4–5.9 PENDING (latest evidence 2026-09-06).**

This is old V2 F9B. Actual hardware is now available and the gate has started.

Captured evidence:

- actual parameter/config baseline captured; `SYSID_MYGCS=250`, `FS_GCS_ENABLE=1`, pre-arm/battery/failsafe values recorded;
- actual telemetry timing verified at about 10 Hz with wide margin to the configured link-warning window;
- guarded disarmed COMMAND_ACK timing captured for LOITER→GUIDED and restore;
- operator-visible HIL follow-up reached GUIDED → ARM → DISARM;
- a real session bearer token was issued; the secret value is intentionally not stored in documentation.

Still required before H02 exit PASS:

- capture a fresh post-token SYSTEM TEST result for the HIL runtime;
- guarded Core-owned single waypoint evidence with exactly one Core mission GOTO and no concurrent Python mission GOTO;
- Core-owned WAIT/HOLD;
- UI disconnect/reconnect continuity for the same run;
- Cancel terminal behavior;
- controlled Core/GCS loss with approved onboard FC failsafe behavior — **mandatory blocker**;
- battery/link preemption where bench-safe;
- manual/STOP/E-STOP takeover;
- Core restart with no auto-resume.

`FENCE_ENABLE=0` is recorded and still requires explicit field-policy review before any controlled real-flight gate. SITL cannot substitute for H02, and H02 evidence does not itself unlock R01.

---

# 17. V3-R01 — Controlled Real Flight Release

**Status: LOCKED**

This replaces the ambiguous old wording "F10 Controlled Real Flight".

V3-R01 may only be evaluated after:

1. V3-H02 actual-FC bench PASS.
2. Any software stage required for the intended first-flight scope is green.
3. No unresolved CRITICAL/HIGH flight-safety finding exists.
4. Release review explicitly approves the exact mission/command scope.

`V3-S12 SITL Endurance` and `V3-R01 Controlled Real Flight` are completely different things.

---

# 18. Old V1/V2 → V3 Crosswalk

Use this table only when reading historical documents.

| Old name | V3 meaning |
|---|---|
| V1 Phase 0 | V3-S01 |
| V1 Phase 1 | V3-S02 |
| V1 Phase 2A/2B/2C | V3-S02 |
| V1 Phase 3 Telemetry Store | **V3-S06** |
| V1 Phase 4 Command Gateway | V3-S07 |
| V1 Phase 5 Run/Command ID + Dedup | V3-S08 |
| V1 Phase 6 Mission Engine → Go | split between V3-S03/S04/S09; foundation/limited scope already done, expansion remains |
| V1 Phase 7 Mission Persistence | V3-S10 |
| V1 Phase 8 Core Service + Restartable UI | V3-S10 |
| V1 Phase 9 Failure Injection | V3-S11 |
| V1 Phase 10 SITL Endurance | V3-S12 |
| V2 F0/F1 | V3-S01 |
| V2 F2/F3 | V3-S03 |
| V2 F4/F5 | V3-S04 |
| V2 F6 | V3-S04 + partial V3-S10 |
| V2 F7 | partial V3-S11, DONE for guarded scope |
| V2 F8 | partial V3-S12, DONE for guarded repeated SITL scope |
| V2 H1/M1 audit repair | V3-S05 |
| V2 F9A | V3-H01 |
| V2 F9B | V3-H02 |
| old Release F10 | V3-R01 |

---

# 19. Remaining Work — Short Answer

If the question is simply **"what is actually left?"**, the answer as of 2026-09-06 is:

1. **Record/complete the separate S09 PRE-FLIP administrative freeze** while keeping all deferred S09 live tokens OFF. The independent review is already PASS; do not redo S09 implementation without a proven defect.
2. **Finish V3-H02**: 5.4 Core-owned waypoint → 5.5 WAIT/HOLD → 5.6 Cancel → 5.7 controlled Core/GCS-loss failsafe → 5.8 battery/link preemption → 5.9 manual/STOP/E-STOP takeover, plus required reconnect/restart evidence.
3. After H02 base PASS, promote **S09 authority scopes one by one** through the full validation ladder: Multi GROUPED → SEPARATE → SWARM_LEADER → Payload → WAVE → Return Policy live validation.
4. Run the **full-system integration + multi-drone/full-system endurance + hardware matrix** required by the intended release scope.
5. Run the **controlled real-flight matrix** only after its prerequisites are green.
6. **V3-R01** — explicit full-production release review; remains LOCKED until the above evidence exists.

S07, S08, S10, S11 and the current S12 operational gate are already complete and must not be reopened merely because older text still lists them as future work.

---

# 20. Execution Order

Canonical order from the current state:

```text
V3-S01–S08  Software foundation                         ✅ DONE
        ↓
V3-S09      PRE-FLIP implementation + independent review ✅ PASS / FREEZE RECORD PENDING
        ↓
V3-S10      Persistence / restartability                ✅ COMPLETE
        ↓
V3-S11      Failure injection                           ✅ COMPLETE
        ↓
V3-S12      Current SITL endurance gate                 ✅ COMPLETE
        ↓
V3-H01      Hardware bench preparation                  ✅ DONE
        ↓
V3-H02      Actual FC bench                             🟠 IN PROGRESS — 5.1–5.3 PASS
        ↓
S09 live scopes, one at a time after H02 base PASS      🔒 OFF UNTIL VALIDATED
        ↓
Full integration / hardware / controlled-flight matrix  ⏳ FUTURE VALIDATION
        ↓
V3-R01      Full Production Release Review              🔒 LOCKED
```

`V3-H01` is already DONE and sits alongside this chain as prepared hardware tooling.

`V3-S07` and `V3-S08` are closed green. `V3-S09` has PRE-FLIP A–F implementation with a Fresh Independent PRE-FLIP Re-review PASS at 0 Critical / 0 High; it is ready for the separate administrative freeze, is not yet marked frozen, and grants no new live authority.

---

# 21. Global V3 Safety Invariants

All future work must preserve:

- exactly one flight/navigation authority per target at a time
- Go failsafe remains authoritative for its current safety scope
- unsupported mission scopes keep the verified legacy Python executor until separately migrated
- unknown/active Core mission slot blocks legacy execution fail-closed
- emergency/takeover is never weakened for performance
- Mission Engine commands go through command.Service / safety envelope
- UI telemetry coalescing never becomes safety telemetry throttling
- UI restart never replays StartMission automatically
- Core restart never auto-resumes persisted/non-terminal flight
- new retry/dedup enforcement is introduced only after command-class semantics are proven
- no actual-FC or real-flight readiness claim without V3-H02 evidence

---

# 22. Canonical Documentation Rule

From now on:

### Roadmap / current status

Use:

`docs/V3_MASTER_ROADMAP.md`

### Historical implementation/evidence

Keep and consult as needed:

- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md`
- `docs/PHASE_3_TO_10_CONTINUOUS_EXECUTION_PLAN.md`
- `docs/LEGACY_BEHAVIOR_PARITY.md`
- `docs/MISSION_FAILURE_INJECTION_F7.md`
- `docs/MISSION_SITL_F8_GATE.md`
- `docs/HARDWARE_BENCH_F9_GATE.md`

Historical V1/V2 numbering in those files is not the current execution naming system.

When reporting status in future, use examples such as:

- `V3-S06 DONE — TelemetryStore + deterministic Qt/gRPC lifecycle`
- `V3-S07/S08 DONE — source review plus targeted/full frozen-source regression green`
- `V3-S09 FINAL REVIEW PASS / READY TO FREEZE — PRE-FLIP A–F exists; no authority flip`
- `V3-H02 IN PROGRESS — actual FC 5.1–5.3 PASS; 5.4–5.9 pending`
- `V3-R01 LOCKED`

Do not report only `F8`, `Phase 8`, or `F10` without the V3 ID.
