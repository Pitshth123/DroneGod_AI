# WAYPOINT WAIT + SWARM MODE LOCK — TEST PLAN

> สถานะ: **PLAN ONLY — ยังไม่ Implement**
> วันที่: 27 สิงหาคม 2026
> ขอบเขต: เพิ่ม WAIT ให้ Waypoint, ล็อกโหมด Waypoint เมื่อ SWARM ACTIVE, และทดสอบ safety interruption ด้วย mock/SITL
> หมายเหตุ: Action A/B ที่มีอยู่เดิม **คงพฤติกรรมเดิม** เอกสารนี้ไม่เปลี่ยนกลไกของ A/B

---

## 1. เป้าหมาย

### 1.1 WAIT ราย Waypoint

เพิ่มเมนู `WAIT / รอ...` ในเมนูคลิกขวาของ Waypoint

- กรอกเวลาเป็นจำนวนเต็ม 1–10 นาที
- Waypoint route หนึ่งกำหนด WAIT ได้สูงสุด 5 จุด
- แก้เวลาของจุดเดิมได้
- ลบ WAIT ได้
- Undo/Clear แล้วจำนวน WAIT ต้องคำนวณจาก route จริง ไม่ใช้ counter แยก
- ระหว่าง Execute ห้ามแก้ WAIT
- A/B เดิมยังใช้ได้เหมือนเดิม

### 1.2 SWARM ACTIVE

เมื่อ SWARM ACTIVE:

- ใช้ Waypoint แบบ `GROUPED / STANDARD Leader Path` เท่านั้น
- `SEPARATE` = Disabled
- `WAVE` = Disabled
- เมนูคลิกขวาของ Leader Path ยังใช้ A/B เดิมและ WAIT ได้
- ออกจาก Swarm แล้ว SEPARATE/WAVE กลับมา Enabled แต่ไม่ restore สถานะเดิมอัตโนมัติ

เหตุผล: Swarm ใช้ Head/Leader เดียวเป็นเส้นทางอ้างอิง จึงไม่ควรผสมกับ per-drone route หรือ WAVE ใน operation เดียว

---

## 2. จุดอ้างอิงในโค้ดปัจจุบัน

### Waypoint model

`frontend/swarmgod_gui/core/waypoint_logic.py`

ปัจจุบัน `Waypoint` เก็บ `index / lat / lon / action`

แนะนำเพิ่ม metadata แยก:

```text
wait_seconds = 0 หรือ 60..600
```

ไม่ยัด WAIT เข้า `action` เดิม เพื่อไม่ให้กระทบ A/B ที่มีอยู่

### Right-click

เส้นทางปัจจุบัน:

```text
map.html contextmenu
→ waypoint_context
→ MapBridge
→ GroundStation._wp_action_menu()
→ _wp_action_submenu()
```

จึงไม่จำเป็นต้องเพิ่ม event type ใหม่สำหรับ WAIT

### Mode restriction

ปัจจุบันมีบางส่วนแล้ว:

- SEPARATE ถูก reject เมื่อ `_swarm_active`
- เข้า Swarm ขณะอยู่ SEPARATE จะถูกบังคับกลับ GROUPED

แต่ยังต้องเพิ่ม:

- disable ปุ่ม SEPARATE จริง
- disable WAVE จริง
- `_wave_toggle(True)` ต้อง reject เมื่อ `_swarm_active` แม้ถูกเรียกจาก code โดยตรง

---

## 3. WAIT Data Model

เพิ่ม field ให้ `Waypoint` เช่น:

```text
wait_seconds: int = 0
```

เพิ่ม route helper สำหรับ:

- set wait
- clear wait
- count wait points
- validate max duration 600s
- validate max 5 WAIT points ต่อ route

กติกา limit:

- GROUPED: 5 จุดต่อ shared route
- SWARM Leader Path: 5 จุดต่อ Leader Path
- SEPARATE (เมื่อ Swarm OFF): 5 จุดต่อ route ของแต่ละ Drone
- WAVE (เมื่อ Swarm OFF): 5 จุดใน route template ไม่คูณตามจำนวน group

---

## 4. UX ของ WAIT

Right-click Waypoint แบ่งเมนูให้เห็นชัด:

```text
Waypoint #N

ACTION
  [รายการเดิม]

WAIT
  ตั้งเวลารอ...
  WAIT ปัจจุบัน: 3 นาที
  ยกเลิก WAIT
```

Popup:

- Title: `WAIT ที่ Waypoint #N`
- Label: `รอกี่นาที? (สูงสุด 10 นาที)`
- Min 1
- Max 10
- Step 1
- Default = ค่าเดิม หรือ 1 นาที

ถ้าจะเพิ่ม WAIT จุดที่ 6:

`ตั้ง WAIT ได้สูงสุด 5 จุดต่อ Route · ลบ WAIT จุดเดิมก่อน`

การแก้เวลาจุดเดิมยังทำได้แม้ครบ 5 จุดแล้ว

---

## 5. Visualization

Marker ควรเห็นว่า waypoint ไหนมี WAIT เช่น badge สั้น:

- `W1`
- `W5`
- `W10`

ถ้าจุดเดียวกันมี action เดิมอยู่ ให้ marker แสดงทั้ง action indicator เดิมและ WAIT indicator โดยไม่เปลี่ยนความหมายของ action เดิม

Side route list แสดง เช่น:

```text
2. 14.xxxxx, 102.xxxxx  [WAIT 5m]
```

แนะนำให้ JS มี metadata update function กลาง เช่น `setWaypointMeta(...)` แล้วคง API เดิมเป็น compatibility wrapper เพื่อลด regression

---

## 6. WAIT Execution State

WAIT ต้องเป็น state ที่ cancel ได้ ไม่ใช้ `singleShot` ยาว 10 นาทีเป็น source of truth

เก็บอย่างน้อย:

```text
run_id / generation
waypoint_index
deadline_monotonic
scope_ids
```

poll ทุกประมาณ 0.5–1 วินาที:

1. run ยังเป็น run ปัจจุบันหรือไม่
2. operation ยัง execute อยู่หรือไม่
3. มี safety interrupt หรือไม่
4. deadline ถึงหรือยัง
5. ถ้ายังไม่ถึง → update countdown UI เท่านั้น
6. ถ้าถึง → complete WAIT และไปขั้นถัดไป

Timer เป็นเพียงตัวเรียกตรวจ state ห้ามเป็น business state เอง

ต้อง invalidate WAIT เมื่อ:

- Cancel Nav
- E-STOP
- WAVE Cancel
- WAVE timeout
- abort waypoint execution
- เริ่ม mission ใหม่
- Battery/Link failsafe ALARM จาก Core

callback เก่าห้ามกลับมาสั่ง waypoint ถัดไปหลัง operation ถูกยกเลิก

---

## 7. Behavior ตาม Mode

### GROUPED

```text
ทุก target ถึง waypoint
→ hold position ตาม behavior ที่ระบบใช้อยู่
→ WAIT N นาที
→ ทำ waypoint action เดิมถ้ามี
→ waypoint ถัดไป
```

### SEPARATE — Swarm OFF เท่านั้น

แต่ละ Drone รออิสระ

- D1 WAIT ไม่บล็อก D2
- D2 สามารถ advance route ของตัวเองได้

### WAVE — Swarm OFF เท่านั้น

แต่ละ group ใช้ WAIT ตาม route template

- group ปัจจุบันต้องจบ WAIT ก่อน route เดินต่อ
- group ถัดไปยังคงรอเงื่อนไข completion/landing เดิม
- Cancel/timeout ต้อง invalidate WAIT callback

### SWARM ACTIVE

ใช้ Leader Path เท่านั้น

- Head ถึง waypoint → WAIT ที่ waypoint
- followers ยังคงอยู่ภายใต้ formation control เดิม
- ไม่เปิด SEPARATE
- ไม่เปิด WAVE

---

## 8. Battery / Safety Precedence

Go Core ปัจจุบันตรวจ failsafe ทุกประมาณ 1 วินาทีใน:

`backend/internal/fleet/manager.go`

เมื่อ battery ต่ำกว่า `cfg.BattFailsafePct` ขณะ armed:

- Core publish Event ระดับ ALARM
- category = `battery`
- Core เริ่ม failsafe RTL เอง

ดังนั้น WAIT **ห้ามมี battery threshold ของตัวเอง**

### Behavior ที่ต้องได้

```text
WAIT ACTIVE
→ Core detects battery critical
→ Core starts failsafe RTL
→ Cockpit receives battery ALARM
→ invalidate WAIT/route progression
→ ไม่ advance waypoint ต่อ
→ ปล่อย Core/FC จัดการ failsafe ต่อ
```

Safety interrupt path ห้ามส่ง command ใหม่ที่อาจไปทับ failsafe จาก Core

ถ้า battery ALARM เกิดก่อน action ของ waypoint ที่ยังไม่เริ่ม ให้ยกเลิก action ที่ยังไม่เริ่มและยกเลิก mission progression แบบ fail-closed

Mission เก่าห้าม auto-resume หลัง failsafe หาย ผู้ใช้ต้องเริ่ม operation ใหม่เอง

---

## 9. Swarm + Failsafe Interlock ที่ต้องทดสอบ

จาก source ปัจจุบัน formation loop ของ Swarm อัปเดต follower เป็นระยะ ขณะที่ fleet failsafe สามารถเริ่ม RTL ของลำที่มีปัญหาได้

ก่อนถือว่าฟีเจอร์ WAIT พร้อมใช้ ต้องมี regression test ว่า formation command ไม่ไปทับลำที่ Core กำลังให้ failsafe อยู่

แนวทาง implementation ที่ควรพิจารณา:

- ให้ fleet expose สถานะ `FailsafeActive(drone_id)` หรือ abstraction ที่เทียบเท่า
- follower ที่ failsafe active → formation loop ไม่ส่ง target ใหม่ให้ลำนั้น
- Leader ที่ failsafe active → mission/formation progression ต้องหยุดแบบ fail-closed และแจ้ง ALARM

จุดนี้ต้องทดสอบแยกใน Go ไม่อาศัย UI timer

---

## 10. SWARM MODE UI LOCK

เพิ่ม helper กลาง เช่น:

```text
_wp_refresh_mode_availability()
```

เรียกเมื่อ:

- Waypoint UI สร้างเสร็จ
- `_on_swarm_update()` active state เปลี่ยน
- ออกจาก Swarm

### เมื่อ Swarm Active

1. force GROUPED
2. WAVE idle ที่เปิดอยู่ → force OFF
3. disable SEPARATE control
4. disable WAVE switch
5. GROUPED ยังคง enabled
6. mode hint แสดง:

`SWARM ACTIVE — ใช้ GROUPED Leader Path เท่านั้น · SEPARATE และ WAVE ถูกล็อก`

Logic guard ยังต้องมีแม้ UI disabled:

- `_wp_set_separate(True)` → reject ถ้า Swarm Active
- `_wave_toggle(True)` → reject ถ้า Swarm Active

### เมื่อออกจาก Swarm

- enable SEPARATE
- enable WAVE
- คง GROUPED
- คง WAVE OFF
- ไม่ restore state ก่อนหน้าอัตโนมัติ

### กรณี defensive

ถ้า Core รายงาน Swarm Active ขณะ WAVE execution ยังทำงานอยู่:

- invalidate WAVE/Waypoint progression ฝั่ง Cockpit
- ห้ามปล่อย callback เก่าเดินต่อ
- แจ้ง ALARM/Log ชัดเจน
- ไม่ส่ง command แทรกจาก handler นี้ที่อาจชนกับ Core Swarm state

ในเส้นทาง UI ปกติควร reject การเริ่ม Swarm ตั้งแต่ต้นถ้า WAVE กำลัง execute

---

## 11. PRE-FLIGHT SUMMARY V2

เพิ่ม row:

```text
WAIT
#2=3m · #5=1m
```

ลำดับแนะนำ:

```text
TARGET
HEAD
TAKEOFF
SWARM
WAYPOINT
PAYLOAD A/B
WAIT
WAVE
RETURN PLAN
GEOFENCE
```

- ก่อน execute = live plan
- เมื่อ execute = freeze WAIT ลง run snapshot
- WAIT ที่แก้ภายหลังห้ามแก้ snapshot ของ run เดิม

Timeline ใช้ generic waypoint task/action step ไม่สร้าง card ใหม่ทุก WAIT

ตัวอย่าง:

```text
WAYPOINT TASK · ACTIVE
WP 2/5 · WAIT · 02:43 remaining
```

Battery interrupt:

```text
WAYPOINT TASK · FAILED
Battery failsafe D2 → RTL
```

---

## 12. TEST-FIRST PLAN

### T1 — Pure Model

`frontend/tests/test_waypoint.py`

เพิ่ม test:

1. default wait = 0
2. wait 1 นาทีได้
3. wait 10 นาทีได้
4. >10 นาที reject
5. route มี WAIT 5 จุดได้
6. จุดที่ 6 reject
7. edit จุดเดิมตอนครบ 5 ได้
8. clear wait แล้วเพิ่มจุดใหม่ได้
9. Undo/Clear ลดจำนวนจริง
10. action เดิมกับ WAIT อยู่ร่วมกันได้
11. snapshot/copy เก็บ WAIT ครบ

### T2 — Context Menu / Render

แนะนำไฟล์ใหม่:

`frontend/tests/test_waypoint_wait.py`

1. menu มี WAIT
2. popup 1–10 นาที
3. cancel popup ไม่แก้ state
4. marker update
5. side label update
6. จุดที่ 6 ถูก block
7. edit จุดเดิมได้
8. clear wait ล้าง marker metadata
9. execute อยู่แล้วแก้ WAIT ไม่ได้

### T3 — GROUPED Runtime

1. WP ไม่มี WAIT → behavior เดิม
2. WP มี WAIT → ไม่ advance ก่อน deadline
3. deadline ถึง → advance
4. WAIT + action เดิม → WAIT ก่อน action
5. Cancel Nav ระหว่าง WAIT → no next callback
6. E-STOP ระหว่าง WAIT → no next callback
7. stale run callback แก้ mission ใหม่ไม่ได้

ห้าม test รอเวลาเป็นนาทีจริง ให้ mock/inject monotonic clock

### T4 — SEPARATE Runtime

1. D1 WAIT ไม่บล็อก D2
2. route แต่ละลำจำกัด 5 จุดของตัวเอง
3. cancel invalidates waits ทุกลำ
4. stale callback ไม่กลับมา

### T5 — WAVE Runtime

1. WAIT ใช้ได้เมื่อ Swarm OFF
2. current group WAIT → next group ยังไม่เริ่ม
3. WAIT จบ → route เดินต่อ
4. cancel/timeout → no stale callback
5. route template limit = 5 ไม่คูณ group

### T6 — SWARM MODE LOCK

ขยาย `frontend/tests/test_waypoint_separate.py` หรือเพิ่ม `test_waypoint_swarm_lock.py`

1. Swarm Active → GROUPED selected
2. SEPARATE disabled
3. WAVE disabled
4. GROUPED enabled
5. method-level SEPARATE guard ยัง reject
6. method-level WAVE guard ยัง reject
7. entering Swarm force SEPARATE → GROUPED
8. idle WAVE → OFF เมื่อเข้า Swarm
9. exit Swarm re-enable controls
10. exit Swarm ไม่ restore state เก่า
11. Leader Path ยังตั้ง action เดิมได้
12. Leader Path ตั้ง WAIT ได้
13. follower event ไม่ advance route แทน Head

### T7 — Battery Failsafe + WAIT

Frontend:

1. WAIT active + battery ALARM → WAIT cancelled
2. battery ALARM → no next waypoint
3. pending waypoint action ที่ยังไม่เริ่ม → cancelled
4. safety interrupt handler ไม่ยิง command แทรกทับ Core failsafe
5. stale wait callback ทำอะไรไม่ได้
6. mission ไม่ auto-resume
7. timeline แสดง failsafe state

Backend:

1. failsafe state query ถูกต้อง
2. follower ที่ failsafe active ไม่ถูก formation loop สั่ง target ใหม่
3. leader failsafe ไม่ปล่อย formation progression ทำงานต่อแบบเงียบ ๆ
4. clear state ไม่ทำให้ mission เก่า auto-resume

### T8 — PRE-FLIGHT SUMMARY V2

`frontend/tests/test_preflight_summary_v2.py`

1. WAIT row เรียงตำแหน่งถูก
2. render หลาย WAIT ถูก
3. snapshot freeze
4. countdown เปลี่ยน detail เท่านั้น
5. timer UI ไม่เปลี่ยน business state เอง
6. safety interrupt ปิด downstream progression

---

## 13. Implementation Order

เมื่อเริ่มเขียนจริง ให้ทำตามลำดับ:

```text
1. Pure WAIT model tests
2. Implement waypoint wait metadata/validation
3. Context menu + render tests
4. Implement planning UI + marker
5. Runtime WAIT tests
6. Implement cancellable WAIT state/run guard
7. Battery interrupt tests
8. Implement safety-yield + backend swarm/failsafe interlock ตาม test
9. Swarm mode-lock tests
10. Implement disable SEPARATE/WAVE + logic guards
11. Summary/Timeline tests + implementation
12. targeted regression
13. full frontend tests
14. full Go tests
15. SITL scenarios
16. update project docs/results
```

---

## 14. ไฟล์ที่คาดว่าจะต้องแตะตอน Implement

Frontend:

- `frontend/swarmgod_gui/core/waypoint_logic.py`
- `frontend/swarmgod_gui/app.py`
- `frontend/swarmgod_gui/assets/map.html`
- `frontend/swarmgod_gui/core/flight_progress.py` ถ้าต้องปรับ timeline detail
- `frontend/swarmgod_gui/widgets/controls.py` ถ้าต้องเพิ่ม API disable option ของ Segmented

Backend safety interlock:

- `backend/internal/fleet/manager.go`
- `backend/internal/swarm/manager.go`

Tests:

- `frontend/tests/test_waypoint.py`
- `frontend/tests/test_waypoint_ui.py`
- `frontend/tests/test_waypoint_separate.py`
- `frontend/tests/test_wave.py`
- `frontend/tests/test_preflight_summary_v2.py`
- `frontend/tests/test_waypoint_wait.py` (ใหม่)
- Go tests สำหรับ failsafe/swarm interlock

ไม่ควรต้องเพิ่ม `.proto` หรือ RPC ใหม่ใน phase นี้

---

## 15. SITL / Mock Acceptance Scenarios

### A — WAIT พื้นฐาน

- route 3 จุด
- WP2 = WAIT 1 นาที
- ถึง WP2 แล้วไม่ไป WP3 ก่อน deadline
- ครบแล้วจึง advance

### B — WAIT + action เดิม

- WP2 มี WAIT + action เดิม
- WAIT ต้องจบก่อน action
- แล้วจึง advance

### C — Cancel ระหว่าง WAIT

- เข้า WAIT
- Cancel Nav
- ข้าม deadline เดิม
- ต้องไม่มี waypoint callback กลับมา

### D — Battery failsafe ระหว่าง WAIT

- เข้า WAIT
- จำลอง battery ALARM จาก Core
- WAIT/route progression หยุด
- Core failsafe เป็นเจ้าของ state ต่อ
- ไม่มี callback จาก WAIT ไป waypoint ถัดไป

### E — SWARM + WAIT

- 3 SITL drones
- Swarm Active
- SEPARATE/WAVE disabled
- Leader Path มี WAIT
- Head หยุดตาม WAIT
- followers อยู่ภายใต้ formation เดิม
- ครบ WAIT แล้ว Leader Path เดินต่อ

### F — Swarm member failsafe

- ระหว่าง WAIT จำลอง member battery failsafe
- formation loop ต้องไม่ส่ง target ใหม่ทับลำที่ failsafe
- waypoint mission progression หยุดแบบ fail-closed

---

## 16. Definition of Done

### WAIT

- [ ] Right-click มี WAIT
- [ ] 1–10 นาที
- [ ] สูงสุด 5 จุดต่อ route
- [ ] edit/clear ได้ก่อน execute
- [ ] marker/list/summary เห็น WAIT
- [ ] cancellable state ไม่มี stale callback
- [ ] action เดิมไม่ regression

### Battery Safety

- [ ] threshold มาจาก Core config เดิม
- [ ] battery ALARM แทรก WAIT ได้
- [ ] ไม่มี next waypoint หลัง safety interrupt
- [ ] mission ไม่ auto-resume
- [ ] formation loop ไม่ทับ failsafe state

### Swarm

- [ ] GROUPED Leader Path เท่านั้น
- [ ] SEPARATE disabled ทั้ง UI + logic
- [ ] WAVE disabled ทั้ง UI + logic
- [ ] ออกจาก Swarm แล้ว controls กลับมา แต่ state ไม่ restore เอง

### Regression

- [ ] Waypoint เดิมผ่าน
- [ ] GROUPED ผ่าน
- [ ] SEPARATE ตอน Swarm OFF ผ่าน
- [ ] WAVE ตอน Swarm OFF ผ่าน
- [ ] Cancel/E-STOP tests ผ่าน
- [ ] PRE-FLIGHT SUMMARY V2 ผ่าน
- [ ] full frontend suite ผ่าน
- [ ] Go tests ผ่าน

---

## 17. Definition of Ready

พร้อมเริ่ม Implement ตามแผนเมื่อยึดกติกานี้:

- WAIT 1–10 นาที
- สูงสุด 5 WAIT points ต่อ route
- WAIT เป็น metadata แยกจาก action เดิม
- ถ้าจุดมีทั้ง WAIT และ action: WAIT ก่อน action
- Core battery/link failsafe มี priority สูงสุด
- safety interrupt invalidate callback และไม่ให้ UI progression ไปทับ Core
- Swarm Active ใช้ GROUPED Leader Path เท่านั้น
- SEPARATE/WAVE disabled และมี logic guard ซ้ำ
- tests มาก่อน implementation ในแต่ละ phase

**สรุป:** งานนี้ไม่ใช่แค่เพิ่ม popup เวลา แต่ต้องทำ WAIT เป็น state ที่ยกเลิกได้จริง และทำให้ safety/failsafe มีสิทธิ์สูงกว่า mission orchestration เสมอ รวมถึงล็อก SEPARATE/WAVE อย่างชัดเจนเมื่อเข้าสู่ Swarm Active
