# Architecture Migration Progress

> สมุดส่งเวรหลัก (handoff notebook) ของงาน Low-Risk Architecture Migration
> ถ้า session จบ / quota หมด / เปลี่ยน AI → เปิดไฟล์นี้แล้วทำต่อได้ทันที
> ไฟล์นี้สำคัญกว่า `CODEX_CHANGES.md` (นั่นเก็บของที่ทำเสร็จ; ไฟล์นี้เก็บงานค้าง + exact next step + เหตุผล)

## Current Status
- **Active Roadmap:** `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`
- **Current F-Phase:** **F4 Stage A — IN PROGRESS.** part 1 (Go observation pipeline) ✅ DONE / inert / tested;
  part 2 (Python shadow-submit) = NOT STARTED. F4 overall ยังไม่จบ (authority ยัง Python, ไม่ส่ง command)
- **V1 Phase 3 Telemetry Store:** **NOT DONE / DEFERRED UNTIL AFTER SAFETY FAST-TRACK**
- **Reason:** เลือก Safety Fast-Track V2 เพื่อเร่งย้าย mission authority ออกจาก Python
  ก่อนการทดสอบบินจริง; V1 ยังไม่ถูกยกเลิก
- **Completed checkpoint:** Phase 0–2C ✅ + **F0 ✅ + F1 ✅ (contract) + F2 ✅ (shadow engine) + F3 ✅ (Mission RPC)**
- **Last full frontend:** **965 passed** (Phase 2C); F0 targeted 181. **Go: `go test ./...` ผ่านทุก package (mission 28, api 15)**
- **Last updated:** 2026-08-27 (Opus 4.8 High: F1 → F2 → F3)
- งาน migration ยัง **ไม่ย้าย flight authority จริง** — F3 = RPC boundary + shadow; Python ยัง execute จริง (F4 = cutover)

## Current Goal
F1→F2→F3 เสร็จ. Go มี Mission RPC boundary (`StartMission`/`CancelMission`/`GetMissionState`)
backed by shadow `mission.Engine` — run identity + idempotency + stale-safe cancel + reconnect query,
โดยไม่ส่ง command และไม่แตะ Python executor.
**F4 Stage A part 1 (Go observation pipeline) เสร็จแล้ว** — telemetry feed → shadow `Observe`/`Poll`,
inert จนกว่าจะมี run (0 behavior change). งานถัดไป = **Stage A part 2: Python shadow-submit**
(best-effort `start_mission`/`cancel_mission` จาก app.py, ยัง shadow, ไม่เปลี่ยน behavior).
จากนั้น Stage B = authority cutover (RISK สูงสุด — ห้าม dual authority). **หยุด/รอไฟเขียวก่อนแต่ละ stage**

## Active Roadmap

`REAL_FLIGHT_SAFETY_FAST_TRACK_V2`

V1 Phase 3 Telemetry Store, telemetry reader migration, render coalescing, presenter เพิ่ม,
system-wide Command Gateway/dedup, persistence และ cosmetic cleanup =
**DEFERRED UNTIL AFTER SAFETY FAST-TRACK**

## Flight Authority Matrix (V2 §10)
| Subsystem | Current Authority | Target Authority | Cutover Status |
|---|---|---|---|
| Plan build/edit | Python | Python | คงเดิม (UI) |
| Mission Start/Cancel | Python | Go (run_id) | **RPC boundary + run identity มีแล้ว (F3)**; Python ยังไม่เรียก (F4) |
| Mission state query | Python vars | Go GetMissionState | **RPC มีแล้ว (F3)**; UI ยังไม่ query (F6) |
| Waypoint progression | Python executor | Go Mission Engine | **SHADOW คำนวณได้ (F2)**; authority ยัง Python (F4) |
| **Arrival detection** | **Browser map JS (3.0m)** | Go (telemetry+radius) | **F4-A: telemetry feed → `Observe` wired (inert)**; UI ยัง authority จริง (F4-B) |
| WAIT / HOLD | Python (Qt 500ms poll) | Go state | **SHADOW WAIT+Poll มีแล้ว (F2)**; authority ยัง Python (F5) |
| Payload action A/B | Python | Go | NOT STARTED (F5/F6B) |
| GOTO/HOLD command | Python→command.Service | Mission Engine→command.Service | path พร้อม, authority ยัง Python |
| Failsafe (battery/link) | **Go Core** ✅ | Go Core | มีแล้ว (คงไว้) |
| Mission state storage | Python process memory | Go in-memory (V2) | NOT STARTED (F3/F6) |
| run identity | Python `itertools.count` (process-local) | Core-generated run_id | NOT STARTED (F3) |

## Protected Real-Flight Modes (crash-tolerant?)
- Waypoint (GROUPED): **NO** (ยังไม่ cutover — ห้ามอ้างว่า crash-safe)
- WAIT: **NO**
- Swarm formation: partial (Go ถือ formation loop + failsafe interlock แล้ว แต่ mission progression ยัง Python)
- WAVE: **NO** (DEFER — V2 §3.6, ทำเฉพาะถ้า flight รอบแรกต้องใช้ = F6B)
- Payload automation: **NO**

> ยังไม่มี mode ใดเป็น crash-tolerant — ทุก mission progression ยังตายพร้อม Python. นี่คือเป้าหมายของ F2→F6.

## Fast-Track F4 Checkpoint (IN PROGRESS — Stage A)

### CURRENT F-PHASE
- **F4 — Waypoint Execution Authority → Go.** RISK สูงสุด. ทำเป็น sub-stage:
  - **Stage A part 1 — Go observation pipeline → ✅ DONE / SAFE / RUNNABLE / inert**
  - **Stage A part 2 — Python shadow-submit (StartMission/Cancel best-effort) → IN PROGRESS (ChatGPT+MCP takeover)**
  - Stage B — authority flag + Python หยุดส่ง GOTO → **NOT STARTED** (RISK สูงสุด)
- **A2 guard:** shadow only; Python command sequence/authority must remain unchanged.
- **Stage B blocker discovered:** `MissionWaypoint.alt` เป็นค่าเดียวต่อ shared waypoint แต่ Python GROUPED ปัจจุบันเลือก altitude รายโดรน (`_last_alt`/`_alt_for`) ได้ — ต้องกำหนด altitude contract/compatibility ก่อน Go GOTO authority; ห้ามเดาค่าแล้ว cutover.
- **Last Safe Runnable Checkpoint = F4 Stage A part 1** (Go build + `go test ./...` green; เป็น additive/inert)

### DONE (Stage A part 1)
- เพิ่ม `mission.Engine.Active()` (cheap check)
- `internal/api/mission.go`: `runMissionObserver(ctx)` ticker 5Hz (200ms) → `observeMissionTick` →
  `observeFrom([]*pb.Telemetry)` = feed `Observe(id,lat,lon,altRel)` + `Poll()` ให้ shadow engine
- start observer goroutine ใน `Server.Serve` (ใช้ lifecycle ctx เดียวกับ graceful stop)
- **inert-in-production:** observer ทำงานเฉพาะเมื่อ `mission.Active()` = true; ปัจจุบันไม่มีใคร StartMission
  จาก Python → observer ไม่ทำอะไรเลยใน runtime จริง = **0 behavior change, 0 command**

### FILES CHANGED (Stage A part 1)
- `backend/internal/mission/engine.go` (+`Active()`)
- `backend/internal/api/mission.go` (+observer: `runMissionObserver`/`observeMissionTick`/`observeFrom`)
- `backend/internal/api/server.go` (+`go s.runMissionObserver(ctx)` ใน Serve)
- `backend/internal/api/mission_test.go` (+3 tests)
- **ไม่แตะ Python / proto / command path**

### TESTS RUN
- `go test ./internal/api/ ./internal/mission/` → ผ่าน (api 18, mission 28)
- `go test ./...` → ผ่านทุก package; `go vet` clean; `go build ./...` OK
- ครอบ: telemetry-driven GROUPED progression (far→WP0 รอครบ→advance→COMPLETED),
  inert-when-idle (nil-safe ไม่ panic), nil-position safe

### TEST RESULTS
- observation pipeline พิสูจน์แล้วว่า shadow engine ก้าวหน้าจาก telemetry จริงได้ (เมื่อมี run)
- ยังไม่ส่ง command (โครงสร้างเดิม `TestMissionHandlersIssueNoCommand` ยังผ่าน)

### DECISIONS
- **F4A-D1:** แยก `observeFrom(telems)` ออกจาก `s.mgr.Snapshot()` เพื่อ unit-test ได้โดยไม่ต้องสร้าง fleet.Manager จริง
- **F4A-D2:** observer เป็น read-only จาก `fleet.Manager.Snapshot()` (ไม่ hook telemetry ingest path — น้อย invasive)
- **F4A-D3:** ticker 200ms (5Hz) — พอสำหรับ shadow tracking, arrival radius 3m; ไม่กระทบ flight เพราะไม่ส่ง command
- **F4A-D4:** ทำ Go pipeline ก่อน Python submit แยกเป็น 2 ขั้น — keep Go milestone isolated/reviewable ก่อนแตะ app.py (WIP)

### RISKS / OPEN
- pipeline ยัง **ไม่ถูก trigger จริง** จนกว่า Stage A part 2 (Python เรียก StartMission ตอน `_wp_begin_execute`)
- Stage A part 2 จะแตะ `app.py` (WIP-heavy) → ต้องเป็น best-effort/daemon-thread/swallow-exception/มี flag ปิดได้
  และ **ห้ามเปลี่ยน behavior เดิม** (pure shadow submit, ไม่อ่าน response ไปสั่งอะไร)
- Stage B (authority) ยังห่าง — ห้ามทำจน Stage A เทียบ shadow ใน SITL ผ่าน

### EXACT NEXT STEP (F4 Stage A part 2 — Python shadow-submit)
1. เพิ่ม thin client method ใน `frontend/swarmgod_gui/core/grpc_client.py`:
   `start_mission(plan_proto, operation_id)`, `cancel_mission(run_id, request_id)`, `get_mission_state()`
2. ใน `app.py`: หลัง `_wp_begin_execute` เริ่มจริง → สร้าง `pb.MissionPlan` จาก route ปัจจุบัน แล้ว
   **fire-and-forget** `start_mission(...)` ใน daemon thread (swallow exception, มี flag `SWARMGOD_MISSION_SHADOW`)
   - ใช้ `_flight_run_id`/operation id เป็น operation_id เพื่อ idempotency
   - ที่ `_cancel_navigation`/`_abort_waypoint_execution`/`_wp_failsafe_interrupt` → fire `cancel_mission` (best-effort)
3. **ห้าม** อ่าน mission state กลับมาสั่งอะไร (ยัง shadow); Python ยัง execute เต็มเหมือนเดิม
4. เพิ่ม frontend test: `_wp_begin_execute` เรียก start_mission (mock client) โดยไม่กระทบ command sequence เดิม
5. SITL: start waypoint → ดูว่า Go `GetMissionState` ก้าวหน้าตาม Python (เทียบ shadow) → ปิด Stage A

### NEXT FILE TO OPEN (Stage A part 2)
- `frontend/swarmgod_gui/core/grpc_client.py` (เพิ่ม mission client methods)
- `frontend/swarmgod_gui/app.py` `_wp_begin_execute` (~7427) + cancel/abort/failsafe paths (hook shadow submit)
- แปลง route → `mission_pb2.MissionPlan` (มี `MISSION_MODE_*`, `wait_seconds`)

### NEXT METHOD TO MODIFY (Stage A part 2)
- Python `_wp_begin_execute`: หลัง set executor state → fire shadow `start_mission` (daemon, guarded)
- Python cancel/abort/failsafe: fire shadow `cancel_mission`

### NEXT TEST TO WRITE (Stage A part 2)
- frontend: mock CoreClient — ยืนยัน `_wp_begin_execute` เรียก `start_mission` 1 ครั้ง และ command GOTO เดิมไม่เปลี่ยน
- frontend: shadow submit ล้มเหลว (client raise) → ไม่กระทบ execution เดิม (swallow)

## Fast-Track F3 Checkpoint

### CURRENT F-PHASE
- **F3 — Mission RPC + Run ID + Start/Cancel/Query Guard → ✅ DONE / SAFE / RUNNABLE**
- **Last Safe Runnable Checkpoint = F3** (Go build + `go test ./...` green; frontend imports OK)

### DONE
- **Proto toolchain แก้แล้ว:** ไม่มี `protoc` แยก แต่ `python -m grpc_tools.protoc` (libprotoc 35.1 = version เดิม)
  regen ได้ทั้ง Go + Python — regen แล้ว 5/6 Go stubs identical, ต่างแค่ comment stale ใน service_grpc (drift เดิม)
  → **วิธี regen: `python -m grpc_tools.protoc -I proto --go_out=... --go-grpc_out=... --python_out=... --grpc_python_out=... proto/swarmgod/v1/*.proto`**
- สร้าง `proto/swarmgod/v1/mission.proto` — run-based mission: `MissionPlan`/`MissionRoute`/`MissionWaypoint`
  (มี `wait_seconds`), enum `MissionMode`/`MissionWpAction`/`MissionRunState`, `Start/Cancel/GetMissionState` messages
- เพิ่ม 3 RPC ใน `service.proto`: `StartMission`/`CancelMission`/`GetMissionState` (+ import mission.proto)
- regen Go + Python stubs (additive, ไม่ churn command/common/swarm/telemetry)
- engine: เพิ่ม `StartOp(plan, operationID)` + `Run.OperationID` — idempotency ตาม operation_id (contract B4)
- wire `mission.Engine` เข้า `internal/api/server.go` (field + `New`) + handlers ใน `internal/api/mission.go`
  (proto↔domain conversion; **handlers ไม่แตะ `s.cmd`/`s.swarm` เลย = ไม่ส่ง command**)

### FILES CHANGED (F3)
- `proto/swarmgod/v1/mission.proto` (ใหม่), `proto/swarmgod/v1/service.proto` (+import +3 RPC)
- `backend/gen/swarmgod/v1/mission.pb.go` (ใหม่), `service.pb.go` + `service_grpc.pb.go` (regen)
- `frontend/swarmgod_gui/gen/swarmgod/v1/mission_pb2*.py` (ใหม่), `service_pb2*.py` (regen) — **generated เท่านั้น, ไม่แตะ Python source**
- `backend/internal/mission/engine.go` (+`StartOp`/`OperationID`), `engine_test.go` (+op-id test)
- `backend/internal/api/server.go` (+`mission` field, init), `mission.go` (ใหม่, handlers), `mission_test.go` (ใหม่)

### TESTS RUN
- `go test ./internal/mission/ ./internal/api/` → **ผ่าน** (mission 28, api 15 รวม mission handlers 8)
- `go test ./...` → **ผ่านทุก package**; `go vet` clean; `go build ./...` OK
- Python smoke: `import swarmgod_gui.app` OK, `grpc_client`/`mission_pb2`/service stubs import OK (regen ไม่ทำ frontend พัง)
- ไม่รัน full Qt suite (F3 ไม่แตะ Python source; generated stubs additive)

### TEST RESULTS (F3 exit criteria ✅)
- duplicate Start (same operation_id) → run เดิม ✅
- duplicate/stale Cancel → no-op ไม่แตะ run ✅ (+ idempotent)
- reconnect Query → คืน run_id/state/plan/participants ✅
- **no flight command** → `TestMissionHandlersIssueNoCommand` ✅ (authority ยัง Python)

### DECISIONS
- **F3-D1:** ใช้ RPC ใหม่ (`StartMission`/`CancelMission`/`GetMissionState`) แทน extend `MissionControl`/`UploadMission` เดิม
  (per-drone, ไม่มี run identity) — ชัดกว่า, ไม่แตะ roadmap RPC เดิม
- **F3-D2:** `mission.proto` แยกไฟล์ (ไม่ยัดใน command.proto) — boundary ชัด, review ง่าย
- **F3-D3:** idempotency ของ Start ผูก `operation_id` (retry/reconnect) + fallback `plan_id`; ต่างจาก command `Idempotent` 60s window
- **F3-D4:** F3 **ยังไม่ wire telemetry→`Observe`** และ Python ยังไม่เรียก RPC ใหม่ — เจตนา (authority ยัง Python จน F4)
- **F3-D5:** regen ใช้ `grpc_tools.protoc` (bundled protoc) เพราะไม่มี `protoc` แยก — บันทึกไว้ให้คนต่อไป

### RISKS / OPEN (ยกไป F4)
- Go server มี mission engine แต่ **ยังไม่ถูกป้อน telemetry** → Snapshot จะค้างที่ RUNNING index 0 จนกว่า F4 wire `Observe`
- F4 = cutover จริง: ต้องมี authority flag (dev/SITL), Python หยุดส่ง GOTO, ห้าม dual authority (STOP condition)
- arrival detection ยังอยู่ browser JS ในเส้นทางจริง — F4 ต้องสลับมาใช้ `Observe` (shadow เทียบก่อน)

### EXACT NEXT STEP (F4 — ยังไม่เริ่ม, รอไฟเขียว, RISK สูงมาก)
1. อ่าน contract PART B (B7 preemption, B8 cutover) + V2 F4 section (safety architecture)
2. **Stage A (shadow-in-server):** wire fleet telemetry → `s.mission.Observe(id,lat,lon,alt)` + `Poll()` loop
   → เทียบ Go shadow state กับ Python executor ใน SITL (ยังไม่ส่ง command)
3. **Stage B (authority flag):** เพิ่ม flag เปิด Go waypoint authority เฉพาะ dev/SITL; Mission Engine ส่ง GOTO
   **ผ่าน `command.Service`/safety เท่านั้น** (ห้าม bypass); Python executor หยุดส่ง GOTO เมื่อ flag on
4. Scope แรก = GROUPED single route, ไม่มี WAVE/payload; SWARM ให้ formation loop คุม follower เหมือนเดิม
5. tests: kill Python → Go advance ต่อใน SITL; Cancel หยุด; stale Python callback ไม่ส่ง GOTO; safety reject → FAILED
6. **STOP ทันทีถ้า:** Python+Go ส่ง mission command พร้อมกัน / bypass safety / failsafe เปลี่ยนโดยไม่มี test

### NEXT FILE TO OPEN (F4)
- `backend/internal/api/server.go` (telemetry subscription/aggregator) เพื่อ feed `Observe`
- `backend/internal/mission/engine.go` (เพิ่ม authority-mode + command-issue hook ผ่าน interface ไป command.Service)
- `frontend/swarmgod_gui/app.py` `_wp_advance`/`_wp_begin_execute` (gate ให้หยุดส่ง GOTO เมื่อ Go authority on)

### NEXT METHOD TO MODIFY (F4)
- Go: `mission.Engine` เพิ่ม command-sink interface (inject จาก server → command.Service) — **shadow ยังเป็น default**
- Python: gate ใน `_wp_advance`/`_wp_advance_one` (เช็ค authority flag ก่อน `_goto_one`)

### NEXT TEST TO WRITE (F4)
- Go: authority-mode engine ส่ง GOTO ผ่าน injected command-sink (mock) ตาม arrival; shadow-mode ไม่ส่ง
- SITL/integration: kill Python → Go advance; no dual GOTO

## Fast-Track F2 Checkpoint

### CURRENT F-PHASE
- **F2 — Go Mission Engine Skeleton (SHADOW ONLY) → ✅ DONE / SAFE / RUNNABLE**
- **Last Safe Runnable Checkpoint = F2** (F0 ก่อนหน้ายังใช้ได้; F2 เพิ่ม package ใหม่ ไม่แตะ code เดิม)

### DONE
- สร้าง package `backend/internal/mission/` (shadow model ตาม contract PART B):
  - `plan.go` — `MissionPlan`/`Route`/`Waypoint`/`Mode`(GROUPED/SEPARATE/SWARM_LEADER)/`Action` + `Validate()`
  - `state.go` — `State` machine (IDLE→VALIDATING→READY→RUNNING↔WAITING→COMPLETED / CANCELLED / INTERRUPTED / FAILED) + `Owner` + `Transition`
  - `engine.go` — `Engine` shadow: `Start`(idempotent+reject 2nd), `Observe`(arrival จาก telemetry+radius),
    `Poll`(WAIT deadline), `Cancel`(stale-safe), `Interrupt`(failsafe fail-closed), `Snapshot`(GetMissionState shadow)
  - `util.go` — deterministic helpers
- **Arrival detection ย้ายเข้า Go แล้ว (shadow)**: `Observe` ใช้ `pkg/geo.HaversineM` + `arrivalRadius`
  (default 3.0 = ค่าเดิมจาก `map.html:TGT_REACH_M`) — แก้ cutover risk #1 ในระดับ shadow
- `run_id` = **Core-generated** (`Engine.nextRunID`) แทน Python `itertools.count`
- ครอบ characterization scenarios: GROUPED รอครบทุกลำ, SEPARATE advance อิสระ + per-drone WAIT,
  SWARM_LEADER เฉพาะ leader ขับ progression, WAIT hold/deadline, cancel stale-safe, failsafe fail-closed + no-auto-resume

### FILES CHANGED (F2)
- `backend/internal/mission/{plan,state,engine,util}.go` (ใหม่, source)
- `backend/internal/mission/{plan,state,engine}_test.go` (ใหม่, tests)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint)
- **ไม่แตะ frontend / proto / command path / app.py เลย** (git status frontend = WIP เดิม ไม่มีของ F2)

### TESTS RUN
- `cd backend && go test ./internal/mission/ -v` → **27 PASS** (0.6s)
- `go vet ./internal/mission/` → clean
- `go test ./...` → **ทุก package ผ่าน** (mission + fleet/swarm/command/api/safety/store/geo/...)

### TEST RESULTS
- **F2 SHADOW ONLY verified:** `TestNoFlightCommandImports` พิสูจน์ว่า package ไม่ import
  fleet/command/swarm/mavlink = ไม่มีทางส่ง flight command (F2 exit criterion)
- ไม่รัน frontend suite (F2 ไม่แตะ frontend); frontend baseline คงเดิม (965/181)

### DECISIONS
- **F2-D1:** engine เป็น pure shadow — inject `Clock` (เหมือน `_wp_clock`), WAIT ใช้ injected clock ไม่รอเวลาจริง
- **F2-D2:** payload action A/B modelled แบบ instantaneous ใน shadow (ไม่มี servo command) — F2 โฟกัส waypoint/WAIT progression; timing servo จริงเป็นเรื่อง F5/F6B
- **F2-D3:** mission-level `State` = WAITING เฉพาะเมื่อไม่มี scope ใด transiting (`hasTransiting`);
  SEPARATE ที่ลำหนึ่ง WAIT แต่ลำอื่นบิน = ยัง RUNNING (per-drone wait อยู่ใน `Snapshot.Waits`)
- **F2-D4:** SWARM_LEADER progression ขับด้วย `LeaderID` เท่านั้น — ไม่สร้าง formation authority ซ้ำ (followers อยู่ใต้ Go swarm manager)
- **F2-D5:** Start idempotency ชั่วคราวผูกกับ `PlanID` (operation_id จริงเป็นของ F3 RPC)

### RISKS / OPEN (ยกไป F3/F4)
- shadow ยังไม่ถูกป้อนด้วย telemetry จริงจาก fleet — F3 ต้อง wire observation source (ยังไม่ authority)
- GROUPED staggered GOTO timing ไม่ได้ modelled (shadow สนใจ sequence ไม่ใช่ 150ms stagger) — พอสำหรับ shadow
- proto ยังไม่มี Mission RPC/run_id/wait_seconds — F3 ต้อง extend proto + server handler
- "shadow agrees with characterization" ปัจจุบัน = encode Python-observable ลง Go tests; cross-run comparison จริงเกิดตอน F3/F4 wire

### EXACT NEXT STEP (F3 — ยังไม่เริ่ม, รอไฟเขียว)
1. อ่าน contract PART B (B4 Start / B5 Cancel / B6 Query) + F3 section ของ V2
2. ออกแบบ proto: เพิ่ม `StartMission`/`CancelMission`/`GetMissionState` (หรือ extend `MissionControl`)
   พร้อม `run_id`, `operation_id`(request_id), `wait_seconds` ใน Waypoint — **ยึด proto convention เดิม**
3. Wire `mission.Engine` เข้า Go server (`internal/api/server.go`) แบบ RPC boundary — **ยังไม่ให้ authority**:
   Python ยัง execute จริง; Go รับ Start/Cancel/Query + observe telemetry แบบ shadow
4. tests: duplicate Start, duplicate/stale Cancel, reconnect Query (contract B4/B5/B6 exit)
5. ปิด F3 checkpoint แล้วหยุดก่อน F4 (waypoint authority cutover)

### NEXT FILE TO OPEN (F3)
- `proto/swarmgod/v1/command.proto` (Waypoint + mission messages) และ `service.proto` (RPC)
- `backend/internal/api/server.go` (wire engine); อ้างอิง `internal/command/service.go` idempotency pattern

### NEXT METHOD TO MODIFY (F3)
- proto regen + server handler ใหม่ (StartMission/CancelMission/GetMissionState)
- **ห้ามแตะ Python executor / app.py ใน F3** — Python ยังเป็น authority จน F4

### NEXT TEST TO WRITE (F3)
- server-level: duplicate Start → run เดิม; stale Cancel → ไม่แตะ run ใหม่; Query หลัง reconnect คืน state ถูก

## Fast-Track F1 Checkpoint

### CURRENT TRACK
- `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`

### CURRENT F-PHASE
- **F1 — Mission Boundary Audit + Contract → ✅ DONE**
- checkpoint: **DONE / docs-only / 0 source flight-behavior change / F0 ยังเป็น last SAFE RUNNABLE checkpoint**
- **Deliverable ส่งแล้ว:** `docs/MISSION_CORE_CUTOVER_CONTRACT.md` (PART A current reality + PART B target contract)

### DONE
- ตรวจ Progress/Git status/diff อีกครั้ง; WIP เดิมยังอยู่และไม่ถูก cleanup/revert
- ยืนยัน F0 frontend **181 passed** + Go fleet/swarm/command/api passed
- กำหนด F1 scope: Python waypoint start/progression/arrival/WAIT/action/cancel,
  RTL/E-STOP/failsafe interrupts, stale guards, request-id/idempotency และ reconnect query
- audit source ปัจจุบันบางส่วนแล้ว:
  - `_wp_execute()` validate/confirm/freeze summary run แล้วผ่าน auto-takeoff gate ก่อน `_wp_begin_execute()`
  - `_wp_begin_execute()` ทำ Python เป็น executor authority และ `_wp_advance*()` ส่ง GOTO ผ่าน `_goto_one()`
  - arrival authority ปัจจุบันอยู่ใน browser map JS: ระยะราบ `<= 3.0m` ส่ง
    `target_reached` ผ่าน `MapBridge` เข้า `_on_target_reached()`; ไม่ตรวจ altitude
  - GROUPED รอ target IDs ครบ; SEPARATE advance อิสระ; Swarm active สั่ง GOTO เฉพาะ Head
  - arrival flow = ARRIVE → optional WAIT/HOLD → optional payload action → delayed advance
  - WAIT entry เก็บ `_wp_wait_generation`, process-local `_flight_run_id`, `_wave_generation`,
    deadline/callback; cancel/E-STOP/failsafe invalidate WAIT ก่อน progression
  - Core battery/link ALARM เป็น failsafe authority; Python `_wp_failsafe_interrupt()`
    หยุด local progression โดยไม่ส่ง HOLD/GOTO/RTL ทับ
  - Cancel Nav ปัจจุบัน: invalidate local executor → (ถ้า swarm) `swarm_stop` →
    `stop_all` → `hold`; RPC sequence ยังไม่มี mission run identity
  - `_flight_run_id` มาจาก process-local counterใน `flight_progress.py`, ใช้ presentation/
    callback guard บางส่วนเท่านั้น และไม่ถูกส่งไป Go
- audit gaps ที่ contract ต้องระบุชัด:
  - `UploadMission`/`MissionControl` ยัง unimplemented และไม่มี GetMissionState
  - duplicate Start/Cancel, stale Cancel และ UI reconnect semantics ยังไม่มี
  - no-WAIT `QTimer.singleShot(500, advance_cb)`, grouped staggered GOTO callbacks และ
    payload worker/finalizer ไม่มี Core-owned run guard; cancel→new mission ต้องถูก characterize
  - GOTO rejection ปัจจุบัน log result แต่ Python executorไม่มี deterministic FAILED transition
  - plan snapshot ใน `flight_progress` เป็น presentation snapshot ไม่ใช่ immutable executable MissionPlan

### CURRENT WIP
- **ไม่มี WIP ค้าง — F1 ปิดครบแล้ว (SAFE/RUNNABLE, docs-only)**
- Contract ครบทั้ง PART A (audit line-verified) + PART B (target contract)
- audit ถูก re-verify กับ source จริงในรอบนี้ (line numbers อัปเดตหลัง presenter extraction):
  `_wp_execute` 7346, `_wp_begin_execute` 7427, `_wp_advance` 7500, `_on_target_reached` 6347,
  `_wp_on_arrived` 7594, `_wp_wait_begin` 7609, `_wp_failsafe_interrupt` 6091, `_flight_run_start` 5023
- ยืนยัน arrival = browser JS `map.html:185 TGT_REACH_M=3.0` (ระยะราบ) → `target_reached`
- ยืนยัน `run_id` = `flight_progress.py` `itertools.count(1)` process-local, ไม่ส่งไป Go
- ยืนยัน `UploadMission`/`MissionControl` มีใน proto แต่ **ไม่มี Go handler**; ไม่มี wait_seconds/run_id
- ยืนยัน Go `command.Service` = validate→safety.Envelope→audit→MAVLink→ACK (path บังคับของ Mission Engine)

### FILES CHANGED (F1)
- `docs/MISSION_CORE_CUTOVER_CONTRACT.md` ✅ **สร้างแล้ว** (F1 deliverable)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint)
- **ไม่มี source/proto/test ถูกแก้ใน F1** — git status: source ที่ modified ทั้งหมด = WIP เดิมก่อน F1

### TESTS RUN
- F1 = docs/audit-only → ไม่รัน test เพิ่ม (ไม่แตะ source, ไม่จำเป็น + memory: เลี่ยง full Qt suite)
- baseline อ้างอิง F0 ที่บันทึกไว้: frontend targeted **181 passed**; Go fleet/swarm/command/api passed;
  full frontend **965 passed** (Phase 2C)

### TEST RESULTS
- F0 remains green (Last Safe Runnable Checkpoint); F1 ไม่มี regression risk (0 source change)

### DECISIONS
- **F1-D1:** contract แยก PART A (current reality) จาก PART B (target) ชัดเจน — คนต่อไปห้ามสับสน
- **F1-D2:** F1 ไม่ implement proto/server/engine — ทำแค่ contract
- **F1-D3:** arrival detection = cutover risk อันดับ 1; ต้องย้ายจาก browser JS เข้า Core **ก่อน/พร้อม F4**
  ไม่งั้น "Python crash → mission survives" เป็นไปไม่ได้ (บันทึกใน contract B8)
- **F1-D4:** Mission run identity ต้องเป็น Core-generated `run_id` แทน Python `itertools.count` +
  generation guards ทั้งหมด (contract B3)

### RISKS (ยกไป F2)
- GROUPED "รอครบทุกลำ" + staggered `singleShot` GOTO ต้องจำลองใน Go ให้ deterministic (F2 shadow ต้องพิสูจน์)
- arrival ผูก UI = ถ้าไม่ย้ายก่อน F4 จะ cutover ไม่ได้จริง
- proto mission RPC เดิมไม่พอ (ไม่มี run_id/request_id/wait_seconds) — F3 ต้อง extend

### EXACT NEXT STEP (F2 — ยังไม่เริ่ม, รอไฟเขียว)
1. อ่าน contract PART B ทั้งหมดก่อนเขียนโค้ด
2. สร้าง package `backend/internal/mission/` (skeleton): `engine.go`, `state.go`, `plan.go` +
   unit tests — **SHADOW ONLY: ห้ามส่ง GOTO/HOLD/TAKEOFF/SERVO/RTL, ห้าม import fleet command send path**
3. Engine รับ plan + telemetry observation → คำนวณ expected state/waypoint/next transition
4. เขียน shadow-comparison tests เทียบกับ characterization scenarios (GROUPED single route ก่อน)
5. ปิด F2 checkpoint แล้วหยุดก่อน F3 (Mission RPC + run id)

### NEXT FILE TO OPEN (F2)
- อ่าน `docs/MISSION_CORE_CUTOVER_CONTRACT.md` PART B (B2 state machine, B3 run id, B8 cutover)
- สร้างใหม่: `backend/internal/mission/state.go` (MissionState enum + transition table จาก B2)
- อ้างอิง pattern จาก `backend/internal/swarm/manager.go` (state loop) และ `command/service.go` (safety path)

### NEXT METHOD TO MODIFY
- ไม่มีการแก้ existing — F2 = สร้าง package ใหม่ (shadow). ห้ามแตะ app.py/command flow

### NEXT TEST TO WRITE (F2)
- `backend/internal/mission/state_test.go`: transition table (IDLE→RUNNING→WAITING→COMPLETED, cancel, interrupt)
- shadow test: GROUPED single-route arrival sequence ต้องตรงกับ Python characterization

## Fast-Track F0 Checkpoint

### CURRENT TRACK
- `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`

### CURRENT F-PHASE
- **F0 — Freeze Fast-Track Baseline**
- checkpoint: **DONE / SAFE / RUNNABLE — source unchanged; frontend + Go gates passed**

### DONE
- อ่าน `REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md`, Progress MD, V1 plan และ Responsibility Map ครบ
- ตรวจ Git status/diff และ source ปัจจุบันจริง; ไม่ revert/cleanup WIP เดิม
- ยืนยัน Phase 2C checkpoint จาก source/tests/doc: MapPresenter อยู่ใน compatibility boundary,
  full frontend ล่าสุด **965 passed**
- ยืนยัน V1 Phase 3 Telemetry Store **ยังไม่เริ่มและไม่ DONE**
- initial command/idempotency inventory:
  - Python `CoreClient._rid()` สร้าง UUID ใหม่ทุกครั้งที่เรียก flight RPC method
  - Go `Idempotent` key = `(request_id, drone_id)`, retention 60 วินาที;
    empty ID ไม่ dedup; concurrent same-key รอผลเดิม แต่ timeout 10 วินาทีแล้วอาจ execute เอง
  - Arm/Disarm/Kill/Takeoff/Land/RTL/Hold/SetMode/Goto/ChangeAlt/Geofence และ
    Swarm RETURN ผ่าน `Idempotent`
  - Servo/RC/StopAll/EqualizeAlt/ParamSet/SetLeader/Swarm START-STOP/config ไม่มี
    request-id/dedup boundary แบบเดียวกัน
  - `UploadMission`/`MissionControl` มีใน proto/generated stubs แต่ไม่มี Server implementation;
    ยังไม่มี Mission Start/Cancel/Query/run_id contract ใน Go
- targeted frontend mission/Waypoint/WAIT/failsafe/swarm baseline:
  **181 passed in 272.61s**
- Go fleet/swarm/command/api baseline: **all 4 packages passed (cached)**

### CURRENT WIP
- ไม่มี source WIP ของ F0; checkpoint นี้ SAFE/RUNNABLE
- F1 Mission Boundary Audit + Contract **ยังไม่เริ่ม**

### FILES CHANGED
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` เท่านั้นสำหรับ F0
- ไม่มี source code เปลี่ยนใน F0

### TESTS RUN
- `python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
- `go test ./internal/fleet/... ./internal/swarm/... ./internal/command/... ./internal/api/...`

### TEST RESULTS
- last known safe checkpoint: frontend full **965 passed** (Phase 2C)
- F0 targeted frontend: **181 passed, 1 existing pytest-cache warning in 272.61s** ✅
- F0 Go: `fleet` ✅ / `swarm` ✅ / `command` ✅ / `api` ✅ (all cached)

### DECISIONS
- **F0-D1:** พัก V1 Phase 3 แบบ `NOT DONE / DEFERRED`; ไม่สร้าง TelemetryStore
- **F0-D2:** F0 เป็น read/test/document-only; ห้ามแก้ mission behavior หรือ authority
- **F0-D3:** ห้ามถือว่า request-id ปัจจุบันคือ system-wide หรือ mission idempotency;
  Mission Start/Cancel/run identity ยังไม่มี

### RISKS
- WIP ใหญ่ยังอยู่ใน Python Waypoint/WAIT/WAVE และ Go swarm/failsafe; ห้าม cleanup/revert
- Python ยังเป็น authority ของ Waypoint progression, WAIT callback และ WAVE sequencing;
  UI crash จึงยังทำให้ execution เหล่านี้หยุด/หาย
- idempotency retention มีขอบเขตเวลา และ in-flight wait timeout สามารถ fallback ไป execute `fn`;
  ต้องกำหนด Mission Start/Cancel semantics แยกใน F1/F3

### EXACT NEXT STEP
1. เปิด **F1 — Mission Boundary Audit + Contract** checkpoint (docs/tests only; no behavior change)
2. inventory Python waypoint start/progression/arrival/WAIT/action/cancel/RTL-E-STOP/failsafe/run guards
3. สร้าง `docs/MISSION_CORE_CUTOVER_CONTRACT.md` พร้อม state-transition table และ
   MissionPlan/run_id/Start/Cancel/Query/preemption/authority rules
4. หยุดก่อน F2; ห้ามสร้าง Go Mission Engine จน F1 contract ผ่าน review/checkpoint

### NEXT FILE TO OPEN
- `frontend/swarmgod_gui/app.py` เฉพาะ `_wp_execute` ถึง `_wp_wait_*`,
  `_cancel_navigation`, `_abort_waypoint_execution`, `_on_target_reached`

### NEXT METHOD TO MODIFY
- ไม่มีใน F1 audit; deliverable แรกเป็นเอกสาร contract และ characterization gaps เท่านั้น

### NEXT TEST TO WRITE
- F1 ต้อง inventory existing characterization ก่อน; test แรกเฉพาะ transition/guard ที่ยังไม่มี coverage

## Flight Authority Matrix

| Subsystem | Current Authority | Target Authority | Cutover Status |
|---|---|---|---|
| Waypoint progression | Python `GroundStation` | Go Mission Engine | NOT STARTED |
| WAIT timing/advance | Python Qt timer + generation guard | Go Mission Engine | NOT STARTED |
| WAVE sequencing | Python `GroundStation` | Deferred/conditional F6B | NOT STARTED |
| Swarm formation loop | Go `swarm.Manager` | Go | Existing; F0 targeted Go passed |
| Failsafe | Go fleet/safety + FC onboard layer | Go + FC | Existing; F0 targeted frontend/Go passed |
| Mission run identity/query | ไม่มี Core authority | Go Mission Engine | NOT STARTED |

## Protected Real-Flight Modes
- Waypoint: **NO**
- WAIT: **NO**
- Swarm formation: **NO — not yet Fast-Track verified**
- WAVE: **NO / DEFERRED unless first flight requires F6B**
- Payload automation: **NO / DEFERRED**

## Last Safe Runnable Checkpoint
- **V2 F0 DONE / SAFE / RUNNABLE**
- frontend mission-related targeted **181 passed**; Go fleet/swarm/command/api all passed
- Phase 2C full frontend checkpoint **965 passed** ยังคงเป็น full-suite reference ล่าสุด
- ไม่มี source/flight authority/RPC/command-order change ใน F0

## Phase 2C Map Presentation Adapter Checkpoint

### Current Phase
- **Phase 2C — Map Presentation Adapter / 3D drone-marker payload only**
- checkpoint: **DONE — targeted + adjacent + safety audit + full frontend gates passed**

### DONE
- อ่าน migration checkpoint ปัจจุบันและทบทวน Map sections ใน plan/responsibility map
- ตรวจ `git status`, `git diff --stat` และยืนยัน WIP ขนาดใหญ่เดิมยังคงอยู่โดยไม่ cleanup/revert
- inventory `_js3d`, `_push_map3d`, `_push_map3d_view`, `_push_map3d_targets`,
  `_wp_routes_3d` และ tests/assets contract ที่เกี่ยวข้อง
- เลือก extraction slice เดียว: pure formatting ของ drone-marker payload ใน `_push_map3d`
- ตัด `_push_map3d_view`, `_push_map3d_targets`, route/fence/GCS/selection/leader/swarm edges
  ออกจาก scope เพราะมี business/navigation state
- เพิ่ม `frontend/tests/test_map_presenter.py` characterization 3 tests
- targeted baseline (`test_map_presenter.py` + `test_map3d.py`) → **56 passed in 14.30s**
- สร้าง pure/no-Qt `controllers/map_presenter.py`; รับ read-only snapshots + color/mode adapters
- เปลี่ยน `_push_map3d()` เฉพาะ payload-building slice เป็น compatibility delegation;
  readiness gate, one-call dispatch และ `json.dumps` call-site ยังอยู่ที่เดิม
- เพิ่ม pure unit test 1 test; targeted หลัง extraction → **57 passed in 17.82s**
- adjacent Map/RTL/GPS/collision/Waypoint regression → **177 passed in 163.92s**
- forbidden dependency scan clean: adapter ไม่มี Qt/CoreClient/RPC/client/command/selection/
  target/waypoint ownership; คำ navigation/flight มีเฉพาะ boundary docstring
- ยืนยัน `_push_map3d_view`, `_push_map3d_targets`, `_wp_routes_3d` และ command call-sites
  ไม่ถูกแก้ใน Phase 2C; `git diff --check` ไม่มี whitespace error
- full frontend gate หลัง extraction → **965 passed in 888.05s (14:48)**

### CURRENT WIP
- ไม่มี source WIP ของ Phase 2C; หยุดที่ checkpoint ก่อนเริ่ม Phase 3 Telemetry Store shadow

### Files Changed
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint นี้)
- `frontend/tests/test_map_presenter.py` (ใหม่; characterization 3 tests)
- `frontend/swarmgod_gui/controllers/map_presenter.py` (ใหม่; pure/no-Qt)
- `frontend/swarmgod_gui/app.py` (minimal import/init + `_push_map3d` wrapper)

### Tests Run
- `python -m pytest tests/test_map_presenter.py tests/test_map3d.py -q`
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/map_presenter.py tests/test_map_presenter.py`
- `python -m pytest tests/test_tactical_map.py tests/test_rtl_and_mapui.py tests/test_map_gps.py tests/test_map_collision_ui.py tests/test_waypoint_ui.py -q`
- `python -m pytest -q`

### Test Results
- characterization baseline: **56 passed, 1 existing pytest-cache warning in 14.30s** ✅
- หลัง extraction + pure unit: **57 passed, 1 existing cache warning in 17.82s** ✅
- Python compile: OK ✅
- adjacent Map/navigation suites: **177 passed, 1 existing cache warning in 163.92s** ✅
- forbidden dependency/safety diff audit: clean ✅
- `git diff --check`: ไม่มี whitespace error; มีเพียง CRLF notice เดิม ✅
- full frontend: **965 passed, 1 existing pytest-cache warning in 888.05s (14:48)** ✅

### Decisions
- **D7:** Map adapter รอบแรกถือเฉพาะ pure `setDrones` payload formatting; readiness gate และ
  `_js3d` transport อยู่ใน `GroundStation` เพื่อคง scheduling/call order เดิม
- **D8:** presenter รับ telemetry/name/group/color/mode adapters ที่จำเป็นเท่านั้น และห้ามรับ
  `GroundStation self`, CoreClient, RPC, selected IDs, nav targets หรือ waypoint routes
- **D9:** ไม่ขยาย MapPresenter ไป `_push_map3d_view()`/targets/routes ใน checkpoint เดียวกัน;
  Phase 2C ปิดด้วย one-responsibility slice ตาม compatibility-first

### Risks
- `_push_map3d_view()` อ่าน selection/head/swarm/fence/waypoint route และ `_push_map3d_targets()`
  อ่าน navigation targets: ทั้งสองเมธอดอยู่นอก scope โดยเด็ดขาด
- mode/color formatting ต้องคง output เดิม byte-for-byte ใน payload เพื่อไม่ทำให้ asset contract เปลี่ยน
- timer เรียก `_push_map3d()` ถี่; adapter ต้องไม่เพิ่ม side effect หรือ JavaScript call count

### Exact Next Step
1. เปิด Phase 3 Step 3.1 checkpoint และ inventory ทุก read/write ของ `_last_telem`
2. เขียน characterization test ของ additive shadow write ก่อน source code
3. สร้าง pure `core/telemetry_store.py` เฉพาะ `update()`/`snapshot()` แล้วเขียนทั้ง state เดิม + shadow;
   UI, map, preflight, RTL, waypoint และ safety **ยังอ่าน `_last_telem` เดิมทั้งหมด**

### Next File To Open
- `docs/APP_PY_RESPONSIBILITY_MAP.md` หมวด telemetry แล้ว `GroundStation._on_telemetry()`

### Next Method To Modify
- ยังไม่มี; ต้อง inventory Phase 3 ก่อน Candidate คือ additive shadow write ใน `_on_telemetry()` เท่านั้น

### Next Test To Write
- characterization: หลัง `_on_telemetry(t)` shadow snapshot ตรงกับ `_last_telem` โดยไม่เปลี่ยน
  widget/command output และ legacy readers ยังชี้ state เดิม

## Phase 2B Fleet Presenter Checkpoint

### Current Phase
- **Phase 2B — Fleet Presenter / presentation-only**
- checkpoint: **DONE — targeted + adjacent + full frontend gates passed**

### DONE
- อ่าน `ARCHITECTURE_MIGRATION_PROGRESS.md`, `LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md`,
  `APP_PY_RESPONSIBILITY_MAP.md`, `WAYPOINT_WAIT_SWARM_MODE_TEST_PLAN.md` ครบทั้งไฟล์
- ตรวจ `git status`, `git diff --stat` และ diff ของ tracked WIP; ไม่ revert/cleanup ไฟล์เดิม
- ระบุ mixed-responsibility boundary ของ `_refresh_selection_ui` และ `_refresh_group_ui`
- เพิ่ม `frontend/tests/test_fleet_presenter.py` characterization 6 tests
- targeted baseline (`test_fleet_presenter.py` + `test_ui_selection.py` + `test_groups.py`)
  → **58 passed in 40.33s**
- สร้าง pure/no-Qt `controllers/fleet_presenter.py` และต่อ compatibility presentation slices
- targeted หลัง extraction + pure unit tests ใหม่ → **62 passed in 40.48s**
- adjacent telemetry/field/waypoint/summary regression → **121 passed in 87.52s**
- ตรวจ forbidden dependency: FleetPresenter ไม่มี Qt/CoreClient/RPC/flight command/
  `_target_ids`/`_target_mode` และไม่เขียน `_selected_ids`
- full frontend gate หลัง extraction → **961 passed in 804.97s**

### CURRENT WIP
- ไม่มี source WIP ของ Phase 2B; หยุดที่ checkpoint ก่อนประเมิน Map Presentation Adapter
- exact extraction scope:
  1. `_drone_name(did)` → compatibility wrapper ที่ส่งเฉพาะ custom/item/telemetry name เข้า presenter
  2. name normalization ภายใน `_on_drone_renamed()` → presenter (method เดิมยังถือ persistence/map/log)
  3. display-name fallback ใน `_on_telemetry()` → presenter; telemetry ingest/orchestration อยู่ที่เดิม
  4. fleet-count label call-sites → compatibility render helper; ไม่ย้าย registry lifecycle
  5. group card/chip text, tooltip, enabled state ใน `_refresh_group_ui()` → presenter;
     `_refresh_wave_group_choices()` อยู่ใน `GroundStation`
  6. card highlight/takeoff-panel selection text + selected summary formatting ใน
     `_refresh_selection_ui()` → presenter; Summary/Waypoint callbacks อยู่ใน `GroundStation`

### Files Changed
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md`
- `frontend/tests/test_fleet_presenter.py` (ใหม่; characterization 6 tests)
- `frontend/swarmgod_gui/controllers/fleet_presenter.py` (ใหม่)
- `frontend/swarmgod_gui/app.py` (minimal presenter wiring/wrappers)

### Tests Run
- `python -m pytest tests/test_fleet_presenter.py tests/test_ui_selection.py tests/test_groups.py -q`
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/fleet_presenter.py tests/test_fleet_presenter.py`
- `python -m pytest tests/test_field_phase3.py tests/test_waypoint_ui.py tests/test_preflight_summary_v2.py -q`
- `rg` forbidden dependency scan + `git diff --check`

### Test Results
- **58 passed, 1 existing pytest-cache warning in 40.33s** ✅
- หลัง extraction/pure tests เพิ่ม: **62 passed, 1 existing cache warning in 40.48s** ✅
- Python compile: OK ✅
- adjacent suites: **121 passed, 1 existing cache warning in 87.52s** ✅
- forbidden dependency scan: clean (พบคำต้องห้ามเฉพาะใน docstring ยืนยันขอบเขต) ✅
- `git diff --check`: ไม่มี whitespace error; มีเพียง CRLF notice เดิม ✅
- full frontend: **961 passed, 1 existing cache warning in 804.97s (13:24)** ✅

### Decisions
- **D4:** ห้ามย้าย `_refresh_selection_ui()` ทั้งก้อน เพราะท้ายเมธอดเรียก Waypoint render
  และเขียน Summary; ย้ายเฉพาะ presentation slice แล้วคง orchestration เดิม
- **D5:** ห้ามย้าย `_refresh_group_ui()` ทั้งก้อน เพราะเรียก WAVE group choices;
  presenter render เฉพาะ card/group-chip แล้ว wrapper เรียก WAVE ต่อเหมือนเดิม
- **D6:** FleetPresenter ต้องไม่มี Qt import, CoreClient, RPC, command, timer, thread,
  `GroundStation self`, `_target_ids`, `_target_mode` หรือ mutation ของ `_selected_ids`

### Risks
- `_selected_ids` เป็น high-risk shared state และกำหนด flight targets ทางอ้อม: presenter รับ snapshot/read-only iterable เท่านั้น
- `_refresh_selection_ui()` เชื่อม Summary + Waypoint: ต้อง lock call order เดิมด้วย app characterization test
- `_refresh_group_ui()` เชื่อม WAVE: ห้ามย้าย/เปลี่ยน `_refresh_wave_group_choices()`
- `_on_drone_renamed()` มี persistence/map side effects: extract ได้เฉพาะ normalize/format string

### Exact Next Step
1. เปิด checkpoint ใหม่เพื่อ **ประเมิน** Map Presentation Adapter ขนาดเล็กเท่านั้น
2. ระบุ render-only methods/call-sites และแยก navigation decision ออกก่อนเขียน test
3. ถ้าต้องรับ `GroundStation self`, route/target decision, GOTO/RPC หรือ flight state → STOP และลด boundary

### Next File To Open
- `docs/APP_PY_RESPONSIBILITY_MAP.md` หมวด Map (2D/3D) แล้ว `frontend/tests/test_map3d.py`

### Next Method To Modify
- ยังไม่มี; ต้องทำ Map scope inventory/characterization checkpoint ก่อน

### Next Test To Write
- ยังไม่เขียนจนกว่าจะระบุ Map render-only boundary; candidate แรกต้องไม่มี navigation decision

## What Has Been Completed
- อ่านครบ 3 เอกสารบังคับ (plan / WAYPOINT_WAIT test plan / CODEX_CHANGES)
- ตรวจ `git status` / `git diff --stat` (ดู section ล่าง) — ไม่ overwrite WIP
- **Phase 0 DONE:** verify baseline — WIP frontend 181 passed, Go ./... ผ่านทุก package
- **Phase 1 DONE:** สร้าง `docs/APP_PY_RESPONSIBILITY_MAP.md` — inventory ครบ:
  state ownership (11 กลุ่ม), async inventory (4 signals / 11 QTimer / ~40 threads / 31 singleShot),
  command authority (ทุก `self.client.*` call site), method groups ตาม subsystem + classification P/B/F/S,
  high-risk shared state, extraction candidates Phase 2
- **Observability DONE:** `health_monitor.py` + wiring observe-only ผ่าน full frontend suite **946 tests**
- **Phase 2A Summary Presenter DONE:**
  - เพิ่ม pure Python `controllers/summary_presenter.py` (ไม่มี Qt/gRPC/client)
  - ย้าย legacy-key mapping, COMMAND audit event, plan/timeline render และ snapshot ออกจาก `app.py`
  - คง `_summ_set`, `_summ_remove`, `_summ_event`, `_render_summary`, `_flight_snapshot`
    เป็น compatibility wrappers; `_summ_sync_waypoint_body` และ WIP WAIT/WAVE ไม่ถูกย้าย
- **Phase 2B Fleet Presenter DONE:**
  - เพิ่ม pure/no-Qt `controllers/fleet_presenter.py`
  - แยก name normalization/fallback, fleet count, selection highlight/summary และ group badge/chip presentation
  - คง selection/target authority, Summary/Waypoint/WAVE orchestration, persistence และ telemetry ingest ใน `GroundStation`
- **Phase 2C Map Presentation Adapter DONE:**
  - เพิ่ม pure/no-Qt `controllers/map_presenter.py`
  - แยกเฉพาะ sorted/filter/format ของ Map 3D drone-marker payload
  - คง readiness, scheduling, JSON/JavaScript dispatch, navigation targets, routes, selection,
    fence, waypoint และ flight authority ใน `GroundStation`

## Files Changed (โดย migration งานนี้)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` — handoff notebook
- `docs/APP_PY_RESPONSIBILITY_MAP.md` — Phase 1 inventory deliverable (docs เท่านั้น)
- **`frontend/swarmgod_gui/core/health_monitor.py`** (ใหม่) — UI Watchdog observe-only, pure model,
  ไม่มี Qt/ไม่มี client. วัด stall/telemetry age/rpc/render/core-connected
- **`frontend/tests/test_health_monitor.py`** (ใหม่) — 16 unit tests (FakeClock, ไม่รอเวลาจริง)
- **`frontend/swarmgod_gui/controllers/summary_presenter.py`** (ใหม่) — presentation-only adapter;
  ไม่มี flight state, Qt, RPC หรือ command client
- **`frontend/swarmgod_gui/controllers/__init__.py`** (ใหม่)
- **`frontend/tests/test_summary_presenter.py`** (ใหม่) — characterization 5 tests
- **`frontend/swarmgod_gui/controllers/fleet_presenter.py`** (ใหม่) — presentation-only;
  ไม่มี Qt/client/RPC/flight state และไม่ mutate selected IDs
- **`frontend/tests/test_fleet_presenter.py`** (ใหม่) — characterization 6 + pure unit 4 tests
- **`frontend/swarmgod_gui/controllers/map_presenter.py`** (ใหม่) — pure 3D drone-marker formatter;
  ไม่มี Qt/client/RPC/navigation/flight authority
- **`frontend/tests/test_map_presenter.py`** (ใหม่) — characterization 3 + pure unit 1 test
- **`frontend/swarmgod_gui/app.py`** — wiring observe-only แบบ additive:
  - import HealthMonitor; สร้าง `self._health` ใน `__init__` (ก่อน `_start_stream`)
  - `_tick_clock`: `record_ui_tick()` + `_log_health_snapshot()` ทุก ~30s; เพิ่ม `_on_ui_stall` (log WARNING เท่านั้น)
  - `_on_telemetry`: `note_telemetry(id)`; `_conn_watchdog`: `note_core_connected`;
    `_run_cmd`: `note_rpc(label)`; `_render_summary`: `record_render()`
  - **ไม่เปลี่ยน flight behavior / command order ใด ๆ** — เพิ่ม observability call ล้วน ๆ
  - เพิ่ม `SummaryPresenter` wiring และเปลี่ยน `_summ_*`/`_render_summary` เป็น wrapper;
    flight progress/run_id และ waypoint runtime ยังอยู่ที่เดิม
  - เพิ่ม `FleetPresenter` wiring สำหรับ name/count/selection/group presentation;
    `_on_fleet_click`, `_selected_ids`, `_target_ids`, `_target_mode`, RPC และ flight flow อยู่ที่เดิม
  - เพิ่ม `MapPresenter` wiring และคง `_push_map3d()` เป็น compatibility/readiness/dispatch wrapper;
    `_push_map3d_view`, `_push_map3d_targets`, route/waypoint/navigation ไม่เปลี่ยน

## Tests Run (baseline — 2026-08-27)
- `cd frontend && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
  → **181 passed in 106.74s** ✅
- `cd backend && go test ./...` → **ทุก package ผ่าน (ok/cached)** ✅
- หมายเหตุ: baseline ไม่ได้รัน full Qt suite (930) — memory ห้ามรันชุดเต็มพร่ำเพรื่อ (ช้ามาก)

### Observability phase (2026-08-27)
- `python -m pytest tests/test_health_monitor.py -q` → **16 passed** ✅
- subset (health + ui_selection + preflight_summary_v2 + rtl_and_mapui + servo_and_ui) → **184 passed** ✅
- `python -m py_compile app.py health_monitor.py` → OK
- `python -m pytest -q` → **946 passed, 1 cache warning in 624.68s** ✅
  (warning = pytest เขียน `.pytest_cache` ไม่ได้; exit code 0 และไม่กระทบ test)

### Phase 2A — Summary Presenter (2026-08-27)
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/summary_presenter.py tests/test_summary_presenter.py` → OK
- presenter + Summary V2 pure/widget tests → **19 passed** ✅
- app integration (`test_waypoint_ui.py`, `test_waypoint_wait.py`, `test_wave.py`) → **104 passed in 101.95s** ✅
- `python -m pytest -q` หลัง extraction → **951 passed, 1 cache warning in 893.27s** ✅

### Phase 2B — Fleet Presenter (2026-08-27)
- characterization baseline: **58 passed in 40.33s** ✅
- targeted หลัง extraction + pure unit: **62 passed in 40.48s** ✅
- adjacent field/telemetry/waypoint/summary: **121 passed in 87.52s** ✅
- `python -m pytest -q` หลัง extraction: **961 passed, 1 cache warning in 804.97s** ✅

### Phase 2C — Map Presentation Adapter (2026-08-27)
- characterization baseline (Map presenter + Map 3D): **56 passed in 14.30s** ✅
- targeted หลัง extraction + pure unit: **57 passed in 17.82s** ✅
- adjacent Map/RTL/GPS/collision/Waypoint: **177 passed in 163.92s** ✅
- Python compile + forbidden dependency/safety diff audit: clean ✅
- `python -m pytest -q` หลัง extraction: **965 passed, 1 cache warning in 888.05s** ✅

## Current Architecture Findings
- `frontend/swarmgod_gui/app.py` ปัจจุบัน = 9035 บรรทัด (Responsibility Map snapshot เดิม = 9024),
  class `GroundStation` ถือ state จำนวนมาก
  (telemetry, selection/fleet, map, swarm/head, RTL, waypoint routes/execution, WAIT timers,
  WAVE, preflight/summary, Field Tablet bridge, UI/REMOTE state)
- Pure logic แยกออกไปแล้วบางส่วน: `core/waypoint_logic.py`, `core/swarm_logic.py`,
  `core/flight_progress.py` (pure model + run_id guard)
- Go Core แยก package: fleet / swarm / safety / telemetry / command / api อยู่แล้ว
- WIP ปัจจุบัน (branch `main2`) = feature WAYPOINT WAIT + SWARM MODE LOCK + failsafe interlock
  Codex อ้างว่า: Frontend 930 passed, `go test ./...` ผ่านทุก package (ยังต้อง verify เอง)

## Decisions Made
- **D1:** ถือ Phase 0 = "verify WIP baseline ก่อน" ตามแผน (ห้ามเริ่ม refactor ถ้า WIP ยัง fail แบบไม่เข้าใจ)
  - เหตุผล: plan §6 + prompt Phase 0 ระบุชัด
  - ทางเลือกที่ปฏิเสธ: กระโดดไปทำ responsibility map ทันที — เสี่ยง refactor ชน WIP
- **D2:** จะ **ไม่รัน full Qt suite** ทีเดียว ใช้ targeted tests ของ WIP subsystem แทน
  - เหตุผล: memory `run-only-affected-tests` — full Qt GUI suite ช้ามาก
  - ทางเลือกที่ปฏิเสธ: รัน `pytest -q` ทั้งชุด (930 เทส) — กิน quota/เวลาเกินจำเป็นสำหรับ baseline
- **D3:** SummaryPresenter รุ่นแรกถือเฉพาะ presentation mapping/rendering และ Mission Log audit
  - compatibility method เดิมใน `GroundStation` ยังอยู่ตามแผน §8
  - ไม่ย้าย `_summ_sync_waypoint_body`, `FlightRun`, run_id, RPC หรือ async callback
  - เหตุผล: ลด diff/rollback ง่าย และไม่แตะ WIP WAIT/SWARM/failsafe
- **D4:** FleetPresenter รับเฉพาะ snapshot/adapters และไม่รับ `GroundStation self`
  - mixed methods `_refresh_selection_ui`/`_refresh_group_ui` คง cross-subsystem orchestration ไว้ที่เดิม
  - presenter ไม่มี Qt/CoreClient/RPC/flight command และไม่ถือหรือแก้ selection authority
- **D5:** MapPresenter รอบแรกย้ายหนึ่ง responsibility คือ 3D drone-marker payload เท่านั้น
  - readiness/dispatch อยู่ใน compatibility wrapper เดิม
  - ไม่ย้าย map view/target/route/fence/waypoint/selection เพราะเป็น business/navigation state

## Risks / Known Issues
- WIP diff ใหญ่มาก (`app.py` +951 บรรทัดจาก WIP feature) → พื้นที่นี้ "ห้ามแตะ" ตอน refactor
- `.pytest_cache` เดิมเขียนไม่ได้ (WinError 5) แต่ test execution ผ่านตามปกติ
- ห้าม import widget-only test ที่สร้าง `QApplication` ก่อน app/QtWebEngine test ใน process เดียว;
  targeted run จึงแยกเป็น pure/widget กับ app integration สอง process

## WIP That Must Not Be Overwritten
ไฟล์ WIP ของ feature WAIT/SWARM-LOCK/failsafe (ห้าม revert/overwrite/cleanup):
- `frontend/swarmgod_gui/app.py` (WAIT execution, mode-lock, failsafe interrupt)
- `frontend/swarmgod_gui/core/waypoint_logic.py` (`wait_seconds`, validators, route helpers)
- `frontend/swarmgod_gui/core/swarm_logic.py` (priority `wait=5.5`)
- `frontend/swarmgod_gui/assets/map.html` (WAIT badge, `setWaypointMeta`)
- `backend/internal/fleet/manager.go` (`FailsafeActive`)
- `backend/internal/swarm/manager.go` (formation interlock, `haltFormation`)
- tests ใหม่: `test_waypoint_wait*.py`, `test_waypoint_swarm_lock.py`, `test_waypoint_failsafe.py`,
  `failsafe_active_test.go`, `failsafe_interlock_test.go`, และ untracked docs

## Exact Next Step
1. เริ่ม **V2 F1 — Mission Boundary Audit + Contract** โดยไม่เปลี่ยน flight behavior
2. สร้าง state-transition table จาก Python source ปัจจุบันและเอกสาร
   `docs/MISSION_CORE_CUTOVER_CONTRACT.md`
3. ระบุ MissionPlan/run_id/Start/Cancel/Query/preemption/reconnect/authority cutover ให้ครบ
4. ห้ามเริ่ม F2 shadow engine จน F1 checkpoint เสร็จ

> V1 Phase 3 Telemetry Store = **NOT DONE / DEFERRED UNTIL AFTER SAFETY FAST-TRACK**

## If Another AI Continues
1. อ่านไฟล์นี้และ `docs/REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md` ทั้งไฟล์ก่อน
2. อย่าแตะไฟล์ในหัวข้อ "WIP That Must Not Be Overwritten"
3. V1 Phase 3 ถูกพัก; ห้ามสร้าง TelemetryStore หรือทำ render coalescing ตอนนี้
4. Current track คือ V2; F0 ผ่านแล้วและ exact next phase คือ F1 Contract ก่อน F2/F3/F4 เสมอ
5. ห้ามสร้าง dual authority หรือให้ mission bypass `command.Service`/safety

## Commands To Re-run
- Summary targeted pure/widget: `cd frontend && python -m pytest tests/test_summary_presenter.py tests/test_preflight_summary_v2.py -q`
- Summary app integration: `cd frontend && python -m pytest tests/test_waypoint_ui.py tests/test_waypoint_wait.py tests/test_wave.py -q`
- Fleet targeted: `cd frontend && python -m pytest tests/test_fleet_presenter.py tests/test_ui_selection.py tests/test_groups.py -q`
- Fleet adjacent: `cd frontend && python -m pytest tests/test_field_phase3.py tests/test_waypoint_ui.py tests/test_preflight_summary_v2.py -q`
- Map targeted: `cd frontend && python -m pytest tests/test_map_presenter.py tests/test_map3d.py -q`
- Map adjacent: `cd frontend && python -m pytest tests/test_tactical_map.py tests/test_rtl_and_mapui.py tests/test_map_gps.py tests/test_map_collision_ui.py tests/test_waypoint_ui.py -q`
- F0 frontend baseline: `cd frontend && python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
- F0 Go baseline: `cd backend && go test ./internal/fleet/... ./internal/swarm/... ./internal/command/... ./internal/api/...`
- Baseline Go: `cd backend && go test ./internal/fleet/... ./internal/swarm/...`
- Full frontend (ช้า, ทำเฉพาะตอนจบ Phase): `cd frontend && python -m pytest -q`

## Last Known Git Status
- Branch: `main2` (up to date with origin/main2)
- WIP feature diff ใหญ่และยังไม่ commit; migration เพิ่ม controller/test/docs โดยไม่ revert ของเดิม
- ยังไม่มี commit จากงาน migration นี้
