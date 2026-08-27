# MISSION CORE CUTOVER CONTRACT (F1)

> Deliverable ของ **F1 — Mission Boundary Audit + Contract** ตาม `docs/REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md`
> **สถานะ: DOCS/AUDIT ONLY — ไม่มี source flight behavior เปลี่ยนใน F1**
> วันที่: 2026-08-27 · ผู้จัดทำ: Opus 4.8 (รับช่วง F1 ต่อจาก handoff)
>
> เอกสารนี้แยกชัดเป็น 2 ส่วน:
> - **PART A — CURRENT REALITY (audit จาก source จริง)** = พฤติกรรมวันนี้ที่ Python เป็น authority
> - **PART B — TARGET CONTRACT** = สิ่งที่ Go Mission Engine ต้องรับผิดชอบหลัง cutover (F2→F5)
>
> กฎ V2: ห้ามใช้เอกสารนี้เป็นเหตุผลกระโดดไป implement F2/F3/F4 รวดเดียว. F1 จบ = หยุดก่อน F2.

---

# PART A — CURRENT REALITY (audit)

## A0. Source ที่ audit (verified บรรทัดจริง วันนี้)
`frontend/swarmgod_gui/app.py` = 9035 บรรทัด. Method mission-critical (ยืนยันแล้ว):

| method | line | บทบาทวันนี้ |
|---|---|---|
| `_wp_execute` | 7346 | validate/guard, confirm A/B, freeze summary run, ผ่าน auto-takeoff gate |
| `_wp_require_takeoff` | 7051 | รอ armed + airborne ก่อนเริ่ม route (generation guard) |
| `_wp_begin_execute` | 7427 | **ตั้ง Python เป็น executor authority** (`_waypoint_executing=True`) |
| `_wp_advance` (GROUPED) | 7500 | ส่ง GOTO จุดถัดไปให้ทุกลำ (staggered `singleShot`) |
| `_wp_advance_one` (SEPARATE) | 7469 | ส่ง GOTO รายลำอิสระ |
| `_on_target_reached` | 6347 | **รับ event ถึงเป้าจาก browser JS** → advance |
| `_wp_on_arrived` | 7594 | ARRIVE → (WAIT) → (action A/B) → advance |
| `_wp_wait_begin`/`_wp_wait_tick` | 7609/7673 | WAIT state + poll 500ms (Qt timer เป็นแค่ตัวเรียกตรวจ) |
| `_wp_run_action` | 6960 | HOLD → servo → 2s → release → advance |
| `_cancel_navigation` | 6389 | operator cancel: abort + swarm_stop + stop_all + hold |
| `_abort_waypoint_execution` | 4828 | ตัด executor state (ใช้ร่วม cancel/E-STOP) |
| `_wp_failsafe_interrupt` | 6091 | Core battery/link ALARM → fail-closed halt |
| `_on_event` | 6058 | รับ Core event; ALARM battery/link → failsafe interrupt |
| `_flight_run_start`/`_flight_is_current`/`_flight_run_cancel` | 5023/5048/5066 | presentation run + run_id guard |

## A1. Authority reality — ใครเป็นเจ้าของอะไร "วันนี้"

```text
Plan build/edit        : Python (waypoint_logic routes, _waypoint_route / _wp_routes)
Mission START trigger  : Python (_wp_execute → _wp_begin_execute)
Waypoint progression   : Python executor (_wp_advance / _wp_advance_one)
ARRIVAL DETECTION      : **Browser map JS** (map.html:185 TGT_REACH_M=3.0m, ระยะราบเท่านั้น)
                         → target_reached → MapBridge → _on_target_reached (Python)
WAIT / HOLD            : Python (_wp_wait_* + Qt 500ms poll; HOLD ยิงครั้งเดียวผ่าน client.hold)
Payload action A/B     : Python (_wp_run_action → client.hold/servo_set/servo_release)
GOTO command           : Python → client.goto → Go command.Service → safety → MAVLink
Cancel                 : Python (_cancel_navigation → swarm_stop, stop_all, hold)
Failsafe (battery/link): **Go Core** (fleet manager loop เริ่ม failsafe RTL เอง);
                         Python แค่ยกเลิก local progression (ไม่ยิง command ทับ)
run identity           : Python process-local itertools.count(1) — **ไม่ส่งไป Go, reset เมื่อ restart**
Mission state storage  : **Python process memory เท่านั้น** (Go ไม่รู้ว่ามี waypoint mission)
```

> **ผลลัพธ์ของ reality นี้ = ปัญหาที่ V2 ต้องแก้:** ถ้า Python crash/freeze →
> arrival detection หาย, executor หาย, WAIT poll หาย, run identity หาย. Go ไม่มี mission state ให้ query.

## A2. Current Python mission state fields (source of truth ปัจจุบัน)
```text
_waypoint_executing : bool   ผู้ถือสถานะว่ากำลัง execute
_wp_target_ids      : list   ลำที่อยู่ใน mission
_waypoint_route     : Route  GROUPED shared route
_wp_routes          : dict   SEPARATE per-drone routes
_wp_current_index   : int    GROUPED จุดปัจจุบัน
_wp_sep_index       : dict   SEPARATE per-drone index
_wp_arrived         : set    GROUPED ลำที่ถึงจุดปัจจุบันแล้ว
_wp_waits           : dict   scope_key -> WAIT entry (deadline/run_id/gen)
_wp_wait_generation : int    bump = invalidate WAIT ค้างทั้งหมด
_flight_run_id      : int    presentation run id (จาก flight_progress)
_wave_generation    : int    invalidate WAVE callbacks
_wp_takeoff_generation : int invalidate takeoff-gate callbacks
```
ทั้งหมดเป็น **process-local** — ไม่มีตัวใดอยู่ใน Go หรือ persist.

## A3. CURRENT-STATE TRANSITION TABLE (Python authority วันนี้)

รูปแบบ: `STATE → EVENT → NEXT STATE | COMMAND SENT | CANCEL GUARD | FAILSAFE BEHAVIOR`

### GROUPED (mode หลักของ real-flight รอบแรก)
| STATE | EVENT | NEXT STATE | COMMAND SENT | CANCEL GUARD | FAILSAFE |
|---|---|---|---|---|---|
| IDLE | `_wp_execute()` (มี route, guard ผ่าน) | VALIDATING | — | — | — |
| VALIDATING | confirm A/B + conflict ok | TAKEOFF_GATE | — | — | — |
| TAKEOFF_GATE | grounded → require takeoff | (รอ airborne) | client.takeoff | `_wp_takeoff_generation` | Core failsafe → interrupt |
| TAKEOFF_GATE | armed+airborne | RUNNING (`_wp_begin_execute`) | — | — | — |
| RUNNING | เริ่มจุด i | RUNNING | client.goto (ทุกลำ, staggered) | `_waypoint_executing` + `_wave_generation` | interrupt หยุด advance |
| RUNNING | `target_reached` ครบทุกลำ (JS) | ARRIVED | — | `_wp_arrived>=target_ids` | — |
| ARRIVED | wp.wait_seconds>0 | WAITING | client.hold (ครั้งเดียว) | run_id+gen guard | interrupt → invalidate WAIT |
| ARRIVED | ไม่มี WAIT, มี action | ACTION | client.hold+servo | run_id guard | interrupt → ไม่ทำ action |
| ARRIVED | ไม่มี WAIT/action | RUNNING(i+1) | `singleShot(500, advance)` | `_waypoint_executing` | — |
| WAITING | deadline ถึง (poll 500ms) | ACTION หรือ RUNNING(i+1) | — | `_wp_wait_valid()` 4 guards | interrupt → invalidate |
| WAITING | cancel/E-STOP/failsafe | IDLE | (cancel path) | `_wp_wait_invalidate()` bump gen | fail-closed halt |
| ACTION | servo release เสร็จ | RUNNING(i+1) | servo_release | run_id + `_wave_generation` | interrupt → callback ตาย |
| RUNNING | index >= len(route) | COMPLETED (`_wp_finish`) | — | — | — |
| any active | `_cancel_navigation()` | IDLE | swarm_stop→stop_all→hold | invalidate ทุก gen | — |
| any active | E-STOP (`_do_estop`) | IDLE | stop_all | `_abort_waypoint_execution` | — |
| any active | Core ALARM battery/link ของ participant | INTERRUPTED (FAILED) | **ไม่มี** (fail-closed) | invalidate ทุก gen + latch | Core เป็นเจ้าของ failsafe RTL |

### SEPARATE (Swarm OFF): เหมือน GROUPED แต่ per-drone index/route, advance อิสระ, ไม่รอลำอื่น
### SWARM ACTIVE: GROUPED Leader Path เท่านั้น (Head ได้ GOTO; followers อยู่ใต้ Go formation loop); SEPARATE/WAVE ถูกล็อก (logic guard `_wp_set_separate`/`_wave_toggle`)

## A4. Cancel / Failsafe fail-closed (verified)
- `_wp_failsafe_interrupt` (6091): เงื่อนไข = กำลัง execute **และ** `_wp_participant(drone_id)` เป็นจริง →
  invalidate WAIT, bump takeoff/wave generation, ล้าง executor state, mark timeline FAILED,
  **ไม่ยิง HOLD/GOTO/RTL** (ปล่อย Core failsafe). Mission **ไม่ auto-resume**.
- WAIT guard `_wp_wait_valid` (7642): เช็ค 4 อย่าง — `gen`, `_waypoint_executing`, `wave_gen` (ถ้า WAVE), `run_id`.
  Callback เก่าที่ผิด guard → ทิ้งเงียบ ไม่ยิง `on_done`.

## A5. request-id / idempotency inventory (ข้อเท็จจริง verified)
- Python `CoreClient._rid()` สร้าง UUID ใหม่ **ทุกครั้ง** ที่เรียก flight RPC → retry ระดับ Python
  ได้ id ใหม่ = idempotency ไม่ครอบ retry ที่เกิดจาก UI logic ซ้ำ (ครอบเฉพาะ transport retry ของ id เดิม)
- Go `command.Service.Idempotent(requestID, droneID, fn)` (`internal/command/idempotency.go`):
  key = `(request_id, drone_id)`, retention **60s**, `request_id` ว่าง = **ไม่ dedup**,
  concurrent same-key รอผลเดิม แต่ timeout แล้วอาจ execute เอง
- RPC ที่มี `request_id` (field 15): Arm, Disarm, Kill, Takeoff, Land, RTL, Hold, SetMode, Goto, ChangeAlt, SetGeofence
- RPC ที่ **ไม่มี** request-id/dedup: Servo, Rc*, StopAll, EqualizeAlt, ChangeSpeed, ParamSet, SetLeader, Swarm START/STOP/config, Orbit, SetYaw
- **`UploadMission`/`MissionControl` มีใน proto (`service.proto:47-48`) แต่ไม่มี Go handler เลย** —
  `UploadMissionRequest` เป็น per-drone, ไม่มี request_id/run_id/plan identity;
  `MissionControlRequest` มี START/PAUSE/RESUME/STOP/CLEAR แต่ไม่มี run_id/request_id;
  `Waypoint` proto msg ไม่มี `wait_seconds` (WAIT เป็น frontend-only)
- Go safety command path (`command/service.go`): ทุกคำสั่งผ่าน `validate → safety.Envelope.Check →
  audit.Log → MAVLink → ACK`. **นี่คือ path ที่ Mission Engine ต้องใช้ ห้าม bypass**

## A6. Existing-test coverage matrix (F0 baseline 181 targeted passed)
| behavior | มี test? | ไฟล์ | gap |
|---|---|---|---|
| WAIT model/limit/edit | ✅ | test_waypoint.py, test_waypoint_wait.py | — |
| WAIT runtime GROUPED/SEPARATE + cancel/stale | ✅ | test_waypoint_wait_runtime.py | — |
| Swarm mode lock (SEPARATE/WAVE disabled + guard) | ✅ | test_waypoint_swarm_lock.py | — |
| Battery/link failsafe interrupt (frontend) | ✅ | test_waypoint_failsafe.py | — |
| Failsafe interlock (Go: follower/leader) | ✅ | failsafe_interlock_test.go, failsafe_active_test.go | — |
| Idempotency (request_id, per-drone, empty, retention) | ✅ | idempotency_test.go | ครอบเฉพาะ command เดี่ยว |
| **duplicate Mission Start** | ❌ | — | **GAP — ไม่มี mission-level start** |
| **stale Cancel กระทบ run ใหม่** | ❌ | — | **GAP** |
| **UI reconnect → query state** | ❌ | — | **GAP — ไม่มี GetMissionState** |
| **Core restart → no auto-resume** | ❌ | — | **GAP** |
| **arrival detection ไม่พึ่ง browser JS** | ❌ | — | **GAP — arrival อยู่ใน map.html** |
| no-WAIT `singleShot(500, advance)` stale จาก run เก่า | ⚠️ บางส่วน | test_waypoint_wait_runtime | ควรเพิ่ม explicit |
| GOTO rejection → deterministic FAILED | ❌ | — | **GAP — วันนี้ log แต่ไม่มี FAILED transition** |

---

# PART B — TARGET CONTRACT (Go Mission Engine หลัง cutover)

> ทั้งหมดนี้คือสิ่งที่ F2 (shadow) → F3 (RPC/run id) → F4 (waypoint authority) → F5 (WAIT authority)
> ต้องทำให้จริง. F1 แค่ระบุ contract — ยัง implement ไม่ได้.

## B1. MissionPlan (minimal, immutable หลัง accept)
```text
MissionPlan {
  plan_id         : string   // client-generated stable id ของ plan หนึ่งชุด (immutable)
  mode            : enum { GROUPED, SEPARATE, SWARM_LEADER }   // WAVE = ยังไม่ใน scope V2 (F6B)
  participants    : [drone_id]                                  // ลำที่อยู่ใน mission
  routes          : mode=GROUPED → 1 shared route
                    mode=SEPARATE → per-drone routes
                    mode=SWARM_LEADER → leader route (followers อยู่ใต้ swarm formation)
  waypoint        : { seq, lat, lon, alt, wait_seconds, action(none|servo_a|servo_b) }
  arrival_radius_m: default 3.0 (ย้ายค่าจาก map.html:TGT_REACH_M มาเป็น Core policy)
  rtl_after       : bool
}
```
- Plan ถูก **freeze** ตอน StartMission accepted; แก้ทีหลังไม่กระทบ run ที่กำลังวิ่ง (ตรงกับ A: "แก้ WAIT ห้ามกระทบ snapshot ของ run เดิม")
- `wait_seconds` ต้องเพิ่มใน proto `Waypoint` (F3) — วันนี้ยังไม่มี

## B2. MissionState (explicit state machine — Go owned)
```text
IDLE → VALIDATING → READY → RUNNING → (WAITING ↔ RUNNING) → COMPLETED
                                    ↘ CANCELLING → CANCELLED
                                    ↘ INTERRUPTED (failsafe/Core-preempt)
                                    ↘ FAILED (safety reject / unrecoverable)
```
ทุก transition ต้องมี: `owner` (operator | Core-event | telemetry-condition | failsafe), `reason`, และ bump `revision`.
**ห้าม transition จาก Qt timer ลำพัง** (timer หลัง cutover = display refresh เท่านั้น — MUST-7/F5).

## B3. Mission Run Identity
```text
run_id      : Core-generated (ไม่ใช่ Python count) — unique ต่อ active mission
plan_id     : immutable plan identity (จาก client)
revision    : เพิ่มทุก transition — UI ใช้ตรวจว่า state ที่เห็นล่าสุดหรือไม่
participants: snapshot ตอน start
current_wp  : { scope, index } (owner = Core)
```
**callback/event จาก run เก่า (run_id ไม่ตรง active) = ไม่มี authority** (MUST-3). แทนที่ Python
`itertools.count` + generation guards ทั้งหมดด้วย Core `run_id` เดียว.

## B4. Start semantics (idempotent)
```text
StartMission(plan, operation_id):
  - operation_id ซ้ำ (retry/double-click/reconnect) → คืน run เดิม, ห้ามสร้าง run ใหม่
  - มี active run อยู่แล้ว (คนละ plan) → REJECT (return existing run info) — ห้าม 2 mission พร้อมกัน
  - accept → freeze plan, gen run_id, state=VALIDATING→READY→RUNNING
```
> ต่างจาก idempotency 60s ปัจจุบัน: mission start ต้องผูกกับ **run identity** ไม่ใช่แค่ (request_id, drone_id) window.

## B5. Cancel semantics (idempotent + stale-safe)
```text
CancelMission(run_id, request_id):
  - cancel run_id ที่ไม่ใช่ active run → no-op (คืน status ของ run นั้น) — **stale Cancel ห้ามแตะ run ใหม่** (MUST-4)
  - cancel ซ้ำ run เดิม → safe/idempotent
  - cancel ต้อง invalidate future transitions **ก่อน** ยิง/รอ command อื่น (ลำดับเดียวกับ A4 fail-closed)
  - หลัง cancel → state=CANCELLING→CANCELLED; ไม่มี GOTO/action หลุดตามมา
```

## B6. Query semantics (GetMissionState) — หัวใจของ UI restart
```text
GetMissionState() → {
  run_id, plan_id, state, revision, participants,
  current_waypoint {scope,index}, wait {active, remaining_s, index},
  last_transition {event, reason, ts}, terminal_reason (ถ้ามี)
}
```
UI เปิดใหม่ **ต้อง** query ตัวนี้แล้ว rebuild display — ห้ามเดาจาก Python local vars, ห้าม StartMission ซ้ำ (MUST-7).

## B7. Safety preemption (บังคับ priority)
Mission Engine ต้อง yield ให้ (สูง→ต่ำ): `E-STOP/Stop` > `battery failsafe` > `link failsafe` >
`geofence/safety reject` > `operator cancel` > `mission progression`.
- Failsafe → state=INTERRUPTED, **ไม่ยิง command ทับ** (คงพฤติกรรม A4)
- safety reject ของ GOTO → mission FAILED/INTERRUPTED, **ไม่ retry ไม่จำกัด** (ปิด GAP A6)
- Mission Engine **ห้ามเรียก Drone.Goto/Hold ตรง** — ต้องผ่าน `command.Service` → `safety.Envelope` (MUST-5, F4 safety architecture)

## B8. Authority cutover rule (ห้ามละเมิด)
```text
F2 Shadow : Go คำนวณ expected state คู่ขนาน แต่ **ห้ามส่ง GOTO/HOLD/TAKEOFF/SERVO/RTL**
            Python ยังเป็น executor เดียว → เทียบ shadow กับ characterization scenarios
F4 Cutover: Go = waypoint executor authority (เฉพาะ GROUPED single-route ก่อน, ไม่มี WAVE)
            Python executor **ต้องไม่ส่ง GOTO** ของ mission นั้นอีก → **ห้าม dual authority** (MUST-5)
            rollback ได้ใน dev/SITL ด้วย flag แต่ห้ามเปิดสองฝั่งพร้อมกัน
```
**Arrival detection** ต้องย้ายจาก browser JS (A1) เข้า Core (ใช้ Core telemetry + arrival_radius) ก่อน/พร้อม F4 —
มิฉะนั้น "Python crash → mission survives" เป็นไปไม่ได้ เพราะ arrival ยังผูกกับ UI.

## B9. UI reconnect / Core restart
```text
Python crash, Core alive : Core ถือ mission state ต่อ (in-memory พอสำหรับ V2 — persistence = DEFER §3.5)
                           UI ใหม่ → GetMissionState → rebuild → bind run_id เดิม → ไม่ Start ซ้ำ
Core crash               : mission = INTERRUPTED / LOST AUTHORITY; **ห้าม auto-resume** (MUST-8)
                           FC onboard failsafe = safety layer สุดท้าย (verify ที่ F9 hardware)
```

## B10. Open decisions ก่อน F2 (ต้องตอบใน F2/F3 ไม่ใช่ F1)
1. RPC surface: extend `MissionControl`/`UploadMission` เดิม หรือเพิ่ม `StartMission/CancelMission/GetMissionState` ใหม่?
   (ยึด proto convention ของ project; เพิ่ม `wait_seconds`, `request_id`, `run_id`, `plan_id`)
2. Mission state publish: stream (เพิ่มใน telemetry/event) หรือ poll `GetMissionState`? — F3 ตัดสิน
3. SWARM_LEADER: contract ระหว่าง Mission Engine กับ Go swarm formation loop (ห้ามสร้าง formation authority ซ้ำ — F4)
4. Arrival: ย้าย logic เข้า Core package ไหน (mission engine อ่าน fleet telemetry โดยตรง)
5. GROUPED "รอครบทุกลำ" + staggered GOTO: จำลองใน Go อย่างไรให้ deterministic (F2 shadow ต้องพิสูจน์)

## B11. Coverage gaps ที่ F2/F3 tests ต้องปิด (จาก A6)
- duplicate Start ไม่สร้าง run ซ้ำ · stale Cancel ไม่แตะ run ใหม่ · reconnect query ได้ ·
  Core restart no-auto-resume · arrival ไม่พึ่ง UI · GOTO reject → FAILED · no-WAIT stale advance

---

# F1 EXIT
```text
[x] audit source จริง (line-verified) — Python authority reality
[x] current-state transition table (GROUPED/SEPARATE/SWARM) + cancel/failsafe
[x] request-id/idempotency inventory (Python + Go, verified)
[x] existing-test coverage matrix + gaps
[x] target contract: MissionPlan / MissionState / run_id / Start/Cancel/Query / preemption / cutover / reconnect / restart
[x] no source flight behavior changed
```
**ถัดไป = F2 (Go Mission Engine skeleton, SHADOW ONLY — ห้ามส่ง command).** หยุดที่นี่ก่อนตาม V2.
