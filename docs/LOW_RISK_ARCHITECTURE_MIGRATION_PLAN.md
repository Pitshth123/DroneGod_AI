# SwarmGod / DroneGod — LOW-RISK ARCHITECTURE MIGRATION PLAN

> สถานะ: **PLAN ONLY — ห้ามใช้เอกสารนี้เป็นเหตุผลในการ refactor ทั้งระบบรวดเดียว**
> วันที่: 27 สิงหาคม 2026
> เป้าหมาย: ลดความเสี่ยงจาก `frontend/swarmgod_gui/app.py` ที่มีหน้าที่มากเกินไป โดยค่อย ๆ แยก responsibility, เพิ่ม observability/recovery, ลดโอกาส UI freeze/crash กระทบการบิน และทำให้ AI อ่าน/แก้โค้ดได้แม่นขึ้น
> หลักสำคัญ: **พฤติกรรมการบินเดิมต้องเปลี่ยนน้อยที่สุดในแต่ละเฟส และหนึ่งเฟสเปลี่ยน authority ได้ไม่เกินหนึ่งเรื่อง**

---

# 1. เป้าหมายสุดท้าย

ต้องการให้สถาปัตยกรรมไปถึงรูปแบบนี้โดยไม่ rewrite ทั้งระบบ:

```text
┌──────────────────────────────────────────────┐
│          Python / PyQt Cockpit UI            │
│                                              │
│ View / Map / Panels / Operator Input         │
│ Read-only presentation state where possible  │
│                                              │
│ ถ้า UI ค้าง/ปิด → safety/mission authority   │
│ ต้องไม่หายไปพร้อม UI                         │
└───────────────────┬──────────────────────────┘
                    │ gRPC / mTLS
                    ▼
┌──────────────────────────────────────────────┐
│                  Go Core                     │
│                                              │
│ Command Gateway / Validation                 │
│ Run ID / Command ID / Dedup                  │
│ Mission Engine                               │
│ Swarm Authority                              │
│ Safety / Failsafe Authority                  │
│ Mission State / Persistence                  │
│ Health / Audit                               │
└───────────────────┬──────────────────────────┘
                    │ MAVLink
                    ▼
┌──────────────────────────────────────────────┐
│             ArduPilot / FC                   │
│ Local stabilization + onboard failsafe       │
└──────────────────────────────────────────────┘
```

หลักการสำคัญคือ:

```text
Python UI ตาย ≠ ระบบ safety ตาย
Python UI restart ≠ mission ถูกเริ่มใหม่
Command retry ≠ command execute ซ้ำ
Telemetry burst ≠ UI queue โตไม่จบ
Core restart ≠ mission เก่าถูก resume อัตโนมัติโดยไม่ยืนยัน
```

---

# 2. สิ่งที่ห้ามทำ

แผนนี้เน้น "Low Risk" ดังนั้นห้ามทำสิ่งต่อไปนี้ใน refactor รอบเดียว:

1. ห้าม rewrite `app.py` ทั้งไฟล์
2. ห้ามย้าย Waypoint + WAVE + Swarm + RTL + Preflight พร้อมกัน
3. ห้าม refactor UI และเปลี่ยน flight behavior ใน PR/ชุดแก้เดียวกัน
4. ห้ามเปลี่ยน gRPC contract โดยไม่จำเป็นในเฟสแรก ๆ
5. ห้ามย้าย safety decision จาก Go Core กลับมาฝั่ง Python
6. ห้ามลบ compatibility method เดิมทันทีหลังแยก Controller
7. ห้าม rename method จำนวนมากเพื่อความสวยงามในช่วง migration
8. ห้ามเพิ่ม thread ใหม่โดยไม่มี ownership/cancellation lifecycle ชัดเจน
9. ห้าม auto-restart mission หลัง Core/UI crash
10. ห้ามใช้ watchdog เป็นตัว "เดา" หรือส่งคำสั่งบินเอง
11. ห้ามแก้ test ให้ผ่านด้วยการลด safety assertion
12. ห้ามทำ architecture migration พร้อม feature ใหญ่ที่ยัง WIP อยู่

---

# 3. สถานะปัจจุบันที่ต้องคำนึงถึง

ปัจจุบัน `GroundStation` ใน `frontend/swarmgod_gui/app.py` ถือ state จำนวนมาก เช่น:

- telemetry/latest state
- selection/fleet UI
- map state
- swarm/head state
- RTL state
- waypoint routes/execution
- WAIT generation/timers
- WAVE
- pre-flight / execution summary
- Field Tablet bridge
- UI/REMOTE state

ขณะเดียวกันระบบมีการแยก pure logic ออกไปบางส่วนแล้ว เช่น:

- `core/waypoint_logic.py`
- `core/swarm_logic.py`
- `core/flight_progress.py`
- Go Core แยก fleet/safety/swarm/telemetry อยู่แล้ว

ดังนั้น **ไม่ควรเริ่มจาก rewrite** แต่ควรใช้โครงสร้างที่มีอยู่เป็นฐานแล้วค่อยดึง orchestration ออกจาก `GroundStation` ทีละ subsystem

> หมายเหตุ ณ วันที่จัดทำแผน: repository มี WIP หลายไฟล์ รวมถึง `app.py`, Waypoint, Swarm และ failsafe ดังนั้นการเริ่ม architecture migration ต้องรอให้ชุด feature ที่กำลังทำอยู่มี baseline/test result ที่ชัดก่อน เพื่อไม่ให้ refactor ชนกับ feature work

---

# 4. กฎกลางของทุก Phase

ทุก Phase ต้องผ่าน Gate เหล่านี้ก่อนเริ่ม Phase ถัดไป

## Gate A — Behavior Compatibility

- UI behavior เดิมที่ไม่เกี่ยวกับ Phase ต้องเหมือนเดิม
- RPC/command order เดิมต้องเหมือนเดิม เว้นแต่ Phase ระบุชัดว่ากำลังเปลี่ยน authority
- Safety/failsafe เดิมต้องไม่ลดลง

## Gate B — Tests

อย่างน้อยต้องมี:

- targeted tests ของ subsystem ที่แก้
- regression tests ของจุดที่มี side effect ใกล้เคียง
- full frontend suite เมื่อจบ Phase
- `go test ./...` เมื่อ Phase แตะ Go

## Gate C — Rollback

แต่ละ Phase ต้องสามารถ rollback ได้โดย:

- revert เฉพาะไฟล์/commit ของ Phase นั้น
- ไม่ต้องย้อน schema/mission state หลายเวอร์ชันพร้อมกัน
- compatibility wrapper ต้องอยู่จนกว่าจะผ่านอย่างน้อย 1 Phase ถัดไป

## Gate D — Observability

ก่อนเปลี่ยน behavior ต้องมี log/metric ที่ตอบได้ว่า:

- command ใครส่ง
- command ไหนถูก execute
- run ไหนเป็นเจ้าของ callback
- telemetry ล่าสุดอายุเท่าไร
- UI event loop stall หรือไม่
- Core connection state เป็นอย่างไร

## Gate E — One Authority Change

**หนึ่ง Phase ย้าย authority ได้ไม่เกินหนึ่ง subsystem**

ตัวอย่าง:

- ย้าย Waypoint execution → Go ได้
- แต่ห้ามย้าย Waypoint + WAVE + RTL พร้อมกัน

---

# 5. ลำดับที่แนะนำจริง

ลำดับเดิม:

```text
app.py ใหญ่
↓
แยก Controller
↓
Command Gateway
↓
Run ID + Dedup
↓
UI Watchdog
↓
Telemetry Store
↓
Mission Engine → Go
...
```

เพื่อให้กระทบน้อยที่สุด แนะนำปรับเป็น:

```text
PHASE 0  Freeze Baseline + Characterization Tests
    ↓
PHASE 1  Observability + UI Watchdog (observe only)
    ↓
PHASE 2  Extract Presentation Controllers ทีละตัว
    ↓
PHASE 3  Telemetry Store แบบ Shadow / Read Model
    ↓
PHASE 4  Command Gateway แบบ Pass-through
    ↓
PHASE 5  Run ID + Command ID + Dedup
    ↓
PHASE 6  Mission Engine → Go ทีละ subsystem
    ↓
PHASE 7  Mission Persistence
    ↓
PHASE 8  Core Service + Restartable UI
    ↓
PHASE 9  Failure Injection
    ↓
PHASE 10 SITL Endurance
    ↓
PHASE 11 Hardware Bench
    ↓
PHASE 12 Controlled Real Flight
```

เหตุผลที่ `UI Watchdog` และ observability ถูกเลื่อนมาก่อน refactor คือถ้าเริ่มแยกโค้ดโดยยังวัดไม่ได้ว่า UI stall/queue/backlog เกิดตรงไหน จะไม่รู้ว่า refactor ทำให้ดีขึ้นหรือแย่ลง

---

# 6. PHASE 0 — Freeze Baseline + Characterization Tests

**Risk: ต่ำที่สุด**

## เป้าหมาย

ยังไม่เปลี่ยน architecture และไม่เปลี่ยน flight behavior

สร้าง "เส้นฐาน" ที่เอาไว้เทียบทุก Phase หลังจากนี้

## ต้องทำ

1. รอ feature WIP ปัจจุบันให้จบ/นิ่งก่อน
2. บันทึก Git commit/tag หรืออย่างน้อย baseline commit ที่รู้ว่า test ผ่าน
3. บันทึกจำนวน frontend tests ที่ผ่านจริง
4. บันทึกผล `go test ./...`
5. ทำ architecture inventory ของ `GroundStation`

แยก method/state ใน `app.py` เป็นหมวดโดยยังไม่ย้ายโค้ด:

```text
UI construction
Fleet selection
Telemetry render
Map
Waypoint planning
Waypoint execution
WAVE
Swarm
RTL
Preflight
Execution summary
Field tablet
Dialogs/settings
Command/RPC adapters
```

6. เพิ่ม characterization test เฉพาะ behavior สำคัญที่ยังไม่มี test

## Characterization test คืออะไร

ไม่พยายามออกแบบ behavior ใหม่

เพียงล็อกว่า:

```text
input แบบนี้
→ ปัจจุบันได้ output / command sequence แบบนี้
```

เพื่อให้ย้าย method แล้วรู้ทันทีว่าพฤติกรรมเปลี่ยนหรือไม่

## ห้าม

- ห้ามย้าย method
- ห้าม rename
- ห้ามปรับ architecture
- ห้าม optimize

## Exit Criteria

```text
[ ] baseline commit/reference ชัด
[ ] frontend full suite ผ่าน
[ ] Go full suite ผ่าน
[ ] subsystem inventory เสร็จ
[ ] critical command paths มี characterization tests
```

---

# 7. PHASE 1 — Observability + UI Watchdog แบบ Observe Only

**Risk: ต่ำมาก**

นี่ควรเป็นการเปลี่ยน code จริงชุดแรก

## เป้าหมาย

รู้ว่าโปรแกรม "ค้างเพราะอะไร" ก่อนเริ่มแก้ performance

Watchdog ระยะแรก **ห้าม restart, ห้าม cancel mission, ห้ามส่ง flight command**

ทำแค่ตรวจจับและ log

## เพิ่มข้อมูลอย่างน้อย

```text
ui_heartbeat_ms
ui_stall_duration_ms
core_connected
telemetry_age_ms per drone
telemetry_events_received/sec
ui_render_count/sec
pending_ui_calls (ถ้าวัดได้)
last_rpc_name
last_rpc_duration_ms
last_operator_action
memory/RSS periodic sample (low frequency)
```

## UI Watchdog concept

Qt timer บน main thread + observer ที่วัด heartbeat จากคนละ execution contextอย่างปลอดภัย

สถานะเช่น:

```text
HEALTHY
DEGRADED
STALLED
```

แต่ Phase นี้:

```text
STALLED → LOG + UI diagnostic เท่านั้น
```

ห้าม:

```text
STALLED → RTL
STALLED → HOLD
STALLED → restart UI
```

เพราะ watchdog ยังไม่ควรถือ flight authority

## ต้องระวัง

watchdog เองต้อง lightweight มาก ห้ามกลายเป็นต้นเหตุของ freeze

## Exit Criteria

```text
[ ] สามารถจำลอง UI stall ใน test/dev ได้
[ ] log ระบุ stall duration ได้
[ ] telemetry age ถูกวัดได้
[ ] watchdog ไม่ส่ง flight command
[ ] full regression ผ่าน
```

## ประโยชน์ต่อ AI

เมื่อ AI แก้ performance รอบหลัง จะมี metric ให้เทียบ ไม่ต้องเดาว่า "น่าจะเร็วขึ้น"

---

# 8. PHASE 2 — Extract Presentation Controllers ทีละตัว

**Risk: ต่ำ → กลาง**

นี่คือจุดเริ่มลดขนาด `app.py` จริง แต่ **ยังไม่ย้าย flight authority**

## หลักการ

ใช้วิธี Compatibility-First Extraction

ก่อน:

```python
GroundStation._some_method(...)
```

หลังช่วงแรก:

```python
GroundStation._some_method(...):
    return self.some_controller.some_method(...)
```

call site เดิมยังไม่ต้องเปลี่ยนพร้อมกันทั้งหมด

นี่ทำให้ rollback ง่ายและลด diff

## ห้ามเริ่มจากอะไร

ห้ามเริ่ม extraction จาก:

- RTL state machine
- failsafe
- WAVE execution
- Waypoint runtime
- Takeoff orchestration

เพราะมี safety/async callback สูง

## ลำดับ Controller ที่ควรแยก

### 2A — Presentation/Formatting ก่อน

ตัวอย่าง:

```text
frontend/swarmgod_gui/controllers/
    __init__.py
    summary_presenter.py
    fleet_presenter.py
```

ย้ายเฉพาะ:

- formatting
- label generation
- rendering helper
- enable/disable state ที่ไม่มี command side effect

ไม่ย้าย RPC

### 2B — Map Presentation Adapter

แยก logic การ render marker/polyline/label ออกจาก business decision

โครงสร้างแนวคิด:

```text
MapController
  render_drone(...)
  render_waypoint(...)
  render_route(...)
  render_target(...)
```

แต่คำตัดสินว่า "จะบินไปไหน" ยังอยู่นอก MapController

### 2C — Preflight/Execution Summary Presenter

`flight_progress.py` เป็น pure model อยู่แล้ว จึงเหมาะกับการแยก presentation orchestration ออกจาก `GroundStation`

## Controller ต้องไม่ทำอะไร

Controller รุ่นแรกห้ามมี:

```text
client.takeoff()
client.rtl()
client.goto()
client.swarm_start()
```

ถ้าจะส่ง command ต้องย้อนกลับไป entry point เดิมก่อน จนถึง Phase Command Gateway

## Exit Criteria ต่อ Controller

```text
[ ] ย้ายทีละ controller
[ ] targeted tests ผ่าน
[ ] GroundStation compatibility wrapper ยังอยู่
[ ] command order ไม่เปลี่ยน
[ ] ไม่มี authority ใหม่ใน controller
[ ] diff ต่อรอบเล็กและ review ได้
```

## เป้าหมายขนาดงาน

อย่าตั้งเป้า "ลด app.py ให้เหลือ 2,000 บรรทัด" ในครั้งเดียว

ให้ตั้งเป้า:

```text
1 migration = 1 responsibility
```

AI จะเริ่มอ่าน code ได้ง่ายขึ้นตั้งแต่ controller แรกโดยไม่ต้องรอ migration เสร็จทั้งหมด

---

# 9. PHASE 3 — Telemetry Store แบบ Shadow / Read Model

**Risk: ต่ำ → กลาง**

## ปัญหาปัจจุบัน

`GroundStation` มี dictionary telemetry/state หลายชุด

ถ้า telemetry handler รับ event แล้ว update widget โดยตรงมากเกินไป อาจเกิด:

- UI render ถี่เกิน
- Qt queue backlog
- stale state หลายชุดไม่ตรงกัน
- AI ต้องตาม state หลายตัวใน `app.py`

## เป้าหมาย

สร้าง pure/read-oriented store เช่น:

```text
frontend/swarmgod_gui/core/telemetry_store.py
```

แนวคิด:

```python
store.update(telemetry)
latest = store.snapshot()
```

## วิธี migrate แบบไม่เสี่ยง

### Step 3.1 — Shadow Write

Telemetry เข้าแล้ว:

```text
update state เดิม
+ update TelemetryStore
```

แต่ UI **ยังอ่าน state เดิม**

เปรียบเทียบ state เดิมกับ store ใน test/debug

### Step 3.2 — Switch Read ทีละ widget

เช่น:

```text
Battery label → Store
GPS label → Store
Map marker → Store
Selected card → Store
```

ทีละส่วน

### Step 3.3 — Render Coalescing

แยก:

```text
Telemetry ingest rate
```

ออกจาก

```text
UI render rate
```

UI ไม่จำเป็นต้อง repaint ทุก telemetry packet

ใช้ latest snapshot ที่ render cadence จำกัดและวัดได้

## สำคัญ

Safety/Core decision **ห้ามใช้ telemetry ที่ถูก throttle เพื่อ UI**

Telemetry Store นี้เป็น frontend read model เท่านั้น

## Exit Criteria

```text
[ ] shadow comparison ผ่าน
[ ] ไม่มี telemetry source-of-truth เพิ่มฝั่ง safety
[ ] UI render rate วัดได้
[ ] queue/backlog ไม่โตต่อเนื่องใน soak test
[ ] widgets ถูก migrate ทีละส่วน
```

---

# 10. PHASE 4 — Command Gateway แบบ Pass-through

**Risk: กลาง**

นี่เป็น Phase สำคัญมาก แต่รอบแรกยัง **ห้ามเปลี่ยน command semantics**

## เป้าหมาย

จากเดิมที่หลาย method อาจเรียก `self.client.*` ให้ค่อย ๆ วิ่งผ่านจุดเดียว

แนวคิด:

```text
UI / Controller
     ↓
CommandGateway
     ↓
CoreClient
     ↓
gRPC
```

## Version แรกต้องเป็น Pass-through

เช่น:

```python
gateway.execute("rtl", ids, lambda: client.rtl(...))
```

Gateway รอบแรกทำแค่:

- structured log
- timestamp
- operator action context
- duration
- result/error normalization

**ยังไม่ retry เอง และยังไม่ dedup เอง**

## Migration Strategy

ย้าย call site ทีละ command family:

```text
1. non-flight / read-only RPC
2. configuration command
3. low-risk navigation helper
4. ARM/TAKEOFF/LAND/RTL
5. swarm/mission commands
```

คำสั่ง safety-critical ย้ายทีหลังสุด

## Compatibility

`GroundStation` method เดิมยังอยู่:

```text
_on_rtl_clicked()
→ CommandGateway
```

ไม่ต้องเปลี่ยน widget signal tree ทั้งก้อน

## Exit Criteria

```text
[ ] ทุก command family ที่ migrate มี test เทียบ call order เดิม
[ ] Gateway ไม่ retry โดยอัตโนมัติ
[ ] Gateway ไม่ตัดสิน failsafe
[ ] RPC error surface เหมือนเดิมหรือดีขึ้นโดยมี test
[ ] audit correlation พร้อมสำหรับ Phase 5
```

---

# 11. PHASE 5 — Run ID + Command ID + Dedup

**Risk: กลาง → สูง**

ทำหลัง Command Gateway เท่านั้น เพราะก่อนหน้านั้น command entry points ยังกระจายเกินไป

## เป้าหมาย

ป้องกัน:

```text
retry
stale callback
double click
reconnect
network timeout
```

แล้ว command เดิม execute ซ้ำโดยไม่ตั้งใจ

## ID ที่ควรแยกความหมาย

```text
operation_run_id   = ภารกิจ/operation หนึ่งรอบ
command_id         = command หนึ่งคำสั่ง
sequence           = ลำดับภายใน run ถ้าจำเป็น
```

## Source of Truth

**Dedup ที่เกี่ยวกับ flight command ควร enforce ใน Go Core**

Python สามารถ generate/carry ID ได้ แต่ไม่ควรเป็น authority เดียว เพราะ Python อาจ restart

## Migration แบบปลอดภัย

### Step 5.1 — Correlation Only

เพิ่ม ID ใน log/context ก่อน แต่ Core ยังไม่ reject duplicate

### Step 5.2 — Observe Duplicate

Core detect duplicate แล้ว log/metric แต่ยังเทียบ behavior เดิม

### Step 5.3 — Enforce ทีละ command class

เริ่มจาก command ที่ semantics ชัดก่อน

ห้ามเปิด dedup enforcement ทุก command พร้อมกัน

## ต้องกำหนดชัด

แต่ละ command เป็น:

```text
idempotent
non-idempotent
replace-current
cancel-current
```

ห้ามใช้ dedup policy เดียวกับทุกคำสั่ง

## Exit Criteria

```text
[ ] duplicate command test ผ่าน
[ ] timeout + retry test ผ่าน
[ ] stale run callback test ผ่าน
[ ] UI restart ไม่สร้าง run เดิมใหม่
[ ] command audit trace จาก UI → Core ได้
```

---

# 12. PHASE 6 — Mission Engine → Go ทีละ Subsystem

**Risk: สูง — ต้องเริ่มเมื่อ Phase 0–5 นิ่งแล้วเท่านั้น**

นี่คือ Phase ที่ทำให้ "Python ตายแต่ mission/safety state ไม่หายตาม UI" ได้จริง

## ห้ามย้ายทั้ง mission ทีเดียว

แนะนำลำดับ:

```text
6A Waypoint execution
6B WAIT state
6C WAVE orchestration
6D higher-level mission composition
```

แต่การรวม/แยกจริงต้องอิง source ณ วันที่ลงมือ และห้ามทำหลาย authority พร้อมกัน

## Pattern ที่แนะนำ: Shadow Engine → Authority Cutover

### Stage A — Python ยังเป็น Authority

Go คำนวณ state แบบ shadow แต่ **ห้ามส่ง command**

เปรียบเทียบ:

```text
Python expected next state
vs
Go shadow next state
```

### Stage B — Go Authority สำหรับ subsystem เดียว

เช่น Waypoint เท่านั้น

Python กลายเป็น:

```text
PLAN → START
DISPLAY STATE
CANCEL
```

Go เป็นคน:

```text
validate
advance
wait
cancel
interrupt
```

### Stage C — ลบ Python executor หลัง soak/regression ผ่าน

อย่าลบ executor เก่าทันทีวันเดียวกับ cutover

## State Machine ต้อง explicit

ตัวอย่างแนวคิด:

```text
IDLE
VALIDATING
READY
EXECUTING
WAITING
INTERRUPTED
CANCELLING
COMPLETED
FAILED
```

ทุก transition ต้องมีเหตุผลจาก:

```text
operator command
Core event
telemetry condition
failsafe
```

ไม่ใช่จาก UI timer โดยลำพัง

## Safety Priority

Mission Engine ต้อง yield ให้:

```text
E-STOP
Battery failsafe
Link failsafe
Geofence/safety policy
Operator cancel
```

ตาม policy ที่กำหนด

## Exit Criteria

```text
[ ] kill Python ระหว่าง mission แล้ว Go state ยัง deterministic
[ ] เปิด UI ใหม่แล้วอ่าน current state ได้
[ ] stale Python callback ไม่มี authority
[ ] Core failsafe override mission ได้
[ ] ไม่ auto-resume mission หลัง safety interrupt
```

---

# 13. PHASE 7 — Mission Persistence

**Risk: กลาง → สูง**

ทำหลัง Mission Engine มี owner ชัดแล้ว

## สิ่งที่ควร persist

อย่างน้อย:

```text
run_id
mission definition / immutable snapshot
current state
current waypoint/group
last transition
created_at / updated_at
terminal reason
```

## สิ่งที่ไม่ควร persist แบบเอาไป execute ตรง ๆ

- Qt timer object
- callback
- raw UI state
- transient pointer/reference

## Recovery Policy

UI restart:

```text
Core ยังทำงาน
→ UI reconnect
→ query current state
→ render state เดิม
```

Core restart:

```text
โหลด persisted run
→ RECOVERY_REQUIRED / INTERRUPTED
→ ห้าม auto-resume flight command โดยอัตโนมัติ
```

เว้นแต่ในอนาคตมี policy ที่ผ่านการออกแบบ/ทดสอบเฉพาะ

## Exit Criteria

```text
[ ] crash/restart persistence test
[ ] corrupted/partial state handling
[ ] atomic write/transaction semantics
[ ] no silent auto-resume
```

---

# 14. PHASE 8 — Core Service + Restartable UI

**Risk: กลาง**

Go Core ปัจจุบันเป็น process แยกอยู่แล้ว แต่ Phase นี้ทำ lifecycle ให้ชัดและ operationally reliable

## เป้าหมาย

```text
UI process lifecycle ≠ Core process lifecycle
```

UI ปิด/ค้างไม่ควร kill Core โดยอัตโนมัติ

## ต้องมี

- Core health endpoint/state
- version/build compatibility check
- reconnect backoff
- UI startup state sync
- stale session detection
- clear banner เมื่อ Core unavailable
- Core process logs แยกจาก UI logs

## Supervisor

ถ้าจะมี supervisor ต้องเริ่มจาก monitor-only ก่อน

ต่อมาค่อยพิจารณา restart policy

แต่:

```text
Core crash → restart process
```

ไม่เท่ากับ

```text
Core restart → resume mission อัตโนมัติ
```

สองเรื่องนี้ต้องแยกกัน

## Exit Criteria

```text
[ ] kill UI แล้ว Core อยู่
[ ] restart UI แล้ว reconnect ได้
[ ] UI reconstruct current state จาก Core
[ ] incompatible Core/UI version ถูก reject ชัดเจน
[ ] Core restart เข้า safe recovery state
```

---

# 15. PHASE 9 — Failure Injection

**Risk: ต่ำต่อ production ถ้าทำใน SITL/dev เท่านั้น แต่มีคุณค่ามาก**

เริ่มหลัง architecture มี boundary ที่พอทดสอบได้

## Scenario หลัก

### UI Failure

- freeze Qt main thread ชั่วคราวใน test harness
- kill Python UI
- restart UI
- flood UI with telemetry
- map/WebEngine crash

### RPC/Network Failure

- delay RPC response
- drop connection
- duplicate request
- reconnect during mission
- stale response arrives after cancel

### Core Failure

- graceful stop
- forced process exit
- restart with active persisted run

### Telemetry Failure

- telemetry stale
- burst
- out-of-order if transport/path permits
- one drone stale while others healthy

### Mission Failure

- cancel during WAIT
- safety interrupt at state transition boundary
- leader failsafe
- follower failsafe
- WAVE group transition interruption

## ทุก test ต้องมี Expected Safe State

ห้าม test แบบ "ลอง kill แล้วดูว่าเป็นยังไง"

ต้องกำหนดก่อนว่า:

```text
Failure X
→ component Y หยุด
→ Core state Z
→ FC expected mode ...
→ mission resume? NO/YES ตาม policy
```

---

# 16. PHASE 10 — SITL Endurance

**Risk: ต่ำ**

## เป้าหมาย

จับ bug ที่ unit/integration test ไม่เห็น เช่น:

- memory leak
- queue growth
- timer leak
- goroutine leak
- thread leak
- reconnect churn
- log growth
- event ordering race

## รอบทดสอบแนะนำ

เพิ่มเวลาแบบขั้นบันได:

```text
30 นาที
2 ชั่วโมง
6 ชั่วโมง
12 ชั่วโมง
24 ชั่วโมง
```

ไม่จำเป็นต้องกระโดดไป 24 ชั่วโมงทันที

## Metrics

```text
Python RSS
Go RSS
thread count
goroutine count
UI heartbeat latency
telemetry age
render rate
RPC latency
error rate
reconnect count
mission state transitions
```

## Pass Criteria

ต้องกำหนด threshold จาก baseline จริง ไม่เดาตัวเลขล่วงหน้า

สิ่งสำคัญคือไม่มีแนวโน้มโตแบบ unbounded

---

# 17. PHASE 11 — Hardware Bench

**Risk: สูงกว่า SITL แต่ยังไม่บินจริง**

ทำบนโต๊ะ/พื้นที่ควบคุมตามขั้นตอนความปลอดภัยของทีม

เป้าหมายตรวจ:

- real FC timing
- real telemetry cadence
- USB/network adapter behavior
- reconnect behavior
- power-cycle behavior
- UI/Core recovery
- command acknowledgement timing
- failsafe state reporting

ห้ามใช้ Hardware Bench เพื่อทดแทน SITL regression

SITL ต้องผ่านก่อนทุกครั้งที่มี architecture change ใหญ่

---

# 18. PHASE 12 — Controlled Real Flight

**Risk: สูงสุด**

เริ่มหลัง:

```text
Unit
→ Integration
→ Full regression
→ Failure injection
→ SITL endurance
→ Hardware bench
```

ผ่านตาม release checklist

## ใช้วิธีเพิ่ม complexity ทีละขั้น

ตัวอย่างแนวคิด:

```text
single drone basic
→ single drone mission
→ multi-drone basic
→ swarm basic
→ waypoint
→ WAIT
→ WAVE / advanced behavior
```

อย่าทดสอบทุก feature ใหม่พร้อมกันใน flight แรกหลัง architecture migration

---

# 19. โครงสร้างไฟล์เป้าหมายฝั่ง Python

นี่เป็น "direction" ไม่ใช่คำสั่งให้สร้างทุกไฟล์พร้อมกัน

```text
frontend/swarmgod_gui/
│
├── app.py                      # composition root / MainWindow
│
├── controllers/
│   ├── summary_presenter.py
│   ├── fleet_presenter.py
│   ├── map_controller.py
│   ├── waypoint_controller.py     # เพิ่มเมื่อพร้อม ไม่ใช่เฟสแรก
│   └── preflight_controller.py
│
├── core/
│   ├── telemetry_store.py
│   ├── command_gateway.py
│   ├── health_monitor.py
│   ├── waypoint_logic.py
│   ├── swarm_logic.py
│   └── flight_progress.py
│
└── widgets/
    └── ...
```

## เป้าหมายของ `app.py`

สุดท้ายควรทำหน้าที่ประมาณ:

```text
Create widgets
Create controllers
Wire signals
Wire Core connection
Route top-level events
Manage process/window lifecycle
```

ไม่ใช่:

```text
Mission state machine
Safety policy
Command retry policy
Waypoint execution engine
WAVE engine
Swarm flight authority
```

---

# 20. โครงสร้างเป้าหมายฝั่ง Go

อย่าสร้างทั้งหมดตั้งแต่แรก

เมื่อถึง Phase Mission Engine จึงค่อยพิจารณา:

```text
backend/internal/
├── command/
│   ├── service.go
│   └── dedup.go
│
├── mission/
│   ├── engine.go
│   ├── state.go
│   ├── waypoint.go
│   └── persistence.go
│
├── fleet/
├── swarm/
├── safety/
└── telemetry/
```

Boundary สำคัญ:

```text
mission → ขอ command ผ่าน interface
safety → สามารถ reject/interrupt mission
swarm → ห้ามยิง command ทับ active failsafe
```

ห้าม mission engine bypass safety envelope เพื่อความสะดวก

---

# 21. วิธีทำให้ AI เขียนโค้ดได้ดีขึ้นทุก Phase

Architecture migration นี้ไม่ได้ทำเพื่อความสวยงามอย่างเดียว

ทุก Phase ควรลด "context radius" ของงาน

ก่อน:

```text
แก้ Waypoint
→ AI ต้องอ่าน app.py ก้อนใหญ่
→ state จำนวนมาก
→ side effect ไม่ชัด
```

หลัง:

```text
แก้ Waypoint UI
→ waypoint_controller.py
→ waypoint_logic.py
→ tests waypoint
```

หรือ:

```text
แก้ Mission execution
→ Go mission package
→ mission tests
```

## กฎสำหรับ AI Agent

ทุก prompt architecture/refactor ควรมี:

1. อ่าน MD นี้ก่อน
2. ตรวจ `git status` / `git diff`
3. ระบุ Phase ที่กำลังทำเพียง Phase เดียว
4. ห้ามแตะ unrelated subsystem
5. เขียน/ปรับ test ก่อนหรือพร้อม code
6. รัน targeted test
7. รัน full regression ก่อนจบ
8. รายงาน behavior ที่เปลี่ยนจริง
9. ถ้า Phase เป็น refactor-only ต้องยืนยันว่า flight behavior ไม่ได้เปลี่ยน
10. ถ้าเจอ WIP ชนกัน ให้หลีกเลี่ยงไฟล์/ส่วนที่ชน ไม่ overwrite

---

# 22. Performance Improvement Strategy

อย่า optimize เพราะ "Python ไฟล์ใหญ่"

ขนาดไฟล์ไม่ใช่ต้นเหตุ performance หลัก

ให้ optimize จาก metric จริงในลำดับ:

```text
1. UI event loop stall
2. telemetry/render frequency
3. blocking RPC
4. heavy map/WebEngine calls
5. queue backlog
6. repeated serialization/JSON
7. memory growth
8. unnecessary polling
```

## กฎ

ทุก optimization ต้องมี:

```text
before metric
change
same workload
after metric
```

ถ้าวัดไม่ได้ว่าเร็วขึ้น ห้ามอ้างว่า performance ดีขึ้น

---

# 23. Crash / Freeze Safety Model

ต้องออกแบบ failure boundary ชัดเจน

## Case A — Python UI Freeze

เป้าหมายสุดท้าย:

```text
UI frozen
→ operator loses cockpit interaction temporarily
→ Core continues deterministic mission/safety state
→ FC local failsafe still independent
```

## Case B — Python UI Crash

```text
UI crash
→ Core remains alive
→ no duplicate mission start
→ reconnect UI reads current state
```

## Case C — Go Core Crash

```text
Core crash
→ FC onboard failsafe/policy remains ultimate local safety layer
→ restarted Core enters recovery-required state
→ does not silently resume old mission
```

## Case D — Duplicate / Stale Callback

```text
old run callback
→ run_id mismatch
→ ignored
```

และถ้าคำสั่งซ้ำถึง Core:

```text
command_id already handled
→ dedup policy
→ no unintended second execution
```

---

# 24. Rollout Pattern ที่ต้องใช้ซ้ำทุกครั้ง

สำหรับ subsystem สำคัญให้ใช้ pattern นี้:

```text
STEP 1  Characterize current behavior
STEP 2  Add tests
STEP 3  Add new component in shadow/pass-through mode
STEP 4  Compare old/new output
STEP 5  Switch one caller/read path
STEP 6  Run targeted tests
STEP 7  Run full regression
STEP 8  Soak/SITL if async/runtime-sensitive
STEP 9  Keep compatibility wrapper
STEP 10 Remove old path only in later clean-up phase
```

**อย่าทำ STEP 3 → STEP 10 ใน commit เดียว**

---

# 25. สิ่งที่ควรเริ่มทำเป็นงานแรกจริง ๆ

เมื่อ WIP ปัจจุบันนิ่งแล้ว งานแรกไม่ควรเป็น "แยก WaypointController"

ให้เริ่มดังนี้:

## งานที่ 1 — Architecture Inventory + Baseline Tests

ไม่มี behavior change

Deliverable:

```text
docs/APP_PY_RESPONSIBILITY_MAP.md
```

บันทึก:

- state fields กลุ่มไหนเป็นของ subsystem ใด
- methods กลุ่มไหนอ่าน/เขียน field ใด
- RPC/command entry points
- QTimer/async callback ownership
- signal connections
- Core event entry points
- high-risk shared state

## งานที่ 2 — Health Monitor / UI Watchdog observe-only

เพิ่ม metric/log เท่านั้น

## งานที่ 3 — แยก Controller ตัวแรกที่ไม่มี Flight Authority

เลือก presentation helper ที่ test ง่ายที่สุดจาก source ณ วันนั้น

ไม่กำหนดตายตัวว่าต้องเป็นไฟล์ไหนจนกว่าจะทำ responsibility map เสร็จ

นี่จะเป็นจุดเริ่มที่กระทบน้อยที่สุดจริง เพราะเราไม่เดา boundary จากชื่อ method อย่างเดียว

---

# 26. Migration Risk Ranking

| งาน | Risk | ควรทำเมื่อ |
|---|---|---|
| Baseline / inventory | ต่ำมาก | แรกสุด |
| Observe-only watchdog | ต่ำมาก | หลัง baseline |
| Presentation controller extraction | ต่ำ | หลังมี characterization tests |
| Telemetry Store shadow | ต่ำ-กลาง | หลัง observability |
| Render coalescing | กลาง | หลังวัด baseline |
| Command Gateway pass-through | กลาง | หลัง controller boundary เริ่มชัด |
| Correlation IDs | กลาง | หลัง gateway |
| Core dedup enforcement | กลาง-สูง | หลัง correlation tests |
| Mission Engine migration | สูง | หลัง gateway/dedup นิ่ง |
| Mission persistence | กลาง-สูง | หลัง engine owner ชัด |
| UI/Core recovery lifecycle | กลาง | หลัง state query/persistence |
| Failure injection | ต่ำใน SITL | หลัง recovery boundary |
| Hardware bench | สูง | หลัง SITL |
| Real flight | สูงสุด | สุดท้าย |

---

# 27. Definition of Done ของ Architecture Migration

ไม่ใช่แค่ `app.py` สั้นลง

ถือว่าสถาปัตยกรรมดีขึ้นเมื่อ:

```text
[ ] app.py เป็น composition/orchestration มากขึ้น ไม่ใช่ business brain
[ ] AI แก้ subsystem หนึ่งโดยไม่ต้องอ่าน app.py ทั้งก้อน
[ ] telemetry ingestion ไม่ผูกกับ repaint ทุก packet
[ ] UI stall มี metric และ trace
[ ] command มี central gateway
[ ] command สำคัญมี correlation/dedup policy
[ ] stale callback ถูกกันด้วย run identity
[ ] mission authority สำคัญอยู่ใน Go Core
[ ] UI restart แล้ว sync state จาก Core ได้
[ ] Core restart ไม่ auto-resume mission เก่าโดยเงียบ
[ ] safety/failsafe มี priority สูงกว่า mission
[ ] Swarm loop ไม่ทับ active failsafe
[ ] failure injection มี expected safe-state
[ ] SITL endurance ไม่มี unbounded queue/memory/timer leak
[ ] Hardware bench ผ่านก่อน real flight
```

---

# 28. Stop Conditions

ถ้า Phase ใดเกิดอย่างใดอย่างหนึ่งต่อไปนี้ ให้หยุด Phase นั้นและกลับ baseline ก่อน:

- full regression fail โดยหาสาเหตุไม่ได้
- command order เปลี่ยนโดยไม่ได้ตั้งใจ
- safety/failsafe behavior เปลี่ยนใน refactor-only Phase
- telemetry state mismatch ระหว่าง old/new path
- memory/thread/goroutine โตแบบต่อเนื่องหลังเปลี่ยน
- UI stall metric แย่ลงชัดเจน
- mission callback จาก run เก่าทำงานได้
- Core/UI reconnect ทำให้ command execute ซ้ำ
- ต้องแก้ unrelated subsystem จำนวนมากเพื่อให้ Phase เดียวผ่าน

ถ้า migration step ต้องแตะหลาย subsystem เกินคาด ให้ถือว่า boundary ออกแบบใหญ่เกินไป และแตก Phase ให้เล็กลง

---

# 29. สรุปลำดับสำหรับทีม/AI

```text
อย่าเริ่มจาก rewrite app.py

1. ทำ WIP ปัจจุบันให้นิ่ง
2. Freeze baseline + tests
3. ทำ responsibility map ของ app.py
4. เพิ่ม observability/watchdog แบบไม่สั่งอะไร
5. แยก presentation controller ตัวเล็กที่สุด
6. แยกทีละ responsibility โดยคง compatibility wrapper
7. ทำ Telemetry Store แบบ shadow ก่อน switch read
8. ทำ Command Gateway แบบ pass-through ก่อนเปลี่ยน semantics
9. เพิ่ม Run ID / Command ID แบบ correlation ก่อน enforce dedup
10. Enforce dedup ทีละ command class
11. ย้าย Mission Engine ไป Go ทีละ subsystem พร้อม shadow comparison
12. เพิ่ม persistence หลัง owner ของ mission ชัด
13. ทำ UI restartable / Core lifecycle แยก
14. Failure Injection
15. SITL Endurance
16. Hardware Bench
17. Controlled Real Flight
```

เป้าหมายไม่ใช่ "แก้ให้เสร็จเร็วที่สุด"

เป้าหมายคือ:

> **ทุกครั้งที่แก้ Architecture ระบบต้องตรวจสอบง่ายขึ้น, AI เข้าใจง่ายขึ้น, failure boundary ชัดขึ้น และสามารถย้อนกลับได้ โดยไม่แลกกับการลด safety ของระบบเดิม**
