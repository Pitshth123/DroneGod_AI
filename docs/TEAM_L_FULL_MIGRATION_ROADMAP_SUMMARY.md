# DroneGod_AI — Team L Full Migration Roadmap & Human-Readable Handoff

Date: 2026-09-06 (roadmap/status synchronization)

Active project: `C:\Users\PC\Desktop\v2 swam\DroneGod_AI`

Legacy reference: `C:\Users\PC\Desktop\v2 swam\DroneGod` (**READ ONLY**)

Canonical roadmap: `docs/V3_MASTER_ROADMAP.md`

---

# 0. Executive Summary — ถ้าอ่านได้แค่ 2 นาที ให้อ่านส่วนนี้

โปรเจกต์ DroneGod_AI ไม่ได้เป็นการ rewrite ใหม่ทั้งระบบในครั้งเดียว แต่เป็นการ **ค่อย ๆ ย้ายระบบจาก Python Cockpit ที่เคยถือทั้ง UI + mission logic + callback/timer จำนวนมาก ไปสู่สถาปัตยกรรมที่ Go Core ถือ authority สำคัญมากขึ้น** โดยรักษาพฤติกรรมเดิมและ fallback เดิมไว้จนกว่าจะมีหลักฐานว่าของใหม่ปลอดภัยกว่า

เป้าหมายหลักที่ทำให้เราทำงานนี้หลายวันและทดสอบซ้ำจำนวนมาก คือแก้ปัญหาใหญ่นี้:

```text
ของเดิม
Python UI ค้าง/ปิด/เด้ง
        ↓
logic mission บางส่วนอาจหยุดหรือหายไปพร้อม UI
```

เป้าหมายใหม่คือ:

```text
Python UI มีหน้าที่หลักเป็น Cockpit / Operator UI
        ↓
Go Core ถือ mission state / safety / command ownership ใน scope ที่ migrate แล้ว
        ↓
FC / ArduPilot เป็น safety layer สุดท้าย
```

แนวคิดสำคัญที่เราพยายามพิสูจน์มาตลอดคือ:

```text
UI ตาย ≠ mission authority หายทันที
UI restart ≠ Start mission ซ้ำ
request retry ≠ command ทำงานซ้ำ
stale callback ≠ command เก่ากลับมาทับคำสั่งใหม่
Core restart ≠ mission เก่า resume เอง
Emergency command ≠ ต้องรอ command ปกติที่ค้างอยู่
Telemetry burst ≠ UI queue โตจนค้าง
```

## สถานะปัจจุบันแบบภาษาคน

- Software architecture หลักที่เราต้องการสำหรับรอบนี้ **ผ่านแล้วเกือบทั้งหมด**
- Single-drone GROUPED waypoint และ WAIT มี Go Core authority ที่ผ่าน SITL/Failure tests แล้ว
- UI สามารถปิด/เปิดใหม่และ query state ได้โดยไม่ Start mission ซ้ำ
- Core restart มี persistence เพื่อบอกว่า mission ก่อนหน้าคืออะไร แต่ **ไม่ auto-resume การบิน**
- Command Gateway / request identity / dedup / emergency preemption ผ่านการทดสอบแล้ว
- Telemetry/UI lifecycle ผ่าน full regression และ stress
- Failure injection ครอบคลุม crash/reconnect/stale/duplicate/corruption/preemption หลายกรณี
- Endurance ล่าสุด: **6 ชั่วโมง continuous SITL PASS + reconnect 15 นาที PASS + client lifecycle 15 นาที PASS**
- ขั้นต่อไปที่เป็น blocker จริงคือ **V3-H02 — ต่อ Flight Controller จริงบนโต๊ะ โดยถอดใบพัด**
- Real flight (`V3-R01`) **ยัง LOCKED** จนกว่า H02 ผ่าน

## Live authority ตอนนี้

มีเพียง:

- `core-single`
- `core-single-wait`

ยัง **ไม่เปิด live authority** สำหรับ:

- multi-drone GROUPED
- SEPARATE
- SWARM_LEADER
- WAVE
- payload/servo waypoint actions

ดังนั้นอย่าเข้าใจผิดว่า “S09 ทำโค้ดไว้แล้ว” = “เปิดใช้จริงแล้ว”

---

# 1. ทำไมงานนี้ถึงใช้เวลาหลายวันและต้องเทสต์เยอะ

เราไม่ได้กำลังแก้แค่ UI bug หรือเพิ่ม feature ธรรมดา แต่กำลังเปลี่ยนว่า **ใครเป็นเจ้าของสิทธิ์สั่งโดรนในช่วงไหน** ซึ่งถ้าทำผิดจะเกิดอันตรายกว่าการ crash ธรรมดา

ตัวอย่างบั๊กที่ test ธรรมดาอาจไม่เจอ:

- operator กด STOP แล้ว command เก่าที่รอ ACK อยู่กลับมาส่งทีหลัง
- double click แล้ว Takeoff ถูก execute สองครั้ง
- command retry หลัง timeout กลายเป็น command ใหม่แทน replay ของเดิม
- UI ปิดตอน WAIT แล้วเปิดใหม่ Start mission ซ้ำ
- telemetry เก่าหลัง reconnect ทำให้ waypoint advance ผิด
- Core restart แล้ว mission เก่ากลับมา Active โดยไม่ตั้งใจ
- KILL ไปติดรอ lock ข้างหลัง LAND/GOTO ที่กำลังรอ FC ACK
- stale Python QTimer callback กลับมาส่ง GOTO หลัง operator takeover
- gRPC streaming thread ปิดไม่ครบจน Python native crash (`0xC0000005`)
- persistent DB เสีย/corrupt แล้วระบบเดา state เอง
- RAM/thread/handle leak ที่ไม่เห็นใน test 20 วินาที แต่เห็นเมื่อเปิดหลายชั่วโมง

เพราะฉะนั้น “จำนวน test เยอะ” ไม่ได้มีไว้เพื่อโชว์ตัวเลข แต่ใช้พิสูจน์ว่า **failure แต่ละชนิดจบใน safe state ที่คาดไว้**

---

# 2. ภาพรวม 3 ยุค — V1 → V2 → V3

## V1 — Low-Risk Architecture Migration

### เป้าหมาย

ลดความใหญ่และความเสี่ยงของ `app.py` แบบไม่ rewrite ทั้งระบบ

แนวทางเดิม:

```text
Freeze baseline
→ Observability
→ Extract presenters/controllers
→ Telemetry Store
→ Command Gateway
→ Run ID / Dedup
→ Mission Engine → Go
→ Persistence
→ Restartability
→ Failure Injection
→ SITL Endurance
→ Hardware
→ Flight
```

### ทำไม V1 สำคัญ

V1 สร้าง “พื้นฐานที่วัดได้” ก่อนแก้ของอันตราย ทำให้เรารู้ว่า behavior เดิมคืออะไร และทุกครั้งที่ย้ายโค้ดสามารถเทียบได้ว่าไม่ได้เผลอเปลี่ยน behavior การบิน

V1 ไม่ได้ถูกทิ้ง ทุกอย่างถูกนำมาใช้ต่อใน V3

---

## V2 — Real-Flight Safety Fast-Track

### เหตุผลที่ต้องมี V2

ระหว่างทำ V1 เราพบว่าถ้าเดินตามลำดับ architecture ปกติ จะใช้เวลานานกว่าจะไปถึงความเสี่ยงสำคัญที่สุด:

```text
Python Cockpit crash/freeze
        ↓
mission logic ที่ยังอยู่ Python หายไปพร้อม UI
```

V2 จึงเปลี่ยนลำดับเพื่อรีบแก้ safety ก่อน:

```text
Mission Boundary Audit
→ Go Mission Engine shadow
→ Run identity + Start/Cancel/Query
→ Waypoint authority → Go
→ WAIT authority → Go
→ UI restart/rebind
→ Failure Injection
→ SITL Safety Gate
→ Hardware Bench
→ Controlled Real Flight
```

### ทำไม V2 สำคัญ

V2 คือช่วงที่เราเริ่มย้ายจาก “จัดโครงสร้าง” ไปเป็น “พิสูจน์ว่า UI ตายแล้ว mission ไม่ตายตามทันที”

---

## V3 — Canonical Roadmap

หลัง V1/V2 เริ่มมีเลข Phase/F ซ้อนกันจนสื่อสารยาก จึงรวมทุกอย่างเป็น V3:

- `V3-Sxx` = Software Stage
- `V3-Hxx` = Hardware Gate
- `V3-Rxx` = Release / Real Flight Gate

V3 ไม่ได้เริ่มโปรเจกต์ใหม่ แต่คือ **การรวบรวมงาน V1/V2 ที่ทำจริงแล้ว + สิ่งที่ยังขาด ให้เป็น roadmap เดียว**

---

# 3. V1 History — สิ่งที่ทำและทำไมสำคัญ

## V1 Phase 0 — Freeze Baseline / Characterization

### สิ่งที่ทำ

- freeze behavior เดิมก่อน refactor
- inventory responsibility ของ `GroundStation`
- สร้าง characterization tests
- เก็บ baseline frontend + Go

หลักฐานช่วงต้นที่สำคัญ:

- mission/Waypoint/WAIT/failsafe/swarm targeted: **181 PASS**
- Go fleet/swarm/command/api: PASS

### สำคัญอย่างไร

ก่อนย้ายระบบต้องรู้ว่า “ของเดิมทำอะไรจริง” ไม่ใช่ทำตามความจำหรือเอกสารเก่า

Characterization test ทำหน้าที่เหมือนกล้องวงจรปิดของ behavior เดิม:

```text
input แบบนี้
→ ต้องได้ command/state sequence แบบนี้
```

ถ้าของใหม่ไม่ตรง เรารู้ทันทีว่าเปลี่ยน behavior

---

## V1 Phase 1 — Observability / HealthMonitor

### สิ่งที่ทำ

- UI heartbeat
- telemetry age
- Core connection status
- RPC/render metrics
- stall detection แบบ observe-only

Full frontend หลัง observability: **946 PASS**

### สำคัญอย่างไร

ก่อนแก้ performance หรือ lifecycle เราต้อง “มองเห็น” ก่อนว่า UI stall, telemetry ช้า, render ถี่ หรือ connection หลุดตรงไหน

Watchdog ในรอบนี้ **ไม่สั่งบินเอง** เพราะระบบตรวจสุขภาพไม่ควรกลายเป็น flight authority ใหม่

---

## V1 Phase 2A — Summary Presenter

Full frontend checkpoint: **951 PASS**

แยก presentation logic ของ summary โดยยังคง flight state/authority ไว้ที่เดิม

### สำคัญอย่างไร

ลดขนาดความรับผิดชอบของ `GroundStation` โดยไม่แตะส่วนสั่งบิน

---

## V1 Phase 2B — Fleet Presenter

Full frontend checkpoint: **961 PASS**

แยกชื่อโดรน, fleet count, selection/group presentation ออก โดยไม่ย้าย selection authority หรือ RPC

### สำคัญอย่างไร

ทำให้ UI logic อ่านง่ายขึ้น แต่ไม่เอาความสวยงามไปเสี่ยงกับ command path

---

## V1 Phase 2C — Map Presentation Adapter

Full frontend checkpoint: **965 PASS**

ย้ายเฉพาะ formatting ของ Map3D marker ออก ไม่ย้าย route/target/navigation decisions

### สำคัญอย่างไร

แสดงหลักการของ migration นี้ชัดที่สุด:

> แยกเฉพาะส่วนที่ปลอดภัยก่อน ไม่ย้ายทั้งก้อนเพราะ “ดูสะอาดกว่า”

---

## V1 Phase 3 — Telemetry Store / UI Lifecycle

ภายหลังถูก map เป็น **V3-S06**

### สิ่งที่ทำ

- immutable `TelemetryStore`
- shadow parity ระหว่าง raw telemetry กับ store
- render coalescing
- Fleet / Selected Card / Field Tablet / Map3D ใช้ presentation snapshot
- flight/business path สำคัญยังอ่าน raw telemetry ตามเดิม
- deterministic gRPC/QThread shutdown

### บั๊กใหญ่ที่เจอและแก้

Full frontend เคย native crash ประมาณ 28% ของ suite:

`0xC0000005 access violation`

Root cause คือ race ระหว่าง streaming thread `run()` / `stop()` และการปิด gRPC channel

แก้ด้วย:

- serialized stream open/stop
- idempotent cancel
- join worker ก่อนปิด channel
- test teardown ปิด GroundStation ที่ test ทิ้งไว้

### Evidence

- `test_grpc_lifecycle` **8/8 PASS**
- GroundStation create/close **50 cycles PASS**
- Map3D **53/53 PASS**
- telemetry/read-model set **132/132 PASS**
- full frontend **1031/1031 PASS** และ rerun green
- full Go PASS
- `go vet` PASS

### สำคัญอย่างไร

นี่คือหลักฐานว่า “เปิด/ปิด UI ซ้ำ, stream ซ้ำ, reconnect” ไม่ควรทิ้ง native thread/resource จนโปรแกรมพัง

---

# 4. V2 Safety Fast-Track — งานที่เปลี่ยนระบบจาก UI-owned ไป Core-owned

## V2 F0/F1 — Safety Baseline + Mission Contract

ภายหลัง map เป็น **V3-S01**

### สิ่งที่ทำ

- audit mission boundary จริง
- document ว่า Python ทำ waypoint/WAIT/cancel/arrival อย่างไร
- สร้าง Mission Core Cutover Contract
- freeze Legacy เป็น behavioral reference

### สำคัญอย่างไร

ก่อนย้าย mission authority ต้องรู้ทุก callback/timer/arrival/failsafe boundary ไม่เช่นนั้น Go version อาจ “ดูเหมือนทำงาน” แต่ไม่เหมือน Legacy

---

## V2 F2 — Go Mission Engine Shadow

ภายหลัง map เป็น **V3-S03**

### สิ่งที่ทำ

สร้าง Go mission model/state machine แบบ **shadow only** ก่อน

- MissionPlan
- states: IDLE / RUNNING / WAITING / COMPLETED / CANCELLED / INTERRUPTED / FAILED
- GROUPED / SEPARATE / SWARM_LEADER model
- WAIT
- arrival detection
- run identity

### สำคัญอย่างไร

Shadow หมายถึง Go คำนวณว่า “ถ้าเป็น authority จะทำอะไร” แต่ยัง **ไม่ส่งคำสั่งบิน**

ทำให้เปรียบเทียบ behavior ก่อนเปิด authority จริง

---

## V2 F3 — Mission RPC + Run ID

ภายหลัง map เป็น **V3-S03**

เพิ่ม:

- `StartMission`
- `CancelMission`
- `GetMissionState`
- Core-generated `run_id`
- `operation_id` idempotency

### สำคัญอย่างไร

ก่อนหน้านี้ UI มี process-local state ถ้า UI restart ข้อมูลเหล่านี้หาย

หลัง F3 เรามี “ตัวตนของ mission” ที่ Core รู้จัก และ UI กลับมาถามได้

---

## V2 F4/F5 — Core Waypoint + WAIT Authority

ภายหลัง map เป็น **V3-S04**

Live authority ที่ผ่านแล้ว:

### `core-single`

- single-drone GROUPED
- no WAIT
- no payload/WAVE/rtl_after

### `core-single-wait`

- single-drone GROUPED
- WAIT/HOLD ได้
- no payload/WAVE/rtl_after

### สิ่งที่พิสูจน์

- Go Core ส่ง GOTO ผ่าน `command.Service` + safety
- Go เป็นเจ้าของ arrival/progression
- WAIT/HOLD อยู่ใน Core
- Python หยุดส่ง mission GOTO เมื่อ Core authority active
- browser target callback ไม่ advance Core-owned mission
- lost Start reply → fail closed + Query
- UI restart/rebind → ไม่ Start ซ้ำ
- battery/link alarm → mission INTERRUPTED แต่ fleet failsafe เป็นคนส่ง flight action

### สำคัญอย่างไร

นี่คือจุดที่เป้าหมาย V2 เริ่มเป็นจริง:

```text
UI ปิด
≠
mission state หายทันที
```

---

## V2 F6/F7 — Restart / Failure Injection

### ทดสอบตัวอย่าง

- duplicate Start
- stale Cancel
- lost Start reply
- stale browser callback
- cancel near waypoint boundary
- battery/link during WAIT
- UI disconnect/reconnect during transit/WAIT
- Core kill/restart
- no auto-resume

### สำคัญอย่างไร

เราไม่ได้พิสูจน์แค่ happy path แต่พิสูจน์ว่า “ตอนพัง จะพังเป็นแบบไหน”

สำหรับระบบบิน ความสำคัญไม่ได้อยู่ที่ไม่เคย fail แต่อยู่ที่ fail แล้วต้องเข้า state ที่ควบคุมได้

---

## V2 F8 — Repeated SITL Mission Verification

Evidence สำคัญ:

- live missionverify **4/4 cycles PASS**
- duplicate Start/reconnect/WAIT/Cancel
- 500 alternating terminal Engine cycles PASS
- Core memory/thread/handle sample bounded

### สำคัญอย่างไร

หนึ่งรอบผ่านไม่ได้แปลว่า lifecycle ถูกต้อง การวนหลายรอบทำให้เห็น stale state, leaked goroutine/thread, run identity reuse และ callback เก่า

---

## V2 H1/M1 Independent Safety Audit Repair

ภายหลัง map เป็น **V3-S05**

Independent review เคยพบ:

- HIGH: manual/ad-hoc navigation อาจชน Core mission ownership
- MEDIUM: authority profile activation fail-permissive

แก้แล้วด้วย:

- block manual GOTO/RC/MODE/TAKEOFF ในช่วง Core mission ownership ตาม policy
- stale queued callbacks ถูก drop
- HOLD/STOP/LAND/RTL/DISARM/KILL takeover ทำ mission terminal ก่อน
- Mission ↔ Swarm START/RETURN mutual exclusion
- authority token ต้องตรงกับ profile ชัดเจน

Final evidence ในช่วงนั้น:

- targeted mission authority frontend **33/33 PASS**
- full Go PASS
- `go vet` PASS
- full frontend **1002/1002 PASS**

### สำคัญอย่างไร

นี่คือ “no dual authority” ซึ่งเป็น invariant สำคัญที่สุดตัวหนึ่งของระบบ

> ห้าม Python กับ Go หรือ Mission กับ Swarm ส่ง navigation ให้โดรนตัวเดียวพร้อมกัน

---

## V2 F9A — Hardware Bench Preparation

ภายหลัง map เป็น **V3-H01**

Tooling ที่เตรียมแล้ว:

- `benchprobe`
- `benchack`
- parameter audit
- HIL launcher guard
- evidence merger
- props-removed bench interlock

SITL tool validation:

- telemetry ~10 Hz
- ACK timing ระดับ ms ใน simulator
- tool ถูก guard ไม่ให้ Arm/Takeoff/navigation โดยไม่ได้ตั้งใจ

### สำคัญอย่างไร

ทำให้ตอนมี FC จริง เราไม่ต้องเขียนเครื่องมือทดสอบใหม่บน hardware สด ๆ

---

# 5. V3 Canonical Roadmap — สถานะทีละ Stage แบบภาษาคน

## V3-S01 — Baseline + Responsibility / Ownership Map ✅ DONE

### คืออะไร

สร้างแผนที่ว่า Python, Go Core, Swarm, Safety และ FC ใครรับผิดชอบอะไร

### ผ่านอะไรมา

- Legacy read-only baseline
- behavior characterization
- mission boundary contract
- ownership map
- parity reference

### สำคัญเพราะ

ทุก migration หลังจากนี้ต้องรู้ว่า “กำลังย้าย ownership จากใครไปใคร” ไม่ใช่ย้ายโค้ดเฉย ๆ

---

## V3-S02 — UI Observability + Presentation Extraction ✅ DONE

### คืออะไร

ทำให้ UI วัดตัวเองได้ และค่อย ๆ เอา presentation logic ออกจาก `app.py`

### ผ่านอะไรมา

- HealthMonitor
- heartbeat/stall metrics
- Summary/Fleet/Map presenter
- compatibility wrappers

### สำคัญเพราะ

ลดความเสี่ยงที่การ refactor UI จะเผลอไปเปลี่ยน flight logic

---

## V3-S03 — Mission Core Model + RPC Foundation ✅ DONE

### คืออะไร

สร้างสมอง mission ใน Go ก่อนเปิด authority

### ผ่านอะไรมา

- MissionPlan / state machine
- arrival observation
- WAIT model
- Core run_id
- Start/Cancel/Query RPC
- shadow comparison

### สำคัญเพราะ

ถ้าไม่มี model/state ที่ deterministic เราจะย้าย mission authority ออกจาก UI อย่างปลอดภัยไม่ได้

---

## V3-S04 — Guarded Go Mission Authority ✅ DONE (limited live scope)

### คืออะไร

เปิด Go authority จริงเฉพาะ scope ที่พิสูจน์แล้ว

### Live scope

- single-drone GROUPED
- single-drone GROUPED + WAIT

### สำคัญเพราะ

นี่คือจุดเปลี่ยนจาก “Go แค่ดู” เป็น “Go สั่งจริง” แต่จำกัด scope เพื่อไม่เพิ่ม risk พร้อมกันหลายเรื่อง

---

## V3-S05 — No-Dual-Authority + Legacy Parity Hardening ✅ DONE

### คืออะไร

ล็อกไม่ให้ command จาก manual/UI/Swarm ชนกับ Core mission

### สำคัญเพราะ

ระบบ flight control ที่มีสอง authority บนโดรนตัวเดียวกันสามารถเกิดคำสั่งขัดกันแบบ timing-dependent ซึ่งอันตรายมาก

---

## V3-S06 — Telemetry Read Model + UI/gRPC Lifecycle ✅ DONE

### คืออะไร

แยก telemetry presentation ออกจาก business state และแก้ lifecycle crash

### Evidence เด่น

- full frontend 1031/1031 PASS
- create/close 50 cycles
- gRPC stream teardown deterministic

### สำคัญเพราะ

GCS ที่บินจริงมักเปิด/ปิด/reconnect หลายครั้ง ถ้า lifecycle ไม่สะอาด ต่อให้ mission logic ดี ระบบก็ยังล้มได้

---

## V3-S07 — Command Gateway + Emergency Priority ✅ DONE

### คืออะไร

ทำให้ command mutation ผ่าน boundary กลางที่สังเกต/trace ได้ พร้อมแก้ emergency preemption

### ปัญหาที่แก้จริง

ตัวอย่าง race:

```text
LAND [D1,D2] เก่า
→ STOP ALL D2 ใหม่
→ LAND เก่าห้ามกลับมาส่ง D2 ทีหลัง
```

แก้ด้วย whole-batch intent / per-target claims / final-write guards

Emergency priority:

```text
Navigation < RTL < Land < Disarm < StopAll < Kill
```

และไม่ถือ global/mission lock ยาวข้าม FC ACK

### สำคัญเพราะ

คำสั่งฉุกเฉินต้อง “ชนะ” ไม่ใช่แค่ถูกเรียกทีหลัง

---

## V3-S08 — Command Identity + Correlation + Dedup ✅ DONE

### คืออะไร

ทำให้รู้ว่า operator action หนึ่งครั้งแตกออกเป็น command/request อะไร และ duplicate ไหนควร suppress หรือไม่ควร suppress

Identity:

```text
operation_id
→ command_id
→ attempt_id
→ request_id
→ Core audit/result
```

### แก้ bug class สำคัญ

- MODE GUIDED กับ LOITER ห้ามถูกมองว่า command เดียวกัน
- TAKEOFF 20m กับ 30m ห้าม dedup กัน
- same request_id ต้อง replay/wait ไม่ execute ใหม่หลัง 10s
- suppressed duplicate ต้องไม่ถูกแสดงว่า success
- emergency/takeover ห้าม suppress แบบ double-click dedup

### Evidence

- targeted command/correlation/preflight **85 PASS**
- full frozen-source frontend **1077/1077 PASS**
- full Go PASS
- `go vet` PASS

### สำคัญเพราะ

command identity เป็นสิ่งที่ทำให้เรา audit ได้ว่า “ใครสั่งอะไร, อันไหนเป็น retry, อันไหนเป็น action ใหม่”

---

## V3-S09 — Mission Authority Expansion ⚠ PRE-FLIP COMPLETE / AUTHORITY NOT FLIPPED

### คืออะไร

เตรียม migration ของ mission scope ที่ซับซ้อนกว่า single-drone

### A — Multi-drone GROUPED

ทำ model/parity/test สำหรับ:

- formation-offset targets
- group barrier
- per-drone altitude
- front-of-travel order
- takeover/failsafe

ยังไม่ live

### B — SEPARATE

ทำ model/test สำหรับ:

- route แยกแต่ละโดรน
- independent progression
- raw waypoint target
- route conflict geometry
- takeover/failsafe

ยังไม่ live

### C — SWARM_LEADER

ทำ model/test สำหรับ:

- mission สั่งเฉพาะ leader
- formation loop สั่ง followers
- follower ต้องไม่ถูก mission command
- emergency revoke/final-write guard

ยังไม่ live

**ยังมี policy decision:** operator takeover follower ควร terminate ทั้ง leader route หรือ exclude follower ตัวนั้น — ยังไม่อนุมัติ behavior ใด

### D — WAVE

ทำ shadow state machine สำหรับลำดับ group:

```text
takeoff
→ route
→ waiting_land
→ landed
→ next group
```

มี timeout/generation/stale callback guards

ยังไม่ live

### E — Payload / Servo A/B

ทำ action state model:

```text
HOLD
→ SET servo
→ dwell
→ RELEASE
→ advance
```

เน้น release cleanup แม้ SET result ไม่แน่นอน

ยังไม่ live

### F — rtl_after

**BLOCKED** เพราะ Legacy live route ไม่มี behavior ที่ชัดเจนให้ characterize

เราเลือก “ไม่เดา flight behavior”

### สำคัญเพราะ

S09 แสดงหลักการสำคัญของโปรเจกต์:

> โค้ดพร้อมไม่ได้แปลว่าต้องเปิด authority ทันที

ต้อง characterize → shadow → compare → gate OFF → SITL → failure → soak ก่อน flip

---

## V3-S10 — Persistence + Restartability ✅ COMPLETE

### คืออะไร

mission state อยู่รอดจาก Core restart ในฐานะ evidence/recovery state

### Rule สำคัญที่สุด

```text
Core restart
→ เจอ unfinished mission
→ RECOVERY_REQUIRED / INTERRUPTED
→ Active=false
→ Authority=false
→ ZERO flight command
→ operator เป็นคนตัดสินใจต่อ
```

### สิ่งที่ persist

- run_id / operation_id
- plan/participants
- current index
- SEPARATE indexes
- WAIT evidence
- revision / reason / timestamps
- rejections / recovery info

### Fail-safe tests

- corrupt/truncated record
- unknown schema
- stale revision
- persistence write failure
- lost Start reply
- stale Cancel จาก session เก่า

ทั้งหมดต้อง fail closed และไม่ invent LAND/RTL

### สำคัญเพราะ

Persistence ที่ “resume เอง” อาจอันตรายกว่าการไม่ persist เลย เราจึง persist เพื่อรู้ว่าเกิดอะไรขึ้น แต่ไม่คืน authority อัตโนมัติ

---

## V3-S11 — Failure Injection Expansion ✅ COMPLETE

### คืออะไร

เอาระบบทั้งหมดไปทดสอบในสภาวะผิดปกติแบบตั้งใจ

Coverage รวม:

- command delay/error
- emergency contention
- duplicate/late response
- persistence crash/corruption
- UI freeze/native teardown
- telemetry burst/stale/out-of-order
- multi-drone partial failure
- leader/follower failure
- WAVE interruption
- payload interruption
- manual takeover during dispatch

เพิ่ม deterministic gaps เช่น:

- semantic persistence corruption fail-closed
- inert telemetry ไม่ทำ DB write churn
- GROUPED progression durable across restart
- recovery clear/delete boundary
- out-of-order telemetry ไม่ทำ freshness ย้อน

### ผล

**ไม่พบ production safety bug ใหม่ใน S11** — invariants ที่สร้างมาก่อนหน้านั้นถืออยู่

### สำคัญเพราะ

เราไม่ได้ถามว่า “ระบบทำงานไหม” แต่ถามว่า:

> “เมื่อมีสิ่งผิดปกติ ระบบจะจบใน Expected Safe State หรือไม่”

---

## V3-S12 — SITL Endurance + Performance ✅ CURRENT OPERATIONAL GATE COMPLETE

### คืออะไร

เปิดระบบนานกว่าหนึ่ง mission จริงเพื่อดู leak / degradation / reconnect behavior

### Overnight evidence ล่าสุด

#### 6 ชั่วโมง idle-connected — PASS

- **21,600.172 s**
- **216,002 telemetry**
- **723 RPC**
- **0 RPC error**
- **0 stream error**
- **0 UI stall**
- no hard failure
- no investigate trend
- Python RSS +ประมาณ **1.91 MB** ตลอด 6h
- Python OS threads 16 → 13
- Core threads 11 → 12
- mean RPC latency ~**0.98 ms**
- DB size delta **0 B**

#### 15 นาที reconnect-churn — PASS

- 15 reconnects
- 9,002 telemetry
- 0 RPC/stream error

#### 15 นาที client lifecycle — PASS

- 75 create/snapshot/close cycles
- 9,003 telemetry
- 0 RPC/stream error
- Core thread stable

### Observation ที่ต้องเก็บไว้

มี `SQLITE_BUSY / database is locked` registry-upsert warning **1 ครั้ง** ใน 6h

- telemetry/RPC ไม่หยุด
-ไม่เกิด harness failure
- ไม่ repeat ใน captured stdout

จึง track เป็น non-blocking SQLite contention follow-up ไม่ reopen S12 gate

### สำคัญเพราะ

Bug บางอย่างไม่เห็นใน unit test หรือ smoke 30 วินาที เช่น memory/thread leak หรือ degradation หลัง reconnect หลายครั้ง

การรัน 6 ชั่วโมงนี้ยาวกว่ารูปแบบใช้งานจริงที่คาดไว้มากพอสำหรับ current operational gate

12h/24h และ long multi-drone soak ยังเก็บไว้เป็น deferred extended validation ไม่ใช่ blocker ตอนนี้

---

# 6. Hardware / Release Status

## V3-H01 — Hardware Bench Preparation ✅ DONE

มี tool และ safety guard พร้อมแล้ว

---

## V3-H02 — Actual FC / Airframe Bench 🟠 IN PROGRESS

Actual Flight Controller พร้อมแล้วและ H02 เริ่มทดสอบจริงแล้ว: **5.1–5.3 PASS / 5.4–5.9 PENDING** พร้อม operator-visible GUIDED/ARM/DISARM follow-up. H02 ยังไม่ exit PASS และยังไม่ปลด R01.

**ทุก bench session ต้องยืนยัน physical safety ตาม procedure ของ session นั้น**

Required evidence:

1. backup actual FC parameters/config ก่อนแก้
2. GCS/Core-loss onboard failsafe policy
3. real telemetry timing
4. real Core↔FC COMMAND_ACK timing
5. guarded Core-owned waypoint
6. Core-owned WAIT/HOLD
7. UI disconnect/reconnect
8. Cancel terminal behavior
9. controlled Core/GCS loss
10. battery/link preemption แบบ bench-safe
11. manual/STOP/E-STOP takeover
12. Core restart → no auto-resume

### ทำไม H02 สำคัญมาก

SITL พิสูจน์ software logic ได้ แต่พิสูจน์ไม่ได้ว่า **FC จริงตั้ง failsafe ถูกหรือไม่**

เราเคยฆ่า Core ใน SITL ระหว่าง GUIDED mission แล้ว simulator FC ยังเดินต่อไปที่ target/hover ซึ่งแสดงให้เห็นชัดว่า:

> software no-auto-resume ถูกต้องอย่างเดียวไม่พอ ถ้า onboard FC failsafe policy ไม่ปลอดภัย

H02 จึงเป็น blocker ที่ถูกต้องก่อนบินจริง

---

## V3-R01 — Controlled Real Flight 🔒 LOCKED

ยังไม่อนุญาตให้ถือว่า “พร้อมบินจริง”

เปิด gate นี้ได้เมื่อ:

1. H02 actual-FC bench PASS
2. software scope ที่จะใช้ในเที่ยวบินแรก green
3. ไม่มี unresolved CRITICAL/HIGH flight safety issue
4. มี explicit release review ของ exact scope

---

# 7. ตาราง Crosswalk V1 / V2 / V3 สำหรับทีม L

| งานเดิม | ตอนนี้เรียกว่า | สถานะ |
|---|---|---|
| V1 Phase 0 | V3-S01 | DONE |
| V1 Phase 1 | V3-S02 | DONE |
| V1 Phase 2A/2B/2C | V3-S02 | DONE |
| V1 Phase 3 Telemetry Store | V3-S06 | DONE |
| V1 Phase 4 Command Gateway | V3-S07 | DONE |
| V1 Phase 5 ID/Correlation/Dedup | V3-S08 | DONE |
| V1 Phase 6 Mission Engine → Go | V3-S03/S04/S09 | foundation + limited live done, expansion pre-flip |
| V1 Phase 7 Persistence | V3-S10 | COMPLETE |
| V1 Phase 8 Restartable Core/UI | V3-S10 | COMPLETE |
| V1 Phase 9 Failure Injection | V3-S11 | COMPLETE |
| V1 Phase 10 SITL Endurance | V3-S12 | CURRENT GATE COMPLETE |
| V2 F0/F1 | V3-S01 | DONE |
| V2 F2/F3 | V3-S03 | DONE |
| V2 F4/F5 | V3-S04 | DONE guarded scope |
| V2 F6 | V3-S04 + S10 | DONE for intended scope |
| V2 F7 | V3-S11 | DONE / expanded |
| V2 F8 | V3-S12 | DONE / expanded |
| V2 H1/M1 audit | V3-S05 | DONE |
| V2 F9A | V3-H01 | DONE |
| V2 F9B | V3-H02 | IN PROGRESS — 5.1–5.3 PASS / 5.4–5.9 PENDING |
| Old F10 | V3-R01 | LOCKED |

---

# 8. Test Evidence Timeline — ทำไมเรามั่นใจขึ้นกว่าหลายวันก่อน

ตัวเลขเหล่านี้ไม่ใช่คะแนน release แต่ช่วยแสดงว่าหลังแต่ละ migration เรา rerun regression จริง

```text
V1 baseline targeted                     181 PASS
Observability full frontend              946 PASS
Summary Presenter full                   951 PASS
Fleet Presenter full                     961 PASS
Map Presenter full                       965 PASS
V2 guarded mission regression            976 PASS (checkpoint)
Independent parity/audit era             993+ PASS checkpoints
Independent safety repair full          1002/1002 PASS
Telemetry/lifecycle migration full      1031/1031 PASS
Command Gateway / S07-S08 frozen full   1077/1077 PASS
S12 tooling                              18 PASS
S12 affected frontend set                48 PASS
S12 overnight                            6h + 15m + 15m PASS
```

Go side ถูก rerun หลายรอบด้วย:

- targeted package tests
- `go test ./...`
- `go vet ./...`
- diff/whitespace gates

หมายเหตุ: test count เพิ่มขึ้นเพราะมี regression/characterization test ใหม่ ไม่ควรเอาจำนวน test ต่างช่วงมาเทียบว่า code “ดีขึ้น X%”

---

# 9. Safety Invariants ที่ Team L ห้ามทำพัง

ไม่ว่าแก้อะไรต่อ ให้ถือสิ่งเหล่านี้เป็นกฎ:

1. **ห้ามสอง navigation authority คุมโดรนตัวเดียวพร้อมกัน**
2. KILL / STOP ALL / operator takeover ต้อง preempt command เก่า
3. command เก่าที่ถูก supersede ห้ามกลับมาส่ง final write ทีหลัง
4. Core mission GOTO/HOLD ต้องผ่าน `command.Service` / safety path
5. failsafe action มีเจ้าของเดียว — mission ไม่ส่ง failsafe flight action ซ้ำกับ fleet
6. UI restart ห้าม Start mission ใหม่อัตโนมัติ
7. Core restart ห้าม auto-resume mission
8. persistence corruption ต้อง fail closed
9. request retry ต้องไม่กลายเป็น duplicate execution
10. dedup ห้าม suppress emergency/takeover/non-idempotent command แบบผิด semantics
11. legacy unsupported scope ต้องอยู่ Python-owned จนกว่าสcope นั้นผ่าน migration gate จริง
12. authority token ใหม่ห้ามเปิดเพียงเพราะ test-only implementation มีอยู่
13. SWARM follower/leader takeover policy ถูก RATIFY + PRE-FLIP implement แล้ว แต่ `core-swarm-leader` ยังห้ามเปิด live จนผ่าน validation ladder
14. Return Policy / `rtl_after` contract ถูก RATIFY + PRE-FLIP implement แล้ว แต่ live validation/flip ยัง pending
15. H02 ต้องใช้ actual FC; SITL แทนไม่ได้
16. R01 ห้ามเปิดก่อน H02 PASS และ release review ของ exact scope

---

# 10. สิ่งที่ยังไม่ได้ทำ / ไม่ควรแอบอ้างว่าทำแล้ว

## ยังไม่ live

- multi-drone GROUPED Core authority
- SEPARATE Core authority
- SWARM_LEADER Core authority
- WAVE Core authority
- payload/servo waypoint Core authority

## PRE-FLIP resolved แต่ยังไม่ live

- SWARM_LEADER follower/leader takeover + succession policy: **RATIFIED / PRE-FLIP IMPLEMENTED / REVIEW PASS**, live authority validation pending
- Return Policy / `rtl_after`: **RATIFIED / PRE-FLIP IMPLEMENTED / REVIEW PASS**, live validation/flip pending
- S09 administrative baseline freeze: review PASS แล้ว แต่ยังไม่มีหลักฐานแยกใน docs ว่า freeze action เสร็จสมบูรณ์

## Deferred validation

- 12h/24h endurance
- long 5-drone/10-drone soak
- prepared-airborne mission Start/Cancel endurance campaign

สิ่งเหล่านี้ **ไม่บล็อก H02 ของ current single-drone guarded scope** แต่ห้ามอ้างว่า verified แล้ว

---

# 11. Current Next Step สำหรับ Team L

Software current operational cycle จบที่ S12 แล้ว

ลำดับต่อไป:

```text
V3-S12 software/SITL ✅
        ↓
V3-H01 tools ready ✅
        ↓
V3-H02 ACTUAL FC BENCH  🟠 IN PROGRESS
5.1–5.3 PASS → 5.4–5.9 PENDING
        ↓
ถ้าเจอ defect → แก้เฉพาะ defect + rerun affected gate
        ↓
H02 BASE PASS
        ↓
S09 live scopes ทีละ scope + full validation ladder
        ↓
Controlled-flight matrix / release review
        ↓
V3-R01 Full Production Release
```

อย่าใช้เวลาย้อนทำ S01–S12 ใหม่เพราะ requirement ถูกพูดซ้ำ

หลักการ continuation:

> ตรวจ source/evidence จริงก่อน แล้วทำเฉพาะสิ่งที่ยังไม่ผ่าน

---

# 12. Team L — Recommended Reading Order

ถ้าต้องเข้าโปรเจกต์ต่อ:

1. `docs/TEAM_L_FULL_MIGRATION_ROADMAP_SUMMARY.md` — ไฟล์นี้
2. `docs/V3_MASTER_ROADMAP.md` — canonical technical roadmap
3. `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` — latest checkpoint / evidence history
4. `docs/HARDWARE_BENCH_F9_GATE.md` — H02/F9B actual FC procedure
5. `docs/V3_S10_PERSISTENCE_RESTARTABILITY.md` — restart/persistence detail
6. `docs/V3_S11_FAILURE_INJECTION_EXPANSION.md` — failure matrix
7. `docs/V3_S12_SITL_ENDURANCE_PERFORMANCE.md` — endurance evidence
8. Legacy `DroneGod` — READ ONLY, ใช้เทียบ behavior เท่านั้น

---

# 13. Final Human Summary

สิ่งที่ทำมาตลอดหลายวันไม่ใช่ “refactor ให้โค้ดสวย” แต่เป็นการค่อย ๆ เปลี่ยนระบบจาก GCS ที่ Python UI ถือ responsibility มากเกินไป ให้เป็นระบบที่มี **ขอบเขต authority, identity, recovery และ failure behavior ที่ชัดเจนกว่าเดิม**

เราเริ่มจากล็อก behavior เดิม → เพิ่มการมองเห็น → แยก presentation → สร้าง Go Mission Engine แบบ shadow → เปิด authority ทีละ scope → ป้องกัน dual authority → ทำ command identity/dedup → persistence แบบ no-auto-resume → failure injection → endurance หลายชั่วโมง

ผลที่สำคัญที่สุดไม่ใช่ “จำนวน test” แต่คือวันนี้เราตอบคำถามสำคัญได้มากขึ้นว่า:

- ถ้า UI ตาย ใครถือ mission?
- ถ้า UI เปิดใหม่ จะ Start ซ้ำไหม?
- ถ้า command ถูก retry จะทำงานซ้ำไหม?
- ถ้า operator กด STOP/KILL command เก่าจะกลับมาทับไหม?
- ถ้า Core restart mission เก่าจะบินต่อเองไหม?
- ถ้า state ใน DB เสีย ระบบจะเดาหรือ fail closed?
- ถ้าเปิดระบบนานหลายชั่วโมง memory/thread/RPC จะเสื่อมหรือไม่?

สำหรับ software/SITL scope ที่ผ่านแล้ว คำตอบเหล่านี้มี test/evidence รองรับแล้ว

สิ่งที่ยังตอบด้วย simulator ไม่ได้คือ:

> “Flight Controller ตัวจริง เมื่อ GCS/Core หาย จะทำอะไรจริง?”

ดังนั้น H02 ไม่ใช่งาน architecture เพิ่ม แต่คือการพิสูจน์ **hardware safety boundary สุดท้ายก่อน controlled real flight**

---

# 14. Current Status Stamp — 2026-09-06

```text
V3-S01 ✅ DONE
V3-S02 ✅ DONE
V3-S03 ✅ DONE
V3-S04 ✅ DONE — guarded single-drone scope
V3-S05 ✅ DONE
V3-S06 ✅ DONE
V3-S07 ✅ DONE
V3-S08 ✅ DONE
V3-S09 🟡 PRE-FLIP COMPLETE / AUTHORITY NOT FLIPPED
V3-S10 ✅ COMPLETE
V3-S11 ✅ COMPLETE
V3-S12 ✅ CURRENT OPERATIONAL GATE COMPLETE
V3-H01 ✅ DONE
V3-H02 🟠 IN PROGRESS — 5.1–5.3 PASS / 5.4–5.9 PENDING
V3-R01 🔒 LOCKED
```

Current live Core mission authority:

```text
core-single
core-single-wait
```

Everything else remains OFF until separately approved and verified.
