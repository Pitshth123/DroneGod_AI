# SwarmGod / DroneGod — REAL-FLIGHT SAFETY FAST-TRACK V2

> **สถานะ: PLAN ONLY — แผนนี้ไม่แทนและไม่ลบ V1**
>
> V1 เดิม: `docs/LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md`
>
> V2 นี้มีเป้าหมายเฉพาะ: **เร่งลดความเสี่ยงที่ Python Cockpit ค้าง/ตายแล้ว mission execution หายไปพร้อม UI ก่อนการทดสอบบินจริง**
>
> วันที่จัดทำ: 27 สิงหาคม 2026
>
> หลักสำคัญ: ยอมพักงาน architecture ที่ช่วยความสะอาด/ประสิทธิภาพแต่ไม่ลด flight-failure risk โดยตรง เพื่อเอาเวลาไปทำ mission authority, restart isolation, failsafe interaction และ verification ก่อน

---

# 0. เหตุผลที่มี V2

V1 เป็นแผน migration แบบครบระยะยาว:

```text
Controllers
→ Telemetry Store
→ Command Gateway
→ Run ID / Dedup
→ Mission Engine → Go
→ Persistence
→ Restartable UI
→ Failure Injection
→ SITL / Hardware / Flight
```

ลำดับนี้ปลอดภัยและเหมาะกับการพัฒนาระยะยาว แต่ใช้เวลามากกว่าจะไปถึงปัญหาหลักที่ต้องแก้ก่อนการบินจริง:

```text
Python Cockpit freeze/crash
        ↓
mission logic ที่ยังอยู่ใน Python หยุด/หายตาม
```

V2 จึงเปลี่ยนลำดับเป็น **Safety Fast-Track** โดยไม่ทิ้ง V1:

```text
Checkpoint ปัจจุบัน
        ↓
Mission Boundary Audit
        ↓
Go Mission Engine แบบ Shadow
        ↓
Mission Run Identity + Start/Cancel Guard
        ↓
Waypoint Authority → Go
        ↓
WAIT Authority → Go
        ↓
UI Restart + Core State Query
        ↓
Failure Injection เฉพาะ flight-critical
        ↓
SITL Safety Gate
        ↓
Hardware Bench
        ↓
Controlled Real Flight
```

ของที่ข้าม/พักจะกลับไปทำตาม V1 หลัง safety fast-track ผ่านแล้ว

---

# 1. Current Checkpoint — จุดเริ่ม V2

ณ วันที่สร้างแผนนี้ `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` ระบุว่า:

```text
Phase 0                         DONE
Responsibility Map             DONE
Observability / HealthMonitor  DONE
Phase 2A Summary Presenter     DONE
Phase 2B Fleet Presenter       DONE
Phase 2C Map Presenter         DONE
Frontend full suite            965 passed
```

และยังไม่มีการย้าย:

```text
flight authority
waypoint execution authority
WAIT authority
mission state authority
```

ไป Go Mission Engine

ดังนั้น V2 **เริ่มจาก checkpoint หลัง Phase 2C** ไม่ย้อนทำ Phase 2 ใหม่

## Current Core advantages ที่ V2 ใช้ต่อได้ทันที

จาก source ปัจจุบัน:

- Go Core เป็น process แยกจาก Python อยู่แล้ว
- Go Core มี fleet manager + telemetry + safety envelope + failsafe loop
- Go Core มี swarm manager ที่ถือ formation loop เอง
- `command.Service` เป็น command path กลางฝั่ง Go และมี safety/audit ก่อน MAVLink
- API mutating commands หลายตัวมี `request_id` + `Idempotent(...)` อยู่แล้ว เช่น Arm, Takeoff, Land, RTL, Hold, SetMode, Goto, ChangeAlt, Geofence
- Python `CoreClient` generate request UUID ให้ RPC หลายตัวอยู่แล้ว

ดังนั้น V2 **ไม่จำเป็นต้องสร้าง Command Gateway และ Dedup ทั้งระบบใหม่ก่อนเริ่ม Mission Engine**

แต่ต้อง audit และเติมเฉพาะ mission boundary ที่ยังขาด

---

# 2. Primary Safety Goal ของ V2

เป้าหมายขั้นต่ำก่อนถือว่า fast-track สำเร็จ:

```text
Python UI crash/freeze
        ↓
Go Core ยังถือ mission state
        ↓
Go Core ยังรู้ current waypoint / WAIT / participants
        ↓
Python callback เก่าไม่มี authority
        ↓
เปิด UI ใหม่
        ↓
Query Core
        ↓
แสดง mission ปัจจุบัน โดยไม่ START ซ้ำ
```

และอีก failure boundary:

```text
Go Core crash / MAVLink GCS หาย
        ↓
ห้าม mission auto-resume
        ↓
FC onboard failsafe ต้องเป็น safety layer สุดท้าย
```

> V2 ไม่สัญญาว่า software จะ "ไม่มีวันล้ม" เป้าหมายคือทำให้ failure ของแต่ละชั้นมีขอบเขตและผลลัพธ์ที่กำหนดได้

---

# 3. สิ่งที่ V2 ข้าม/พักไว้ก่อนได้

งานต่อไปนี้ **ไม่ใช่ prerequisite โดยตรง** ของการแก้ “Python ตายแล้ว mission ตาย” จึงพักได้:

## 3.1 Phase 3 Telemetry Store เต็มรูปแบบ — DEFER

พักได้:

- frontend `TelemetryStore` shadow migration เต็มระบบ
- ย้าย Battery/GPS/Map/Fleet widget มาอ่าน store ใหม่
- render coalescing optimization เต็มระบบ

เหตุผล:

Mission Engine ใหม่ต้องอ่าน telemetry/state จาก **Go fleet/Core โดยตรง** ไม่ใช่ frontend Telemetry Store

ดังนั้น frontend Telemetry Store ไม่จำเป็นต่อ Core-owned mission authority

กลับมาทำหลัง Fast-Track ได้ตาม V1

---

## 3.2 Presentation Controller เพิ่มเติม — DEFER

Phase 2A/2B/2C ให้ boundary ที่ดีพอแล้ว

ยังไม่ต้องรีบแยก:

- UI helper อื่น ๆ
- toast/banner/pill
- map presentation ส่วนที่เหลือ
- settings presenter
- cosmetic refactor

เพราะไม่ช่วยให้ mission รอดจาก Python crash โดยตรง

---

## 3.3 Command Gateway ฝั่ง Python แบบครบทุกคำสั่ง — DEFER

ไม่ต้อง migrate ทุก `self.client.*` ก่อน

Fast-Track ใช้วิธี:

```text
Python Mission UI
      ↓
Mission RPC boundary
      ↓
Go Mission Engine
      ↓
command.Service
      ↓
safety envelope
      ↓
MAVLink
```

Mission Engine **ห้าม bypass `command.Service`/safety path ไปเรียก Drone flight command ตรง ๆ**

Command Gateway แบบ system-wide ค่อยกลับมาทำใน V1

---

## 3.4 System-wide Dedup ทุก command — DEFER

ปัจจุบันมี request-id/idempotency อยู่แล้วหลาย RPC แต่ยังไม่ครบทุก family

V2 ไม่ต้องแก้ทุกคำสั่ง

ต้องทำให้แข็งแรงเฉพาะ:

```text
Mission Start
Mission Cancel
Mission transition/action
UI reconnect
stale run
```

ก่อน

System-wide dedup สำหรับ Servo/RC/Swarm/config/etc. ค่อยทำใน V1 ยกเว้นคำสั่งนั้นถูกนำเข้า mission fast-track จริง

---

## 3.5 Mission Persistence — DEFER สำหรับเป้าหมาย UI crash

ถ้า:

```text
Python crash
Go Core ยังอยู่
```

mission state สามารถอยู่ใน memory ของ Go ได้ และ UI ใหม่ query กลับมาได้

ดังนั้น **ไม่ต้องสร้าง DB persistence ก่อนพิสูจน์ UI restart**

แต่ถ้า Go Core crash:

```text
mission = INTERRUPTED / LOST AUTHORITY
ห้าม auto-resume
FC failsafe รับช่วงตาม config
```

Persistence จะกลับมาทำทีหลังเพื่อ audit/recovery visibility ไม่ใช่เพื่อ auto-resume flight

---

## 3.6 WAVE — DEFER ได้ถ้า real-flight รอบแรกไม่ใช้ WAVE

WAVE มี state ซับซ้อนกว่า Waypoint/WAIT มาก:

```text
group sequencing
TAKEOFF per group
route
payload
return/land
next group
cancel/failsafe
```

เพื่อให้ fast-track สั้น:

**รอบบินจริงแรกที่อาศัย V2 ควรไม่ถือ WAVE เป็น protected mode จนกว่าจะ migrate WAVE authority เข้า Go**

ถ้าทีมต้องใช้ WAVE ในการบินจริงรอบแรกจริง ๆ → WAVE กลายเป็น `CANNOT SKIP` และต้องเพิ่ม Fast-Track Phase F6B ก่อน Flight Gate

---

# 4. สิ่งที่ V2 ห้ามข้าม

ต่อให้ต้องการเร็วแค่ไหน ห้ามข้ามหัวข้อต่อไปนี้

## MUST-1 — Known-good checkpoint

ก่อนเริ่ม mission authority:

- Git/WIP ต้องถูก inventory
- targeted baseline ต้องผ่าน
- full frontend checkpoint ล่าสุดต้องรู้ผล
- Go tests ต้องผ่านสำหรับพื้นที่ที่จะใช้
- ห้าม refactor unrelated code พร้อม mission migration

เหตุผล: ถ้าไม่รู้ baseline จะไม่รู้ว่า bug มาจาก WIP เดิมหรือ authority ใหม่

---

## MUST-2 — Explicit Mission State Machine

ห้ามย้าย Python code เข้า Go แบบ copy/paste timer logic

ต้องมี state ชัด เช่น:

```text
IDLE
VALIDATING
READY
RUNNING
WAITING
CANCELLING
INTERRUPTED
COMPLETED
FAILED
```

ทุก transition ต้องมี owner และเหตุผล

---

## MUST-3 — Mission Run Identity

ทุก active mission ต้องมีอย่างน้อย:

```text
run_id
mission_id / immutable plan identity
current state
current waypoint
participants
transition sequence / revision
```

callback/event จาก run เก่าห้ามมี authority กับ run ใหม่

---

## MUST-4 — Start/Cancel Idempotency

UI reconnect, double click หรือ network retry ต้องไม่ทำให้:

```text
START mission เดิมสองครั้ง
CANCEL แล้ว callback เก่าเดินต่อ
```

Fast-Track ไม่ต้อง dedup ทุก RPC แต่ **Mission Start/Cancel ห้ามข้าม**

---

## MUST-5 — Single Authority

เมื่อ Waypoint subsystem cutover ไป Go แล้ว:

```text
Go = executor authority
Python = plan submit + display + operator intent
```

ห้ามให้ Python executor และ Go executor ส่ง GOTO ของ mission เดียวพร้อมกัน

ใช้ shadow ก่อน cutover ได้ แต่ shadow **ห้ามส่ง flight command**

---

## MUST-6 — Core Safety Preemption

Mission Engine ต้อง yield ให้:

```text
E-STOP / Stop
operator cancel
battery failsafe
link failsafe
geofence/safety reject
Core shutdown
```

Mission code ห้ามส่ง GOTO/HOLD ทับ failsafe หลัง mission ถูก interrupt

---

## MUST-7 — UI Restart State Query

UI ใหม่ต้อง:

```text
connect Core
→ Query active mission
→ render current authoritative state
```

ห้ามเดาจาก local Python variables

ห้าม START mission ซ้ำเพียงเพราะ UI เพิ่งเปิด

---

## MUST-8 — FC Failsafe Validation

V2 แก้ Python failure แต่ไม่ได้ทำให้ Go ไม่มีวันตาย

ก่อน real flight ต้องพิสูจน์ว่าเมื่อ Core/GCS link หาย:

- FC มี onboard failsafe ที่ทีมตั้งใจใช้
- behavior ตรงกับ safety policy ของสนาม/airframe
- ไม่มี Go/Python auto-resume หลังกลับมา

รายละเอียด parameter ต้องตรวจจาก FC configuration จริง ณ วันที่ทดสอบ ไม่ใช้ assumption จากเอกสารเก่า

---

## MUST-9 — SITL Failure Injection ก่อน Hardware/Flight

อย่างน้อยต้องมี automated/repeatable tests สำหรับ:

- kill Python UI ระหว่าง route
- restart UI ระหว่าง active mission
- UI gRPC disconnect/reconnect
- duplicate Mission Start
- stale old-run callback/event
- Cancel ใกล้ waypoint transition
- battery failsafe ระหว่าง RUNNING
- battery/link failsafe ระหว่าง WAIT
- Core stop/crash → no mission auto-resume

---

# 5. FAST-TRACK PHASES

---

# F0 — Freeze Fast-Track Baseline

**Risk: ต่ำ**

## เป้าหมาย

สร้าง checkpoint ก่อนแตะ mission authority

## ทำ

1. อ่าน `ARCHITECTURE_MIGRATION_PROGRESS.md`
2. ตรวจ Git status/diff ใหม่
3. ยืนยัน Phase 2C checkpoint
4. รัน targeted frontend mission/waypoint/WAIT/failsafe tests
5. รัน Go fleet/swarm/command/api tests ที่เกี่ยวข้อง
6. บันทึกผลลง Progress MD

## ห้าม

- telemetry refactor
- UI cleanup
- rename ใหญ่
- mission behavior change

## Exit

```text
[ ] baseline known
[ ] targeted frontend green
[ ] related Go green
[ ] WIP list current
```

**Model:** GPT-5.6 Sol Medium / Opus 4.8 Low

---

# F1 — Mission Boundary Audit + Contract

**Risk: กลาง / สำคัญมาก**

## เป้าหมาย

ก่อนเขียน Engine ต้องรู้ว่า Python ปัจจุบันทำอะไรบ้าง

Inventory เฉพาะ:

```text
_wp_execute
_wp_begin_execute
_wp_advance*
_wp_on_arrived
_wp_wait_*
_wp_run_action
_cancel_navigation
_abort_waypoint_execution
_on_target_reached
failsafe interrupt path
```

และ map เป็น state transition table:

```text
CURRENT PYTHON STATE
EVENT
NEXT STATE
COMMAND SENT
CANCEL GUARD
FAILSAFE BEHAVIOR
```

## ตรวจ request-id ปัจจุบัน

ต้องบันทึกข้อเท็จจริง:

- RPC ไหนมี `request_id`
- RPC ไหนไม่มี
- Python `_rid()` สร้าง UUID ใหม่ตอนไหน
- server `Idempotent` retention/semantics คืออะไร

ห้ามถือว่า system-wide dedup สมบูรณ์เพียงเพราะมี `request_id`

## Deliverable

สร้างเอกสารย่อย/section เช่น:

```text
docs/MISSION_CORE_CUTOVER_CONTRACT.md
```

ต้องระบุ:

- MissionPlan minimal schema
- MissionState
- Run ID
- Start/Cancel semantics
- State query
- safety preemption
- authority cutover rule

## Exit

ไม่มี source flight behavior เปลี่ยน

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F2 — Go Mission Engine Skeleton แบบ SHADOW ONLY

**Risk: กลาง**

## เป้าหมาย

สร้าง `backend/internal/mission` หรือ package ที่เหมาะสม โดยยังไม่ส่ง command

Engine รุ่นแรก:

```text
Load/validate plan
Create run_id
Track state
Track waypoint index
Track participants
Accept telemetry/state observations
Compute expected next transition
Publish mission state
```

แต่:

```text
NO GOTO
NO HOLD
NO TAKEOFF
NO SERVO
NO RTL
```

## Shadow comparison

Python ยัง execute mission เดิม

Go shadow แค่บอก:

```text
expected current waypoint
expected WAIT state
expected next transition
```

Test เปรียบเทียบ Python behavior กับ Go model

## ทำไม F2 ห้ามข้าม

ถ้า cutover authority ทันที แล้ว state machine ใหม่ผิด จะค้นหายากว่าคำสั่งผิดจาก engine หรือ transport

Shadow ทำให้พิสูจน์ logic ก่อนมี flight side effect

## Exit

```text
[ ] Go mission model tests
[ ] state transition tests
[ ] no command dependency side effect
[ ] shadow agrees with characterization scenarios
```

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F3 — Mission RPC + Run ID + Start/Cancel/Query Guard

**Risk: สูง**

## เป้าหมาย

สร้าง boundary ขั้นต่ำที่ UI ใช้ได้:

แนวคิด:

```text
StartMission(plan, operation_id)
CancelMission(run_id, request_id)
GetMissionState()
```

ชื่อ RPC/schema จริงให้ยึด proto convention ของ project

## Required semantics

### Start

- operation/start ID ซ้ำ → ห้ามสร้าง run ซ้ำ
- active run อยู่แล้ว → reject หรือ return existing ตาม contract
- plan ถูก freeze หลัง accepted

### Cancel

- cancel ซ้ำต้อง safe
- cancel run เก่าห้าม cancel run ใหม่
- cancel ต้อง invalidate future transitions ก่อนส่ง/รอ command อื่น

### Query

UI เปิดใหม่ต้องอ่าน:

```text
run_id
state
plan identity
participants
current waypoint
WAIT remaining/state
last transition
terminal/interrupted reason
revision
```

## สำคัญ

ไม่ต้องทำ Command Gateway system-wide

แต่ Mission RPC ใหม่ต้องมี identity/idempotency semantics ของตัวเองชัดเจน

## Exit

```text
[ ] duplicate start test
[ ] duplicate cancel test
[ ] stale run test
[ ] reconnect query test
[ ] no flight command yet หรือ command authority ยัง Python จน F4 cutover
```

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F4 — Waypoint Execution Authority → Go

**Risk: สูงมาก**

## เป้าหมาย

ย้ายเฉพาะ **Waypoint progression** ก่อน

Go Mission Engine เป็นคน:

```text
validate target
issue waypoint GOTO through command.Service/safety
observe Core telemetry
judge arrival
advance current waypoint
cancel / interrupt
publish state
```

Python เป็นคน:

```text
build/edit plan ก่อน START
submit plan
operator cancel intent
render mission state
```

## Safety architecture

```text
Mission Engine
     ↓
command.Service interface
     ↓
safety.Envelope
     ↓
Drone/MAVLink
```

**Mission Engine ห้ามเรียก Drone.Goto โดย bypass command.Service/safety**

## Cutover strategy

1. shadow tests ผ่าน
2. เปิด authority flag เฉพาะ dev/SITL ก่อน
3. เมื่อ Go authority active → Python waypoint executor ต้องไม่ส่ง GOTO
4. compatibility UI ยังอยู่
5. rollback สามารถกลับ Python authority ใน dev ได้ แต่ห้าม dual authority

## Scope แรกที่แนะนำ

เริ่ม:

```text
single route / GROUPED route progression
ไม่มี WAVE
ไม่มี payload ก่อนถ้ายังไม่จำเป็น
```

ถ้า SWARM active:

- Core swarm manager ยังคุม formation/followers
- Mission Engine ต้องกำหนด contract ว่า waypoint target ถูกใช้กับ leader/mission owner อย่างไร
- ห้ามสร้าง formation authority ซ้ำใน Mission Engine

## Exit

```text
[ ] Python UI kill แล้ว Go ยัง advance waypoint ใน SITL ตาม policy
[ ] Cancel หยุด progression
[ ] old Python callback ไม่ส่ง GOTO
[ ] safety reject ทำ mission FAILED/INTERRUPTED ไม่ฝืน retry แบบไม่จำกัด
```

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F5 — WAIT Authority → Go

**Risk: สูงมาก**

## เป้าหมาย

WAIT ต้องไม่พึ่ง QTimer/Python process อีกต่อไป

Flow:

```text
ARRIVE WP
→ Go transition WAITING
→ monotonic deadline/state in Core
→ safety checked continuously/by event
→ WAIT complete
→ next transition
```

Qt timer กลายเป็น display refresh เท่านั้น

## Required behavior

- Cancel ระหว่าง WAIT → invalidate immediately
- battery/link failsafe ระหว่าง WAIT → mission INTERRUPTED; no auto resume
- UI crash ระหว่าง WAIT → WAIT state ยังอยู่ใน Core
- UI restart → query WAIT remaining/state
- stale Python WAIT generation ไม่มี authority

## HOLD semantics

ถ้า WAIT ต้อง Hold:

- command ต้องออกจาก Go through command safety path
- ห้าม frontend ส่ง Hold ซ้ำเพื่อ “ช่วย” Core
- failsafe ต้องมี priority สูงกว่า WAIT

## Exit

```text
[ ] kill UI during WAIT SITL
[ ] restart UI sees same WAIT run
[ ] cancel/failsafe during WAIT
[ ] no post-cancel advance
```

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F6 — Restartable Cockpit สำหรับ Active Mission

**Risk: สูง**

## เป้าหมาย

ทำเฉพาะ lifecycle ที่จำเป็นกับ mission ก่อน ไม่ต้องทำ Core Service architecture เต็ม V1

UI startup/reconnect:

```text
Connect Core
→ GetMissionState
→ if ACTIVE/WAITING:
     rebuild execution display
     bind UI to existing run_id
     DO NOT StartMission
→ if terminal/idle:
     normal UI
```

## ต้องแก้ Python local state assumptions

ระหว่าง active Core mission:

Python fields เก่า เช่น:

```text
_waypoint_executing
_wp_current_index
_wp_waits
_flight_run_id
```

ห้ามเป็น authority

อาจคงไว้เป็น compatibility/read cache ชั่วคราว แต่ต้อง rebuild จาก Core state

## ไม่ต้องทำใน F6

- mission DB persistence
- auto-start Core
- supervisor full-feature
- version negotiation เต็มระบบ

## Exit

```text
[ ] kill Python
[ ] Core continues
[ ] reopen Python
[ ] displays same run
[ ] no duplicate GOTO/START
```

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F6B — WAVE / Payload Conditional Gate

**ทำเฉพาะถ้าการบินจริงรอบแรกต้องใช้ WAVE หรือ mission payload automation**

ถ้าไม่ใช้ → DEFER

ถ้าใช้ → ต้อง migrate authority ที่เกี่ยวข้องก่อนเรียก mode นั้นว่า crash-tolerant

ห้ามอ้างว่า “UI crash safe” สำหรับ WAVE ถ้า WAVE sequencing ยังอยู่ Python

แนะนำ order:

```text
Waypoint + WAIT stable
→ payload action state
→ WAVE group sequencing
```

ไม่ย้ายพร้อมกันใน commit เดียว

**Model:** High ทั้งสองตัว

---

# F7 — Flight-Critical Failure Injection

**Risk: สูงด้าน reasoning แต่ทำใน SITL/dev**

## Required scenarios

### UI failure

1. kill UI ระหว่างเดินทาง WP
2. kill UI ระหว่าง WAIT
3. restart UI ระหว่าง mission
4. freeze UI event loop

Expected:

```text
Core mission deterministic
no duplicate command
UI restart = state query only
```

### Network/UI-Core

5. drop UI↔Core gRPC
6. reconnect
7. duplicate Start request
8. stale Cancel/run event

### Mission safety

9. battery failsafe ระหว่าง transit
10. battery failsafe ระหว่าง WAIT
11. link failsafe ระหว่าง transit/WAIT
12. Cancel ตรง transition boundary

### Core failure

13. stop/kill Go Core ใน SITL
14. restart Core

Expected:

```text
no silent mission resume
FC/local failsafe behavior matches configured policy
```

## Exit

ทุก scenario ต้องมี expected state เขียนก่อน test และผล actual เทียบ expected

**Model:** GPT-5.6 Sol High / Opus 4.8 High

---

# F8 — Targeted SITL Safety Gate

**Risk: กลาง**

V2 ไม่จำเป็นต้องรอ endurance architecture เต็ม V1 ก่อนเริ่ม hardware bench แต่ห้ามข้าม repeated SITL regression

ต้องพิสูจน์อย่างน้อย:

```text
normal route repeatedly
route + WAIT repeatedly
cancel repeatedly
UI kill/restart repeatedly
telemetry/link interruption
failsafe during mission
no command duplication
no run leak / goroutine leak ที่เห็นชัด
```

เก็บ metric จาก HealthMonitor/Core logs ที่มีอยู่แล้ว

ถ้าเจอ memory/goroutine/queue growth แบบต่อเนื่อง → STOP ก่อน hardware

Full long-duration soak ตาม V1 ยังทำภายหลังได้ แต่ mission duration และ failure cycles ที่ใช้ก่อน real flightต้องครอบคลุม workload จริงอย่างสมเหตุสมผล

**Model:** GPT-5.6 Sol Medium สำหรับรัน/สรุป; เปลี่ยน High เมื่อวิเคราะห์ failure / Opus Low → High ตามแบบเดียวกัน

---

# F9 — Hardware Bench Gate

**Risk: สูง**

ก่อน controlled flight:

- verify Core ↔ FC command/ACK
- verify telemetry timing จริง
- verify WAIT/HOLD behavior จริงโดยไม่พึ่ง UI
- verify Cancel
- verify UI disconnect/reconnect โดยไม่สร้าง command ซ้ำ
- verify FC failsafe configuration และผลเมื่อ GCS/Core link หาย ตาม safety procedure ของทีม
- verify manual/safety takeover path ที่ทีมใช้จริง

ห้ามถือว่า SITL timing = hardware timing

ถ้า hardware behavior ต่างจาก SITL ต้องแก้และย้อน F7/F8

**Model:** High ทั้งสองตัว

---

# F10 — Controlled Real Flight Gate

**Risk: สูงสุด**

Real flight ใช้เพื่อ **verify สิ่งที่ผ่าน SITL/bench แล้ว** ไม่ใช่เพื่อค้นหา architecture bug ครั้งแรก

## เพิ่ม complexity ทีละระดับ

แนะนำ conceptually:

```text
R1 basic Core-owned waypoint mission
R2 waypoint + WAIT
R3 UI interruption/reconnect scenario เฉพาะเมื่อ safety setup/team procedure พร้อม
R4 grouped/swarm use case หลัง single mission stable
R5 WAVE/payload เฉพาะเมื่อ F6B ผ่าน
```

แต่ละระดับต้องเป็น checkpoint ที่สามารถหยุดและกลับไปแก้ได้

ห้ามรวม feature ใหม่หลายชนิดในเที่ยวบินเดียวเพื่อประหยัดเวลา

## Flight Gate

ก่อนยอมให้ mode หนึ่งเข้าสู่ flight test ต้องตอบ YES ได้ว่า:

```text
[ ] Authority owner ชัด
[ ] Cancel owner ชัด
[ ] Failsafe priority ชัด
[ ] UI crash behavior ผ่าน SITL
[ ] Core crash behavior + FC failsafe ผ่าน bench/SITL
[ ] Duplicate/stale run tests ผ่าน
[ ] No auto-resume after Core restart
[ ] Logs/audit ระบุ run/transition ได้
```

**Model:** High ทั้งสองตัว

---

# 6. ลำดับ Fast-Track ที่แนะนำจริง

```text
CURRENT: Phase 2C DONE / frontend 965 passed

        ↓
F0  Freeze Fast-Track Baseline
        ↓
F1  Mission Boundary Contract
        ↓
F2  Go Mission Shadow Engine
        ↓
F3  Mission Start/Cancel/Query + Run Identity
        ↓
F4  Waypoint Authority → Go
        ↓
F5  WAIT Authority → Go
        ↓
F6  Restartable Cockpit for Active Mission
        ↓
F7  Failure Injection
        ↓
F8  SITL Safety Gate
        ↓
F9  Hardware Bench
        ↓
F10 Controlled Real Flight

Optional only if needed before first real flight:
F6B WAVE / Payload authority
```

---

# 7. เทียบ V1 กับ V2 — อะไรถูกเลื่อนไปทีหลัง

| V1 Work | V2 ก่อน Flight? | เหตุผล |
|---|---|---|
| More Presentation Controllers | ไม่จำเป็น | Phase 2C boundary เพียงพอสำหรับ fast-track |
| Frontend Telemetry Store | พัก | Mission ใช้ Core telemetry โดยตรง |
| Render coalescing | พัก เว้นแต่เป็น blocker | performance ไม่ใช่ authority |
| Full Python Command Gateway | พัก | Mission ใช้ Go command.Service โดยตรง |
| System-wide Command Dedup | พักบางส่วน | ทำเฉพาะ Mission Start/Cancel/action identity ก่อน |
| Run ID | **ต้องทำ** | stale/reconnect safety |
| Mission Engine → Go | **ต้องทำ** | เป้าหมายหลัก |
| Mission Persistence | พัก | UI restart ใช้ Core memory; Core restart = fail-closed |
| Restartable UI | **ต้องทำเฉพาะ mission state** | แก้ UI crash goal |
| Failure Injection | **ต้องทำ** | พิสูจน์ boundary |
| SITL | **ต้องทำ** | ก่อน hardware |
| Hardware Bench | **ต้องทำ** | timing/failsafe จริง |
| Real Flight | หลัง gates | verification เท่านั้น |
| WAVE migration | conditional | ต้องทำก่อน flight เฉพาะถ้าจะใช้ WAVE จริง |

---

# 8. Fast-Track Architecture Target

ก่อน real-flight fast-track ถือว่าสำเร็จ โครงสร้างต้องอย่างน้อยเป็น:

```text
┌──────────────────────────────────────┐
│ Python Cockpit                       │
│                                      │
│ Plan editor                          │
│ Start/Cancel intent                  │
│ Mission state display               │
│                                      │
│ crash/restart ได้                    │
└──────────────────┬───────────────────┘
                   │ Mission RPC
                   ▼
┌──────────────────────────────────────┐
│ Go Core                              │
│                                      │
│ Mission Run ID                       │
│ Waypoint state machine               │
│ WAIT state                           │
│ Cancel/Interrupt                     │
│ Mission state query                  │
│                                      │
│ command.Service                      │
│ safety / failsafe                    │
└──────────────────┬───────────────────┘
                   │ MAVLink
                   ▼
┌──────────────────────────────────────┐
│ Flight Controller                    │
│ stabilization + onboard failsafe     │
└──────────────────────────────────────┘
```

หลัง UI crash:

```text
UI ✖
Core Mission ✓
FC ✓
```

หลัง Core crash:

```text
UI ?
Core ✖
mission must NOT auto-resume
FC onboard failsafe = final safety layer
```

---

# 9. Coding Rules สำหรับ Fast-Track

1. **No rewrite** — ห้าม rewrite `app.py` หรือ command package ทั้งก้อน
2. **One authority cutover per checkpoint**
3. **Shadow before authority**
4. **No dual executor**
5. **Mission Engine must use safety command path**
6. **No auto-resume after Core restart**
7. **No Python timer as mission authority หลัง cutover**
8. **Every async transition must carry/check run identity**
9. **Every completed F-phase must leave a runnable/tested checkpoint**
10. **Update `ARCHITECTURE_MIGRATION_PROGRESS.md` continuously**
11. ถ้า AI quota ใกล้หมด → หยุด source ใหม่ก่อนและเขียน exact handoff
12. ห้ามแก้ failing safety test ให้ผ่านด้วยการลด assertion

---

# 10. Progress MD Format เพิ่มสำหรับ V2

เมื่อเริ่ม V2 ให้ Progress MD เพิ่มหัวข้อ:

```md
## Active Roadmap
REAL_FLIGHT_SAFETY_FAST_TRACK_V2

## Fast-Track Phase
F0 / F1 / ...

## Flight Authority Matrix
| Subsystem | Current Authority | Target Authority | Cutover Status |

## Protected Real-Flight Modes
- Waypoint: YES/NO
- WAIT: YES/NO
- Swarm formation: YES/NO
- WAVE: YES/NO
- Payload automation: YES/NO

## Last Safe Runnable Checkpoint

## Current WIP — NOT SAFE CHECKPOINT if applicable

## Exact Next Step

## Tests / Failure Scenarios Passed
```

คำว่า `Protected Real-Flight Modes` สำคัญ: ห้ามเหมารวมว่าทั้งโปรแกรม crash-tolerant ถ้าย้ายแค่บาง mode

---

# 11. Stop Conditions

หยุด Fast-Track phase ทันทีถ้า:

- Python และ Go สามารถ execute mission เดียวพร้อมกัน
- mission code bypass safety envelope
- old run สามารถ advance new run
- Cancel แล้วมี GOTO/action หลุดตามมา
- failsafe แล้ว mission ยังออกคำสั่งทับ
- UI reconnect แล้ว START mission ซ้ำ
- Core restart แล้ว mission resume เอง
- state query ไม่สามารถบอก authority/current run ได้
- Go goroutine/timer leak ต่อ mission
- test ต้องแก้ assertion safety เดิมเพื่อให้ผ่าน
- diff ลามไป unrelated UI/telemetry cleanup มากเกิน scope

หลักคือ:

```text
ถ้าต้องแก้หลายระบบพร้อมกันเพื่อให้หนึ่ง phase ผ่าน
→ phase ใหญ่เกินไป
→ แยกให้เล็กลง
```

---

# 12. Definition of Fast-Track Success

V2 **ไม่จำเป็นต้องทำ V1 จบทั้งหมด**

Fast-Track ถือว่าสำเร็จเมื่อ mode ที่จะเอาไป controlled real flight ผ่านข้อเหล่านี้:

```text
[ ] mission authority อยู่ Go สำหรับ mode นั้น
[ ] Python UI ไม่ใช่ owner ของ waypoint/WAIT progression
[ ] UI crash ไม่ลบ active mission state ใน Core
[ ] UI restart query/rebuild state ได้
[ ] duplicate Start ไม่สร้าง mission ซ้ำ
[ ] stale callback ไม่มี authority
[ ] Cancel/failsafe preempt mission ได้
[ ] Core crash ไม่ auto-resume mission
[ ] FC onboard failsafe ได้ verify บน hardware
[ ] failure injection ผ่านใน SITL
[ ] hardware bench ผ่าน
[ ] controlled flight เริ่มจาก scope ที่พิสูจน์แล้วเท่านั้น
```

หลังจากนั้นกลับไป V1 เพื่อทำ:

```text
Telemetry Store
Full Command Gateway
System-wide Dedup
Mission Persistence
WAVE/full mission migration
Long endurance
Remaining controllers
Performance cleanup
Full recovery/service hardening
```

โดยไม่ทิ้งงาน V2 — V2 กลายเป็นฐาน safety ของ V1 ต่อไป

---

# 13. คำสั่งสำหรับ AI ตัวถัดไป

ก่อนลงมือ Fast-Track:

```text
1. Read docs/REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md completely
2. Read docs/ARCHITECTURE_MIGRATION_PROGRESS.md completely
3. Read docs/LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md for deferred context
4. Inspect git status/diff — do not overwrite WIP
5. Start F0 only
6. Update Progress MD before/after every material checkpoint
7. Never jump directly to F4 code without F1 contract + F2 shadow + F3 run boundary
```

**V2 เป็นการลัดลำดับงานที่ไม่จำเป็นต่อ flight-failure isolation ไม่ใช่การลัด safety gates**
