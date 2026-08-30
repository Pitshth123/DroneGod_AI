# DroneGod_AI — Full Production Validation Plan

Date: 2026-08-30
Status: **S09 FINAL REVIEW PASS — READY FOR PRE-FLIP FREEZE / H02 AFTER FREEZE**

## Recall / Trigger Phrase

If the user says exactly or approximately:

> **“ต้องการเทสต์ FC ตามโรดแมป”**

then open and use **this file** (`docs/V3_FULL_PRODUCTION_VALIDATION_PLAN.md`) as the active execution plan.
Do not restart S01–S12, do not invent a new validation order, and do not open multiple new authority scopes at once.
Start from the next applicable step in this roadmap, beginning with the Actual FC bench validation when the hardware is ready.

Active project: `C:\Users\PC\Desktop\v2 swam\DroneGod_AI`
Canonical roadmap: `docs/V3_MASTER_ROADMAP.md`

> Purpose: เก็บแผนพิสูจน์ระบบแบบครบเส้นทางตั้งแต่ Actual FC Bench ไปจนถึง Full Production Release เพื่อให้เมื่อ Flight Controller พร้อม สามารถเปิดเอกสารนี้แล้วเริ่มตามลำดับได้ทันที โดยไม่ต้องย้อนอ่านบทสนทนาเก่า
>
> หลักสำคัญ: **ห้ามเปิด authority ใหม่หลาย scope พร้อมกัน** ต้องเปิด/พิสูจน์ทีละ scope เพื่อรู้ root cause ชัดและรักษา no-dual-authority / failsafe / emergency invariants ที่ผ่านการทดสอบมาแล้ว

---

# 1. Current Baseline Before Hardware

Software/SITL foundation ที่ผ่านแล้ว:

- V3-S01–S08: COMPLETE
- V3-S09: **FINAL REVIEW PASS — 0 CRITICAL / 0 HIGH — READY FOR PRE-FLIP FREEZE / AUTHORITY NOT FLIPPED**
- V3-S10 Persistence + Restartability: COMPLETE
- V3-S11 Failure Injection Expansion: COMPLETE
- V3-S12 Current Operational Endurance Gate: COMPLETE
- V3-H01 Hardware Bench Preparation: DONE

Current live mission authority remains only:

- `core-single`
- `core-single-wait`

The following remain OFF until separately validated:

- `core-grouped-multi`
- `core-separate`
- `core-swarm-leader`
- `core-wave`
- `core-payload`

Current administrative gate before hardware:

- Fresh Independent PRE-FLIP Re-review is complete with `FINAL REVIEW PASS` and **0 Critical / 0 High**.
- Perform the separate administrative **S09 PRE-FLIP software baseline freeze**.
- Freeze is evidence/baseline only and does **not** authorize any S09 authority flip.
- Do not start H02 as an S09 expansion task and do not widen live authority during freeze.

Intentionally unresolved:

- SWARM_LEADER operator-takeover / leader-succession policy = **RATIFIED / PRE-FLIP IMPLEMENTED / FRESH REVIEW PASS (2026-08-30); later live authority validation pending**
- S09-F Return Policy = **RATIFIED / PRE-FLIP IMPLEMENTED / FRESH REVIEW PASS; later live authority validation pending**

S12 evidence already completed:

- 6h idle-connected PASS
- 216,002 telemetry
- 723 RPC
- 0 RPC errors
- 0 stream errors
- 0 UI stalls
- 15m reconnect-churn PASS
- 15 reconnect cycles
- 15m client-lifecycle PASS
- 75 create/snapshot/close cycles

One non-blocking follow-up remains: one transient `SQLITE_BUSY` registry-upsert warning during the 6h run without service interruption.

---

# 2. Production Goal

Goal is not merely “first flight works”.

Goal is to reach **Full Production**, meaning every intended mission scope must have evidence that:

- function works correctly;
- Core/Python never command the same drone concurrently;
- stale callback / stale command cannot revive an old mission;
- duplicate Start/command does not execute twice;
- Cancel becomes terminal;
- manual takeover wins;
- STOP/KILL/E-STOP cannot be blocked by normal mission work;
- battery/link failsafe wins over mission logic;
- UI disconnect/restart does not duplicate commands;
- Core restart never auto-resumes a previous mission;
- actual FC behavior matches the software safety contract;
- every production-enabled scope has SITL + failure + hardware + controlled-flight evidence.

---

# 3. Overall Validation Order

```text
CURRENT SOFTWARE/SITL BASELINE ✅
        ↓
H02-A — Actual FC Base: Single + WAIT
        ↓
S09-A — Multi-drone GROUPED
        ↓
S09-B — SEPARATE
        ↓
RATIFIED SWARM takeover: exclude follower only; promote the next eligible leader
        ↓
S09-C — SWARM_LEADER
        ↓
S09-E — Payload / Servo A/B
        ↓
S09-D — WAVE
        ↓
VALIDATE + EXPLICITLY FLIP S09-F Return Policy (separate task, if approved)
        ↓
FULL-SYSTEM INTEGRATION
        ↓
MULTI-DRONE / FULL-SYSTEM ENDURANCE
        ↓
FULL HARDWARE MATRIX
        ↓
CONTROLLED REAL-FLIGHT MATRIX
        ↓
R01 — FULL PRODUCTION RELEASE REVIEW
```

Important: do not skip ahead just because later code already exists in PRE-FLIP form.

---

# 4. Mandatory Validation Pattern For Every New Authority Scope

Every S09 scope must follow this exact pattern:

```text
Legacy / current behavior characterization
        ↓
Inspect existing PRE-FLIP implementation
        ↓
Repair only actual gaps
        ↓
Authority gate ON in SITL only
        ↓
Prove Python is no longer concurrent authority for that scope
        ↓
Happy-path SITL
        ↓
Failure injection
        ↓
Restart / reconnect / stale-command tests
        ↓
Emergency / manual takeover / failsafe tests
        ↓
Soak / repeat cycles
        ↓
Actual FC bench
        ↓
Controlled real flight
        ↓
Freeze evidence
        ↓
Only then consider retiring Legacy executor for that scope
```

Never bulk-flip A/B/C/D/E together.

---

# 5. STEP 1 — V3-H02-A Actual FC Base Validation

## Goal

Prove the already-mature `core-single` and `core-single-wait` paths against the **actual Flight Controller** before expanding mission authority.

## Physical boundary

- Propellers removed or otherwise physically safe under the team bench procedure.
- Use HIL/bench guard only.
- No real flight in H02.
- Capture original FC parameters before changing anything.

## Required tests

### 5.1 FC parameter/config snapshot

Verify actual FC configuration for:

- accepted GCS system ID;
- GCS/Core loss failsafe;
- pre-arm checks;
- battery failsafe/action;
- `FS_OPTIONS`;
- fence;
- relevant link/failsafe parameters.

Do not modify first; archive baseline first.

### 5.2 Real telemetry timing

Measure:

- telemetry rate;
- packet/source age;
- gaps;
- link quality;
- LinkWarn/LinkLost timing margin;
- reconnect behavior.

### 5.3 COMMAND_ACK timing

While disarmed and bench-safe:

- observe first;
- guarded mode change;
- measure ACK accepted/rejected/timeout;
- restore original mode.

### 5.4 Core-owned single waypoint

Verify:

- Core sends the mission GOTO;
- Python does not send duplicate GOTO;
- one command per waypoint;
- actual FC mode/ACK behavior is correct;
- arrival progression is correct.

### 5.5 Core-owned WAIT/HOLD

Verify:

- WAIT entered by Core;
- HOLD belongs to Core path;
- close UI while WAIT active;
- Core retains run;
- reopen/query same run;
- no duplicate Start/GOTO/HOLD.

### 5.6 Cancel

Test:

- Cancel during transit;
- Cancel near waypoint boundary;
- later telemetry cannot restart progression.

### 5.7 Core/GCS loss — mandatory blocker

In a bench-safe state:

- stop Core;
- confirm actual FC performs the approved onboard failsafe behavior;
- restart Core;
- old mission must not auto-resume;
- no stale command may reappear.

### 5.8 Battery/link preemption

Where bench-safe:

- induce alarm/fault conditions;
- fleet failsafe remains sole flight-action owner;
- mission becomes INTERRUPTED;
- no later mission GOTO/HOLD.

### 5.9 Manual / STOP / E-STOP takeover

Verify:

- operator takeover gets authority promptly;
- Core mission becomes terminal/interrupted before stale progression can continue;
- no auto-resume after takeover.

## H02-A Exit Gate

All items above must pass before opening a new S09 live authority scope.

---

# 6. STEP 2 — S09-A Multi-drone GROUPED

## Why this goes first

It is the closest expansion from the already-proven single GROUPED authority.

## Core behavior to prove

For each shared waypoint:

- each drone receives its formation-offset target;
- per-drone altitude is preserved;
- dispatch order matches characterized legacy behavior;
- each drone arrival is judged against its own offset target;
- shared index advances only after all participants reach the barrier;
- exactly one command per participant per index;
- Python is not also issuing mission GOTO.

## Failure / contention tests

- one participant telemetry stale;
- one participant disconnects;
- one GOTO rejected;
- simultaneous arrivals;
- one drone reaches early;
- Cancel while some drones already arrived;
- STOP ALL during multi-target dispatch;
- KILL during multi-target dispatch;
- RTL/LAND/HOLD takeover;
- battery/link failsafe;
- duplicate Start;
- UI reconnect/restart;
- Core restart/no auto-resume;
- stale callback after terminal state;
- no dual authority.

## Required ladder

```text
PRE-FLIP review
→ SITL authority
→ failure injection
→ repeat/soak
→ multi-FC bench
→ controlled multi-drone flight
→ freeze evidence
```

Only after this passes may Multi GROUPED be considered production-enabled.

---

# 7. STEP 3 — S09-B SEPARATE

## Core behavior to prove

Each participant has its own route and current index.

Must prove:

- independent progression;
- raw waypoint targets (no GROUPED offset);
- per-drone altitude;
- one drone may finish while others continue;
- no duplicate per-drone GOTO;
- participant/route bijection is exact.

## Collision / route-conflict safety

Mandatory:

- route crossing at same/near altitude rejected;
- altitude separation boundary verified;
- minimum route distance boundary verified;
- current-position → WP1 crossing included;
- stale/no-GPS/invalid current position fails closed;
- missing telemetry cannot silently bypass preflight.

## Failure tests

- one drone stalls;
- one finishes early;
- one loses telemetry;
- per-drone command rejection;
- takeover on one participant;
- whole mission Cancel;
- battery/link failsafe on participant;
- stale callback;
- duplicate Start;
- restart/reconnect;
- no dual authority.

Then:

```text
SITL
→ failure injection
→ soak
→ actual multi-FC bench
→ controlled SEPARATE flight
```

---

# 8. STEP 4 — SWARM_LEADER Takeover / Succession Policy (RATIFIED)

The operator-takeover / leader-succession behavior for a leader-driven mission is
**RATIFIED and implemented PRE-FLIP (2026-08-30)**. This remains PRE-FLIP evidence
only and grants **no live authority**: `core-swarm-leader` stays OFF and
`missionAuthorityMode` recognizes only `core-single` / `core-single-wait`.

Ratified policy:

- **Follower takeover — exclude only that follower.** The taken-over follower leaves
  the formation; the leader plus remaining followers continue the **same run** at the
  same route/index. Formation is rebound to the remaining members.
- **Leader takeover/removal — promote the next eligible participant.** The old leader
  is excluded and the next eligible participant in **original participant order** is
  promoted; the run_id and current route/index are preserved.
- **No auto-rejoin.** An excluded participant never rejoins the current run — not via
  telemetry recovery, reconnect, or UI restart.
- **No successor / below minimum ⇒ INTERRUPTED.** If no eligible successor (or the
  minimum formation) remains, the mission becomes INTERRUPTED with `Active=false`,
  `Authority=false`, and no invented flight action.
- **No automatic SWARM_LEADER → SINGLE conversion.**

Example: D1=Leader, D2/D3/D4=Followers. Operator takes D3 → D3 excluded, D1 still
leads, D2/D4 continue the same run. Operator later takes D1 → D1 excluded, D2 promoted
(original order), same run/index; formation rebound to D2.

Excluded members hold structurally important slots without a stand-in: the remaining
formation geometry is recomputed from the surviving members under the existing
separation/collision checks; the removed slot is not backfilled by another aircraft.

Implementation: `mission.Engine.BeginSwarmOperatorTakeover` / `CompleteSwarmOperatorTakeover`
(membership + succession revision), `api.applySwarmMissionTakeoversLocked` (runs before
the generic ownership cancel), and `swarm.Manager.RebindMissionMembership` (atomic
leader/member swap; stale formation generation). Live enablement still requires a
separate fresh review plus SITL/hardware authority validation.

---

# 9. STEP 5 — S09-C SWARM_LEADER

## Ownership model

```text
Mission Engine → Leader only
Swarm Manager  → Followers only
```

Must prove there is never same-drone overlapping navigation authority.

## Tests

- only leader gets mission GOTO;
- follower arrival does not advance mission;
- leader arrival drives progression;
- mission never commands followers;
- formation never commands leader incorrectly;
- leader failure;
- follower failure;
- approved follower takeover policy;
- leader takeover;
- formation start/stop;
- form-up transitions;
- RETURN;
- LAND;
- STOP/KILL during form-up;
- STOP/KILL during RETURN/LAND;
- stale follower write cannot occur after revoke;
- stale RETURN/LAND write cannot occur after revoke;
- battery/link failsafe;
- UI reconnect/restart;
- Core restart/no auto-resume;
- no deadlock / emergency blocked behind loop teardown.

Then:

```text
SITL
→ failure injection
→ soak
→ actual multi-FC bench
→ controlled SWARM flight
```

---

# 10. STEP 6 — S09-E Payload / Servo A/B

Do this before WAVE because it is a smaller, more isolated authority/action scope.

## Expected sequence

```text
Waypoint arrival
→ HOLD
→ SERVO SET
→ dwell ~2 s
→ SERVO RELEASE
→ route progression
```

## Mandatory tests

- HOLD accepted/rejected;
- SET accepted/rejected;
- lost/uncertain SET result;
- every participant remains a release obligation once action starts;
- RELEASE exactly once per participant;
- duplicate callback cannot double-release;
- Cancel before SET;
- Cancel after SET;
- failsafe during action;
- stale action token;
- old action cannot mutate newer action;
- cleanup failure operator-visible;
- no route advance after Cancel/failsafe;
- no payload remains engaged merely because ACK/result was lost.

## Hardware approach

Use bench-safe dummy load / servo test fixture before any real payload.

Then:

```text
SITL/model validation
→ command/ACK integration
→ failure injection
→ payload bench fixture
→ actual aircraft bench
→ controlled payload flight
```

---

# 11. STEP 7 — S09-D WAVE

WAVE goes later because it orchestrates multiple lower-level subsystems.

## Expected high-level sequence

```text
Group 1: Takeoff → Route → Return/Land → Disarmed
        ↓
Group 2: Takeoff → Route → Return/Land → Disarmed
        ↓
...
```

## Mandatory tests

- group order;
- minimum online-group validation;
- next group cannot start before landed + disarmed proof;
- RTL active prevents premature advancement;
- timeout aborts whole WAVE;
- Cancel stops next-group launch;
- failsafe stops next-group launch;
- stale timer/callback ignored;
- previous-group token cannot affect new group;
- UI disconnect/reconnect;
- Core restart/no auto-resume;
- one group failure;
- emergency during route;
- emergency during group transition;
- operator takeover;
- no duplicate group start;
- no dual authority with underlying GROUPED executor.

WAVE should reuse already-proven GROUPED execution; do not create a second independent navigation path.

Then:

```text
SITL sequential groups
→ failure injection
→ soak
→ hardware multi-group bench where feasible
→ controlled WAVE flight
```

---

# 12. STEP 8 — S09-F rtl_after

Current status: **RETURN POLICY RATIFIED / PRE-FLIP IMPLEMENTATION COMPLETE;
AUTHORITY/LIVE VALIDATION PENDING.** No production or hardware authority was enabled.

Validated software contract:

- `rtl_after` is compatibility input resolved during plan building, never direct authority.
- Single / WAIT / GROUPED: `RTL_ALL_AFTER_MISSION`, only after natural mission completion.
- GROUPED waits for every participant; SEPARATE defaults to all routes complete.
- SEPARATE per-route early Return is disabled pending a return-corridor contract.
- SWARM_LEADER hands the whole return to the existing Swarm Manager; no leader-only RTL.
- WAVE retains exactly one WAVE-managed return/land/disarm path.
- Payload cannot trigger Return before the parent mission completes.
- Cancel, failsafe, manual takeover, STOP/KILL, failure, and recovery never revive Return.
- A Core restart from pending/returning evidence produces recovery-required state and zero commands.
- Automatic RTL uses command safety/audit/idempotency and per-target final-write guards;
  ACK delay does not hold the mission ownership lock.

Before any explicit authority flip, execute:

```text
independent frozen-source review
→ targeted SITL Return scenarios and failure injection
→ SITL
→ endurance/soak for the exact scope
→ hardware bench
→ controlled flight
```

The software gate is intentionally in-process/test-only. The live token set must
remain `core-single` / `core-single-wait` until a separate reviewed flip task; the
five deferred S09 tokens remain OFF. Actual FC validation belongs to V3-H02 and
controlled flight remains locked behind the full release plan.

Recorded software evidence (2026-08-30): exact targeted backend scope 6/6
packages passed; full backend scope 29/29 packages passed; `go vet ./...` passed
with zero diagnostics; affected frontend regression scope 378/378 tests passed
(one pytest cache-permission warning only). These results do not satisfy or waive
any SITL, soak, hardware-bench, FC, or controlled-flight gate above.

---

# 13. STEP 9 — Full-System Integration Matrix

After each scope passes individually, run combined scenarios to expose cross-subsystem bugs.

Example chains:

```text
Multi GROUPED
→ WAIT
→ Payload
→ Cancel
→ new mission
→ SEPARATE
→ reconnect
→ SWARM
→ manual takeover
→ WAVE
→ emergency
```

Required integrated faults:

- duplicate command;
- delayed ACK;
- lost ACK;
- stale callback;
- stale telemetry;
- out-of-order telemetry;
- telemetry burst;
- UI freeze/restart;
- Core restart;
- FC disconnect/reconnect;
- DB persistence/recovery;
- STOP/KILL contention;
- battery/link failure;
- multi-drone partial failure;
- leader/follower failure;
- payload cleanup during interruption;
- WAVE transition interruption.

Expected Safe State must be declared before every failure test.

---

# 14. STEP 10 — Full Production Endurance

Do not rerun 6h after every small change. Use targeted tests during development.

Before final production release, run an explicit full-system endurance campaign.

Suggested ladder:

```text
5-drone telemetry / mission load
↓
10-drone telemetry / mission load
↓
Multi GROUPED cycles
↓
SEPARATE cycles
↓
SWARM cycles
↓
Payload cycles
↓
WAVE cycles
↓
reconnect / client lifecycle
↓
2h representative mission soak
↓
6h final representative soak if required by release review
```

Measure at minimum:

- Python RSS/private bytes;
- Go/Core RSS;
- threads/goroutines where exposed;
- UI heartbeat/stalls;
- telemetry ingest/age;
- RPC latency/errors;
- reconnect count;
- mission transitions;
- duplicate/suppressed commands;
- stale authority;
- log growth;
- DB growth/locking;
- resource leaks.

The prior transient `SQLITE_BUSY` should be specifically watched during this final integrated campaign.

---

# 15. STEP 11 — Full Hardware Matrix

After each software scope is green, validate the intended production hardware configuration.

Hardware evidence should cover:

- actual FC firmware/config;
- real transport/link;
- real telemetry timing;
- real COMMAND_ACK timing;
- GCS/Core-loss onboard failsafe;
- battery failsafe;
- manual takeover;
- E-STOP/KILL;
- multi-FC synchronization/behavior;
- servo/payload hardware where applicable;
- reconnect/restart behavior;
- no auto-resume after Core restart.

SITL never substitutes for this gate.

---

# 16. STEP 12 — Controlled Real-Flight Matrix

Do not jump directly to one all-feature flight.

Use a controlled ladder:

```text
Flight 1  — Single waypoint
Flight 2  — Single + WAIT
Flight 3  — Multi GROUPED
Flight 4  — SEPARATE
Flight 5  — SWARM_LEADER
Flight 6  — Payload
Flight 7  — WAVE
Flight 8  — Integrated mission chain
Flight 9  — controlled takeover/failure scenarios where safely executable
```

Every step requires:

- approved scope;
- pre-flight safety review;
- evidence capture;
- no unresolved CRITICAL/HIGH finding;
- explicit stop criteria;
- rollback/fallback procedure.

A failure in one flight step blocks promotion of that scope but does not justify rewriting already-passed scopes.

---

# 17. R01 — Full Production Release Criteria

Full Production Release should require all intended production scopes to have:

1. Defined behavior contract.
2. PRE-FLIP review complete.
3. SITL authority evidence.
4. No-dual-authority proof.
5. Failure-injection evidence.
6. Restart/reconnect evidence.
7. Emergency/failsafe/takeover evidence.
8. Soak/repeat evidence.
9. Actual FC/hardware evidence.
10. Controlled real-flight evidence.
11. No unresolved CRITICAL/HIGH safety finding.
12. Explicit release review approval.

Do not use “all tests green” alone as the definition of production readiness.

---

# 18. Hard Safety Invariants — Never Weaken To Make Tests Green

- KILL remains strongest.
- STOP ALL must not be overwritten by stale work.
- Emergency/takeover cannot wait behind long normal FC ACK work.
- No same-drone dual navigation authority.
- Mission GOTO/HOLD goes through `command.Service` and safety guards.
- Failsafe owns its flight action; mission only becomes interrupted/terminal.
- Cancelled/preempted stale callbacks cannot execute later.
- `request_id` retry must not execute underlying command twice.
- Correlation metadata is observability only, never authority.
- UI restart must not Start a mission again.
- Core restart must not auto-resume a previous mission.
- Persistence is recovery evidence, not flight-resume authority.
- Invalid/stale telemetry must fail closed for authority decisions.
- Do not retire a Legacy executor until the replacement scope has completed its evidence ladder.
- Do not enable a new live authority token merely because PRE-FLIP unit tests pass.

---

# 19. Exact Next Step When FC Becomes Available

When the user says the actual FC is ready:

1. Open this file: `docs/V3_FULL_PRODUCTION_VALIDATION_PLAN.md`.
2. Open `docs/HARDWARE_BENCH_F9_GATE.md`.
3. Confirm actual hardware bench safety (props removed / approved bench setup).
4. Capture original FC parameter snapshot before modifying anything.
5. Start **STEP 1 — V3-H02-A Actual FC Base Validation** using only `core-single` / `core-single-wait`.
6. Do not flip S09 authority during the initial hardware baseline.
7. Record evidence and exact failures.
8. Repair only proven defects.
9. After H02-A base is green, proceed to S09-A Multi GROUPED according to this document.

---

# 20. Short Human Summary

We are not waiting for the FC because software work was incomplete. The software/SITL architecture has already been heavily tested. The remaining purpose of hardware validation is to prove that the safety assumptions actually hold when the real FC, real MAVLink timing, real failsafe configuration, and real command ACK behavior are involved.

After that baseline is proven, the advanced S09 scopes are promoted one at a time. This prevents a bug in Multi/Swarm/WAVE/Payload from being mixed together and preserves the safety properties already established over the previous migration/testing campaign.

**Next physical gate when FC is ready: V3-H02-A Actual FC Base — Single + WAIT first.**
