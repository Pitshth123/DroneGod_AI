# PRE-FLIGHT SUMMARY V2 — Flight Plan + Live Execution Timeline

> สถานะ: Implementation plan สำหรับ Codex
> วันที่: 26 สิงหาคม 2026
> ขอบเขต: ปรับเฉพาะแนวคิด/หน้าตา/การเดิน state ของ PRE-FLIGHT SUMMARY และการ log ที่เกี่ยวข้อง
> ห้ามลด Safety Envelope, ห้ามข้าม Pre-flight gate, ห้ามเปลี่ยน Go core / `.proto` ถ้าไม่จำเป็น

## 1. เป้าหมายของคอนเซ็ปต์ใหม่

PRE-FLIGHT SUMMARY ต้องไม่เป็น Mission Log ย่อส่วนอีกต่อไป แต่ต้องตอบผู้ใช้ให้ได้ 2 คำถามเท่านั้น:

1. **ก่อนบิน ฉันตั้ง/เลือกอะไรไว้แล้วบ้าง?**
2. **หลังเริ่มทำงาน ตอนนี้ระบบกำลังทำขั้นตอนไหน และขั้นต่อไปคืออะไร?**

ดังนั้นแบ่งกล่องเดิมเป็น 2 ส่วนในกล่องเดียว:

- **A. FLIGHT PLAN / BEFORE TAKEOFF** — ค่าและแผนที่มีผลจริงต่อเที่ยวบิน
- **B. EXECUTION FLOW** — Timeline แนวตั้งของภารกิจที่กำลังทำจริง

สิ่งที่เป็น “ประวัติ” เช่น กดปุ่ม, ยกเลิก, SYSTEM TEST, CHECKLIST, ผลสำเร็จ/ล้มเหลว,
Cancel Nav, Undo/Clear Waypoint, Stop Swarm, Servo cancel ให้ไปอยู่ **Mission Log > COMMAND**
ไม่เพิ่มเป็นแถวถาวรใน PRE-FLIGHT SUMMARY
## 2. ส่วน A — FLIGHT PLAN / BEFORE TAKEOFF

ส่วนนี้เป็น **live snapshot ของค่าปัจจุบันก่อนเริ่มบิน** ไม่ใช่ event history
ให้เรียงแถวตามความสำคัญคงที่ ไม่ใช่ตามลำดับที่ผู้ใช้กด:

1. **TARGET** — ลำ/กลุ่มเป้าหมาย เช่น `D1, D2, D3` หรือ `Group 1 + Group 3`
2. **HEAD / LEADER** — เช่น `D1`
3. **TAKEOFF** — `ALL / SEQUENTIAL` + ความสูงรายลำ/ค่า default
4. **SWARM** — รูปขบวน + spacing + จำนวนลำ ถ้ามีการใช้ Swarm
5. **WAYPOINT** — `GROUPED / SEPARATE` + จำนวนจุด + เป้าหมาย
6. **PAYLOAD A/B** — เช่น `WP#2=A · WP#4=B` เฉพาะเมื่อมี action
7. **WAVE** — ลำดับกลุ่ม เช่น `G1 → G2 → G4` เฉพาะเมื่อเปิด WAVE
8. **RETURN PLAN** — RTL base altitude / gap ถ้าค่านี้มีผลกับภารกิจ
9. **GEOFENCE** — แสดงสั้น ๆ ว่า `SET · 6 points` ถ้ามี fence ใช้งานจริง

กติกา:
- key เดิมเปลี่ยนค่า = **แก้แถวเดิม** ห้ามสร้างซ้ำ
- ถ้าฟีเจอร์ไม่เกี่ยวกับแผนปัจจุบัน ให้ซ่อนแถวนั้น
- ห้ามเอา telemetry สด เช่น BAT/GPS/RSSI มาใส่เป็นแผน เพราะไม่ใช่ค่าที่ผู้ใช้ตั้ง
- ห้ามเอา SYSTEM TEST/CHECKLIST result มาเป็นแถวในส่วนนี้
- ห้ามเอา Cancel/Undo/Clear/Stop/ผล command มาเป็นแถวในส่วนนี้
### 2.1 Freeze Snapshot เมื่อเริ่มภารกิจ

ก่อนกด TAKEOFF/EXECUTE ROUTE ให้ส่วน A เป็น `PLAN · LIVE` และเปลี่ยนตามค่าที่ผู้ใช้แก้
เมื่อผู้ใช้ยืนยันเริ่มภารกิจจริง ให้ copy ค่าแผนปัจจุบันเป็น **RUN SNAPSHOT** แล้ว freeze
ค่าที่แสดงสำหรับ operation นั้น เพื่อให้ตอบได้ว่า “ตอนเริ่มจริง สั่งอะไรออกไป”

ระหว่าง mission active:
- Header เปลี่ยนเป็น `MISSION · ACTIVE`
- ค่าของ RUN SNAPSHOT ห้ามถูกแก้ย้อนหลังจากการคลิก UI ที่เกิดภายหลัง
- ถ้าผู้ใช้เปลี่ยนค่าที่จะมีผลกับคำสั่งถัดไปจริง ให้ update ผ่าน state transition ที่ชัดเจน
  ไม่ใช่แก้ snapshot แบบเงียบ ๆ
- เมื่อ mission จบ ให้คง snapshot + timeline ผลสุดท้ายไว้จนเริ่ม operation ใหม่หรือ Clear

ตัวอย่างก่อนบิน:

```text
FLIGHT PLAN                         PLAN · LIVE
TARGET          D1, D2, D3
HEAD            D1
TAKEOFF         SEQ · D1 20m · D2 20m · D3 25m
SWARM           WEDGE · SEP 6m
WAYPOINT        GROUPED · 5 จุด
PAYLOAD         #2=A · #4=B
RETURN          base 15m · gap 5m
```
## 3. ส่วน B — EXECUTION FLOW แบบ Timeline แนวตั้ง

หลังผู้ใช้กด TAKEOFF / SWARM TAKEOFF / EXECUTE ROUTE / WAVE ให้สร้าง Timeline
ตามชนิด operation จริง และเรียงจากบนลงล่างด้วยเส้นเชื่อมต่อระหว่างขั้นตอน

สถานะมาตรฐานของแต่ละ Step:

- `PENDING` — เทา, ยังไม่ถึงขั้นนี้
- `ACTIVE` — ฟ้า/เขียวสว่าง + จุด/ขอบ **กระพริบ** ทุกประมาณ 700–1000 ms
- `DONE` — เขียว + ✓
- `FAILED` — แดง + ! และหยุด progression ตามกติกาของ operation
- `CANCELLED` — เหลือง/ส้ม; เหตุผลเต็มไป Mission Log > COMMAND
- `SKIPPED` — เทาจาง เช่น ลำบินอยู่แล้วจึงไม่ต้อง Auto Takeoff

ข้อบังคับ UX:
- ปกติให้มี **ACTIVE หลักเพียง 1 step** ต่อ operation
- ด้านบน timeline แสดง `STEP 3/6 · TAKEOFF`
- ใต้ ACTIVE แสดงรายละเอียดสด เช่น `Airborne 3/5 · Head D1 ready`
- Step ถัดไปใส่ pill เล็ก `NEXT` เพื่อให้รู้ว่าจะเกิดอะไรต่อ
- เส้นเหนือ ACTIVE ที่ทำสำเร็จแล้วเป็นเขียว, เส้นด้านล่างที่ยังไม่ถึงเป็นเทา
- การกระพริบเป็นเพียง animation ของ UI; ห้ามใช้ animation/timer เป็นตัวตัดสิน business state

ตัวอย่าง:

```text
EXECUTION FLOW                     STEP 3/6
✓ 1  PLAN CONFIRMED
│
✓ 2  PRE-FLIGHT GATE
│
● 3  TAKEOFF                 ACTIVE
│    Airborne 3/5 · target 20m
│
○ 4  WAIT AIRBORNE             NEXT
│
○ 5  FORM UP
│
○ 6  SWARM ACTIVE
```
## 4. Timeline ที่ต้องสร้างตามชนิดคำสั่ง

### 4.1 TAKEOFF ปกติ — ALL

1. `PLAN CONFIRMED`
2. `PRE-FLIGHT GATE` — แสดงแค่ผ่าน/ถูกข้าม ไม่แสดงรายการ SYSTEM TEST ย่อย
3. `SEND TAKEOFF` — ส่งคำสั่งให้ลำเป้าหมาย
4. `WAIT AIRBORNE` — รายละเอียดสด `armed/airborne x/n`
5. `REACH TARGET ALT` — ใช้ telemetry altitude ของลำเป้าหมายจริง
6. `FLIGHT READY`

### 4.2 TAKEOFF — SEQUENTIAL

1. `PLAN CONFIRMED`
2. `PRE-FLIGHT GATE`
3. `SEQUENTIAL TAKEOFF` — detail เช่น `D2/5 · D1 done · D2 active`
4. `WAIT ALL AIRBORNE`
5. `REACH TARGET ALT`
6. `FLIGHT READY`

ไม่สร้าง step แยกทุก timer 2.5 วินาทีจนยาวเกินไป; ใช้ step เดียวแล้วเปลี่ยน detail ว่า
ตอนนี้กำลังส่งลำไหน และสำเร็จแล้วกี่ลำ

> หมายเหตุ: Go core มี composite Takeoff ภายใน (GUIDED/ARM/TAKEOFF) แต่ UI ไม่มี event ราย sub-step
> ที่เชื่อถือได้ทุกจุด ดังนั้น **ห้ามสร้าง timeline หลอกว่า ARM/GUIDED สำเร็จทีละขั้น**
> ถ้าจะโชว์ ให้โชว์เป็น detail จากข้อมูลที่ยืนยันได้จริงเท่านั้น
### 4.3 SWARM TAKEOFF

อิง flow ปัจจุบันใน `app.py`: Takeoff ก่อน → รอลอยตัว → Form up

1. `PLAN CONFIRMED`
2. `PRE-FLIGHT GATE`
3. `TAKEOFF` — ALL/SEQ ตามค่าจริง
4. `WAIT AIRBORNE` — ต้องบอก `Head D# ready/not ready` และ `x/n airborne`
5. `FORM UP` — เริ่มเมื่อเงื่อนไข `_await_airborne_then_formup()` ผ่านจริง
6. `SWARM ACTIVE` — หลัง core ตอบรับ start/form-up

ถ้า Head ไม่ลอยภายใน timeout ตาม logic ปัจจุบัน:
- step `WAIT AIRBORNE` → `FAILED`
- `FORM UP` และ `SWARM ACTIVE` → `SKIPPED`
- log เหตุผลเต็มลง COMMAND/ALERT ตามระบบเดิม

### 4.4 WAYPOINT / EXECUTE ROUTE

1. `VALIDATE ROUTE` — route/conflict/targets พร้อม
2. `CONFIRM MISSION` — รวมการยืนยัน payload A/B ถ้ามี
3. `AUTO TAKEOFF` — มีเฉพาะลำที่ยังอยู่พื้น; ถ้าทุกลำบินอยู่ให้ `SKIPPED`
4. `WAIT AIRBORNE`
5. `FLY WAYPOINT` — detail `WP 2/5 · D1,D2,D3`
6. `WAYPOINT ACTION` — แสดงเฉพาะเมื่อ WP ปัจจุบันมี A/B เช่น `HOLD → SERVO A → wait 2s`
7. `ROUTE COMPLETE`

สำหรับหลาย waypoint ไม่ต้องสร้าง 20 การ์ด; ใช้ step `FLY WAYPOINT` เดียวแล้วเปลี่ยน
index/detail สดตาม `target_reached` และ state ของ route จริง
### 4.5 WAVE

WAVE ต้องทำให้ผู้ใช้เห็นชัดว่ากลุ่มไหนกำลังทำ และกลุ่มไหนรอต่อคิว

Flow หลัก:

1. `VALIDATE WAVE` — groups/route/targets พร้อม
2. `GROUP G1 · PREPARE`
3. `GROUP G1 · TAKEOFF` — เฉพาะถ้ายังอยู่พื้น
4. `GROUP G1 · ROUTE`
5. `GROUP G1 · RETURN + LAND`
6. `WAIT G1 DISARMED`
7. `GROUP G2 · PREPARE`
8. ทำซ้ำจนหมดกลุ่ม
9. `WAVE COMPLETE`

เพื่อไม่ให้ยาวเกินไปเมื่อมีหลายกลุ่ม ให้ renderer รองรับ **group block แบบย่อ**:

```text
✓ GROUP 1                 COMPLETE
│  takeoff → route → return → land
│
● GROUP 2                   ACTIVE
│  ROUTE · WP 3/5
│
○ GROUP 3                     NEXT
```

เมื่อเข้า group block ที่ ACTIVE ให้ detail เปลี่ยนเป็น sub-state `TAKEOFF / ROUTE / RETURN / LAND`
แทนการสร้างทุก sub-step เป็นการ์ดใหญ่ทั้งหมด

ถ้า WAVE ถูก Cancel/E-STOP/Cancel Nav:
- current step/block → `CANCELLED`
- step ที่เหลือ → `SKIPPED`
- ตัว action “ยกเลิก WAVE” และเหตุผลต้อง log ลง **Mission Log > COMMAND** เช่นเดิม
## 5. COMMAND Log — สิ่งที่ต้องย้ายออกจาก Summary

Codex ต้องตรวจทุกจุดที่เรียก `_summ_event()` / `_summ_set()` แล้วแยกให้ถูกประเภท

ให้เข้า Mission Log category `COMMAND`:
- กด/ยกเลิก TAKEOFF
- Cancel Nav
- Undo / Clear Waypoint
- เปิด/ปิด/ยกเลิก WAVE
- Start / Stop Swarm
- Servo A/B on/off/cancel
- SYSTEM TEST เริ่ม/ผ่าน/ไม่ผ่าน
- CHECKLIST เปิด/บันทึก/ครบหรือไม่ครบ
- Pre-flight ถูก SKIP
- command result สำเร็จ/ล้มเหลว

PRE-FLIGHT badge ด้านบนยังทำงานเหมือนเดิมได้ แต่ **รายละเอียดการเทสไม่ต้องยัดใน Summary**

ข้อสำคัญ: อย่า log ซ้ำสองบรรทัดจากทั้ง timeline และ command handler
Timeline คือ current state visualization; Mission Log คือ audit/history

## 6. State ต้องมาจากของจริง ไม่ใช้เวลาเดา

Timeline ต้องเปลี่ยน step จาก transition ที่ระบบรู้จริง เช่น:
- command ถูกยืนยันและส่งจริง
- RPC response จริง
- `_last_telem` / `_last_alt` / armed / mode จริง
- `target_reached` จริง
- `_waypoint_executing`, `_wave_executing`, `_rtl_active` และ swarm state จริง
- เงื่อนไข `_await_airborne_then_formup()` ผ่านจริง
- กลุ่ม WAVE disarmed ครบจริง

QTimer ใช้ได้เพื่อ polling/animation แต่ **ห้าม timer หมดแล้วถือว่า step สำเร็จเอง**
## 7. โครงสร้างโค้ดที่แนะนำ

ยึดหลักเดิมของโปรเจกต์: **UI เป็นกระจก ไม่ใช่สมอง**

### 7.1 เพิ่ม pure logic ใหม่

ไฟล์ใหม่แนะนำ: `frontend/swarmgod_gui/core/flight_progress.py`

มีโครงสร้างประมาณนี้:

- `StepStatus`: `PENDING / ACTIVE / DONE / FAILED / CANCELLED / SKIPPED`
- `FlightStep`: `id, title, detail, status, group_id(optional)`
- `FlightRun`: `run_id, kind, started_at, plan_snapshot, steps, active_step_id`
- builder: `build_takeoff_flow(...)`
- builder: `build_swarm_takeoff_flow(...)`
- builder: `build_waypoint_flow(...)`
- builder: `build_wave_flow(...)`
- methods: `activate()`, `complete()`, `fail()`, `cancel()`, `skip()`, `set_detail()`

logic file ห้าม import Qt/gRPC เพื่อให้ test ได้ headless

### 7.2 Widget

แก้ `frontend/swarmgod_gui/widgets/command_summary.py`
โดยคงชื่อ `CommandSummaryBox` ไว้ก่อนเพื่อลด regression แต่เปลี่ยนภายในเป็น 2 section:

- `FLIGHT PLAN` — render rows แบบปัจจุบัน
- `EXECUTION FLOW` — render timeline vertical ใหม่

เพิ่ม method เช่น `render_plan(rows)`, `render_timeline(run_snapshot)` และ animation เฉพาะ ACTIVE
### 7.3 App integration

แก้ `frontend/swarmgod_gui/app.py` แบบ adapter เท่านั้น:

- `_summ_set/_summ_remove` ยังใช้กับ FLIGHT PLAN ได้
- ก่อน operation จริง: `_flight_run_start(kind, snapshot)`
- ตอน transition: `_flight_step_active(id, detail)` / `_flight_step_done(id)`
- ตอน error: `_flight_step_failed(id, reason)`
- ตอน cancel: `_flight_run_cancel(reason)`
- ทุก transition เรียก `_render_summary()` หนึ่งครั้งหลัง batch update

อย่า parse ข้อความจาก `cmd_result` เพื่อเดาสถานะ ถ้าจุดไหนรู้ transition อยู่แล้ว
ให้เรียก progress API จากจุดนั้นโดยตรง

### 7.4 กัน callback เก่ามาทับ mission ใหม่

ทุก FlightRun ต้องมี `run_id` หรือ generation token
callback จาก thread/QTimer ต้องเช็ค run_id ก่อนแก้ timeline

ตัวอย่างปัญหาที่ต้องกัน:
- mission A ถูก Cancel แล้ว mission B เริ่ม
- timer/worker ของ A ตอบกลับช้า
- ห้าม response ของ A ไปทำ step ของ B เป็น DONE/FAILED

ใช้แนวเดียวกับ `_wp_takeoff_generation` ที่มีอยู่แล้ว แต่ทำเป็น abstraction กลางของ timeline

## 8. Layout ในพื้นที่จริง

ตำแหน่งกล่องเดิมข้าง Mission Log ให้คงไว้ ไม่เพิ่มหน้าต่างใหม่
ใช้ vertical layout และ scroll เดียวหรือแยก 2 section ตามพื้นที่จริง
ตัวอย่าง layout เป้าหมาย:

```text
◇ PRE-FLIGHT SUMMARY                 MISSION · ACTIVE

FLIGHT PLAN
TARGET       D1, D2, D3
TAKEOFF      SEQ · 20m / 20m / 25m
SWARM        WEDGE · SEP 6m
WAYPOINT     GROUPED · 5 จุด

EXECUTION FLOW                      STEP 3/6
✓ PLAN CONFIRMED
│
✓ PRE-FLIGHT GATE
│
● TAKEOFF                         ACTIVE
│  D2/3 · airborne 1/3
│
○ WAIT AIRBORNE                     NEXT
│
○ FORM UP
│
○ SWARM ACTIVE
```

Visual:
- Header/section ใช้ typography เดิมของ Cockpit
- `ACTIVE` ใช้ accent ฟ้า/เขียว และ pulse เฉพาะ dot/outline เพื่อไม่รบกวนสายตา
- `DONE` เขียว, `FAILED` แดง, `CANCELLED` amber, `PENDING/SKIPPED` เทา
- ห้ามทำทั้งกล่องกระพริบ
- ต้องอ่านได้แม้ animation ถูกปิด; มีคำว่า `ACTIVE/NEXT/DONE` กำกับเสมอ
## 9. Test plan ที่ Codex ต้องเพิ่ม

แนะนำไฟล์ใหม่ `frontend/tests/test_preflight_summary_v2.py`

ขั้นต่ำต้องมี:

1. plan row key เดิมเปลี่ยนค่าแล้วไม่ซ้ำ
2. Cancel/System Test/Checklist event ไม่เข้า Flight Plan rows
3. SYSTEM TEST/CHECKLIST ถูก log เป็น category `COMMAND`
4. TAKEOFF ALL สร้าง step order ถูกต้อง
5. TAKEOFF SEQ แสดง progress ลำปัจจุบัน/ทั้งหมดได้
6. SWARM flow ต้อง TAKEOFF → WAIT AIRBORNE → FORM UP ตามลำดับ ห้าม Form up ก่อน
7. Head ไม่ airborne แล้ว timeout ต้อง FAILED + downstream SKIPPED
8. WAYPOINT ที่ลำอยู่พื้นต้องมี AUTO TAKEOFF; ถ้าบินอยู่แล้ว step นี้ SKIPPED
9. target_reached ทำให้ `FLY WAYPOINT` เปลี่ยน index/detail จริง
10. WAVE ต้องไม่เลื่อนไป Group 2 ก่อน Group 1 disarmed ครบ
11. Cancel Nav/WAVE/E-STOP ต้องหยุด run ปัจจุบันและไม่ปล่อย step เก่ากลับมา ACTIVE
12. stale callback ที่ run_id เก่า ต้องไม่แก้ run ใหม่
13. มี ACTIVE หลักได้ไม่เกิน 1 step ต่อ run
14. animation timer เปลี่ยนแค่ visual state ไม่เปลี่ยน business status
15. construct `CommandSummaryBox` headless แล้วไม่ crash และ render timeline ได้

ต้องรัน regression เดิมอย่างน้อย:
- `tests/test_preflight.py`
- `tests/test_wave.py`
- `tests/test_waypoint*.py`
- `tests/test_swarm_logic.py`
- `tests/test_ui_polish.py`
- `tests/test_ui_selection.py`
## 10. ลำดับการ Implement สำหรับ Codex

### Phase 1 — แยก Plan ออกจาก History
1. ตรวจ `_summ_set`, `_summ_remove`, `_summ_event` ทั้งหมด
2. จำกัด `_summ_set` ให้เหลือเฉพาะค่าที่มีผลกับแผนจริงตาม §2
3. ย้าย cancellation / test / result ไป Mission Log > COMMAND
4. ทำ order ของ Flight Plan ให้คงที่ตาม spec ไม่ขึ้นกับลำดับกด

### Phase 2 — Pure progress model
5. เพิ่ม `core/flight_progress.py`
6. เขียน unit tests state transition ให้ครบก่อนต่อ UI
7. ใส่ `run_id` ป้องกัน async callback เก่า

### Phase 3 — Timeline UI
8. แบ่ง `CommandSummaryBox` เป็น FLIGHT PLAN + EXECUTION FLOW
9. ทำ vertical connector + status dot + ACTIVE pulse
10. รองรับ detail update โดยไม่ rebuild widget ทั้งกล่องถ้าไม่จำเป็น

### Phase 4 — ผูก operation จริง
11. ผูก TAKEOFF ALL/SEQ
12. ผูก SWARM TAKEOFF / WAIT AIRBORNE / FORM UP
13. ผูก WAYPOINT + payload action
14. ผูก WAVE group progression
15. ผูก Cancel / failure / E-STOP กับ termination ของ current run

### Phase 5 — Regression + docs
16. รัน targeted tests แล้ว full frontend suite
17. อัปเดต `docs/USAGE.md`, `docs/SWARM_CONTROL.md`, `docs/PROGRESS.md`
18. บันทึกไฟล์/เหตุผล/ผล test ใน `docs/CODEX_CHANGES.md`
## 11. Definition of Done / Acceptance Criteria

ถือว่าเสร็จเมื่อทุกข้อนี้ผ่าน:

- [ ] ก่อนบิน Summary เห็นเฉพาะ **สิ่งที่ตั้ง/เลือกและจะมีผลจริง**
- [ ] ไม่มี cancellation, SYSTEM TEST, CHECKLIST หรือ command history ปนใน Flight Plan
- [ ] เหตุการณ์เหล่านั้นค้นย้อนหลังได้ที่ Mission Log > COMMAND
- [ ] กด TAKEOFF แล้วเห็น timeline ตามลำดับจริงทันที
- [ ] ACTIVE กระพริบชัด และบอก detail ว่ากำลังทำอะไร
- [ ] ผู้ใช้มองแล้วรู้ `ตอนนี้` และ `NEXT` โดยไม่ต้องเปิด log
- [ ] Step DONE/FAILED/CANCELLED มาจาก state/event/telemetry จริง ไม่ใช่เวลาเดา
- [ ] SWARM ไม่แสดง FORM UP ก่อนโดรน/Head พร้อมจริง
- [ ] WAYPOINT แสดง WP ปัจจุบัน/ทั้งหมด และ action A/B เมื่อถึงจุดจริง
- [ ] WAVE แสดงกลุ่มปัจจุบันและไม่ข้ามกลุ่มก่อนเงื่อนไข disarm ครบ
- [ ] เปลี่ยนแผนหลังเริ่มแล้วไม่แก้ RUN SNAPSHOT ย้อนหลัง
- [ ] callback เก่าจาก mission ก่อนหน้าไม่สามารถแก้ timeline mission ใหม่
- [ ] E-STOP/Cancel ทำให้ timeline หยุดและไม่มี step ถัดไปวิ่งต่อ
- [ ] UI ไม่กระตุก/ไม่ rebuild WebEngine/ส่วนอื่นโดยไม่จำเป็น
- [ ] targeted tests + full frontend tests ผ่าน

## 12. สิ่งที่ห้ามทำ

- ห้ามเปลี่ยน PRE-FLIGHT SUMMARY เป็น log อีกครั้ง
- ห้ามแสดง step ที่ระบบพิสูจน์สถานะจริงไม่ได้
- ห้ามใช้ QTimer อย่างเดียวตัดสินว่าโดรนขึ้นถึง/ลงถึง/ถึง waypoint แล้ว
- ห้ามลด/ข้าม `_preflight_gate()` หรือ Safety Envelope
- ห้ามยิง gRPC ซ้ำเพียงเพื่อทำให้ timeline ดูมีสถานะ
- ห้ามย้าย orchestration ความปลอดภัยจาก core มาไว้ใน widget
- ห้ามแก้ Go core / proto เพื่อ UI อย่างเดียว หาก state เดิมใน cockpit เพียงพอ

---

**สรุปสำหรับ Codex:** PRE-FLIGHT SUMMARY V2 = `WHAT WILL HAPPEN` + `WHAT IS HAPPENING NOW`.
History ทั้งหมดอยู่ COMMAND Log; Summary ต้องเป็นแผนที่อ่านก่อนบิน + live execution timeline ที่เชื่อถือได้จริง.
