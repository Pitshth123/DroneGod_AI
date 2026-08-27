# บันทึกการแก้ไขโดย Codex

เอกสารนี้ระบุการเปลี่ยนแปลงที่ **Codex ดำเนินการในโปรเจกต์ SwarmGod** เพื่อให้ตรวจสอบย้อนหลังได้ว่าแก้ส่วนใดและอยู่ในไฟล์ไหน

## 27 สิงหาคม 2026 — WAIT ราย Waypoint + SWARM MODE LOCK + Failsafe interlock

Implement ตาม `docs/WAYPOINT_WAIT_SWARM_MODE_TEST_PLAN.md` แบบ test-first ทุก phase

### สิ่งที่ implement จริง

- **WAIT ราย Waypoint** — เพิ่มเวลารอ (HOLD) ต่อจุด 1–10 นาที, สูงสุด 5 จุดต่อ route,
  แก้/ลบได้, ห้ามแก้ระหว่าง EXECUTE. เป็น metadata แยกจาก action A/B เดิม
  ลำดับที่จุดหนึ่ง: `ARRIVE → HOLD/WAIT → action A/B → waypoint ถัดไป`
- **WAIT เป็น cancellable state** — poll ทุก 500ms พร้อม generation/run-id guard
  (ไม่ใช้ `singleShot` ยาว, ไม่ใช้ `sleep` บน UI thread). Timer เป็นแค่ตัวเรียกตรวจ
  ไม่ใช่ business authority. ทำงานครบทั้ง GROUPED / SEPARATE (per-drone) / WAVE (per-group)
- **SWARM MODE LOCK** — เมื่อ `_swarm_active`: บังคับ GROUPED, ปิด WAVE, disable
  SEPARATE/WAVE controls + logic guard ซ้ำ (`_wp_set_separate(True)` และ
  `_wave_toggle(True)` reject แม้เรียกจาก code), ออก Swarm แล้ว enable กลับแต่ไม่ restore state
- **Battery/Link failsafe มี priority สูงกว่า WAIT** — ใช้ failsafe เดิมของ Core เป็น
  source of truth (ไม่มี threshold ใหม่ใน frontend). เมื่อ ALARM: invalidate WAIT/route
  progression, ไม่ advance ต่อ, ไม่ยิงคำสั่งทับ Core, mission ไม่ auto-resume
- **Swarm formation interlock (Core)** — follower ที่ Core กำลัง failsafe RTL จะไม่ถูก
  formation loop ส่ง Goto/GotoYaw ทับ; leader failsafe → halt formation แบบ fail-closed
  (latch ไม่ auto-resume จนกว่าจะ Start ใหม่) + ALARM
- **Pre-flight Summary V2** — เพิ่มแถว `WAIT` (`#2=3m · #5=1m`) วางถัดจาก PAYLOAD A/B
  ก่อน WAVE, freeze ลง run snapshot; timeline แสดง `WAITING · WP N · MM:SS remaining`

### ไฟล์ที่แก้ (frontend)

- `frontend/swarmgod_gui/core/waypoint_logic.py` — `Waypoint.wait_seconds`, `WaitLimitError`,
  validators, route helpers (`set_wait`/`set_wait_minutes`/`clear_wait`/`wait_count`/`wait_summary`)
- `frontend/swarmgod_gui/app.py` — context-menu WAIT + popup, marker/label render,
  cancellable WAIT execution (`_wp_on_arrived`/`_wp_wait_*`), `_wp_refresh_mode_availability`,
  `_wp_swarm_defensive_wave_stop`, `_wp_failsafe_interrupt`/`_wp_participant`, guards ใน
  `_wave_toggle`/`_swarm_start`, WAIT row ใน summary (`wp_wait` → `wait`)
- `frontend/swarmgod_gui/assets/map.html` — WAIT badge + `setWaypointMeta`/`setWaypointWait`
  (คง `setWaypointAction`/`addWaypoint` เป็น compatibility wrapper)
- `frontend/swarmgod_gui/core/swarm_logic.py` — priority `wait=5.5` ใน CommandSummary

### ไฟล์ที่แก้ (backend/Core)

- `backend/internal/fleet/manager.go` — เพิ่ม `FailsafeActive(id)` (battery หรือ link lost)
- `backend/internal/swarm/manager.go` — `planFormationTargets` (ข้าม follower failsafe,
  คง slot), leader-failsafe `haltFormation` fail-closed latch, reset ใน Start/Stop

### tests ที่เพิ่ม

- `test_waypoint.py` (+20 model), `test_waypoint_wait.py` (menu/popup/render/summary),
  `test_waypoint_wait_runtime.py` (GROUPED/SEPARATE runtime + cancel/stale guard),
  `test_waypoint_swarm_lock.py`, `test_waypoint_failsafe.py`, `test_wave.py::TestWaveWait`,
  `test_preflight_summary_v2.py::TestWaitPlanRow`
- Go: `internal/fleet/failsafe_active_test.go`, `internal/swarm/failsafe_interlock_test.go`

### safety decisions

- ไม่สร้าง battery/link threshold ใหม่ใน frontend — ใช้ failsafe RTL เดิมของ Core
- safety interrupt **ไม่ยิง command** (HOLD/GOTO/RTL/stop) ที่อาจชนกับ failsafe ของ Core
- generation/run-id guard ป้องกัน callback WAIT เก่ากลับมาสั่ง waypoint ถัดไปหลัง
  cancel/RTL/E-STOP/timeout/mission ใหม่/failsafe
- leader-failsafe halt เป็น latch — mission ไม่ auto-resume แม้ failsafe หาย
- SEPARATE/WAVE lock มี logic guard ชั้นสองนอกเหนือจาก disabled widget

### test results จริง

- **Frontend full suite: 930 passed** (`python -m pytest -q`, offscreen)
- **Backend: `go test ./...` ผ่านทุก package** (fleet, swarm, api, ...)

### ที่ยังต้อง SITL/real-flight verification

- Scenario A–F ใน TEST_PLAN §15 (WAIT พื้นฐาน, WAIT+action, cancel/battery ระหว่าง WAIT,
  SWARM+WAIT, member failsafe interlock) — mock/headless ผ่านแล้ว ยังต้องยืนยันบน SITL/จริง
- พฤติกรรม HOLD ระหว่าง WAIT บน FC จริง (GUIDED hover) และ timing ของ failsafe RTL
  ทับ formation บนโดรนหลายลำ

## 26 สิงหาคม 2026 — PRE-FLIGHT SUMMARY V2

Codex เพิ่ม execution model และปรับ Summary ให้ตอบ “ตั้งอะไรไว้” และ “กำลังทำอะไร”
แยกจาก Mission Log อย่างชัดเจน:

- เพิ่ม `frontend/swarmgod_gui/core/flight_progress.py` เป็น pure state model ของ
  Takeoff, Swarm Takeoff, Waypoint และ WAVE พร้อม `run_id` ป้องกัน callback เก่า
- ปรับ `widgets/command_summary.py` เป็น FLIGHT PLAN + EXECUTION FLOW มีสถานะ
  PENDING/ACTIVE/DONE/FAILED/CANCELLED/SKIPPED และ pulse เฉพาะภาพของ ACTIVE
- ปรับ `app.py` ให้ freeze plan snapshot ก่อน operation, อัปเดต timeline จาก RPC /
  telemetry / target reached / WAVE disarm และยกเลิก run เมื่อ Cancel Nav หรือ E-STOP
- จำกัด Flight Plan ให้เหลือเฉพาะค่าที่มีผลจริง; event และผลคำสั่งอยู่ Mission Log > COMMAND
- เพิ่ม `frontend/tests/test_preflight_summary_v2.py` ครอบคลุม state transition,
  snapshot, cancel, WAVE และ widget แบบ headless
- ตรวจสุดท้ายด้วย `python -m unittest discover -s tests -p "test_*.py" -q`:
  **830 tests ผ่าน** (26 สิงหาคม 2026)

## 25 สิงหาคม 2026 — Pre-flight Summary, AUTO TAKEOFF ก่อน Route และชิปกลุ่ม

Codex แก้ `frontend/swarmgod_gui/app.py` และเพิ่ม regression tests ใน `frontend/tests/test_wave.py` / `frontend/tests/test_groups.py`:

- ขยาย **PRE-FLIGHT SUMMARY** ให้แสดง Waypoint Mode/Route, จุด A/B, WAVE และลำดับกลุ่ม, การเลือกกลุ่ม, TAKEOFF plan, EXECUTE ROUTE, คำสั่งทั่วไป และรายการยกเลิกล่าสุด
- เมื่อกด **EXECUTE ROUTE** ระบบตรวจทุกลำเป้าหมาย ถ้ายังไม่บินจะแสดงรายชื่อ/ความสูงและถามยืนยัน TAKEOFF
- หลังยืนยัน ระบบรอ telemetry ยืนยันว่า Armed และสูงพ้นพื้นอย่างน้อย 1.5 เมตรก่อนส่ง GOTO; timeout 90 วินาทีแล้วหยุดเส้นทาง
- WAVE ตรวจเงื่อนไข TAKEOFF ใหม่ก่อนเริ่มทุกกลุ่ม กลุ่มที่บินครบแล้วไม่ถามซ้ำ ส่วนกลุ่มถัดไปที่ยังอยู่พื้นจะถามและขึ้นบินเฉพาะกลุ่มนั้น
- ยกเลิกระหว่างรอ TAKEOFF หรือระหว่าง WAVE จะตัด timer/GOTO ที่ค้างอยู่ ไม่ให้คำสั่งหลุดตามมาภายหลัง
- แก้ชิปกลุ่มที่ข้อความถูก padding ตัดหาย โดยเพิ่มความกว้างเป็น 40px, เอา padding ออก และแสดง `1·2` = กลุ่ม 1 มี 2 ลำ
- อัปเดต `docs/USAGE.md`, `docs/GROUP_WAVE.md` และคู่มือในแอปให้ตรงกับพฤติกรรมใหม่

## 25 สิงหาคม 2026 — Tooltip และการ์ดโดรนที่เลือก

Codex แก้ `frontend/swarmgod_gui/assets/map.html`, `frontend/swarmgod_gui/widgets/fleet_item.py`, `frontend/swarmgod_gui/app.py` และเพิ่ม regression tests ใน `frontend/tests/test_rtl_and_mapui.py` / `frontend/tests/test_ui_selection.py`:

- Tooltip บนแผนที่รับเมาส์ไม่ได้แล้ว และจะปิดทันทีเมื่อเมาส์ออกจากไอคอนหรือออกจากพื้นที่แผนที่; GCS / Home ใช้พฤติกรรมเดียวกัน
- กรอบรอบการ์ดรายละเอียดโดรนด้านล่างเปลี่ยนเป็นสีประจำลำที่เลือกทันที แม้ลำนั้นยังไม่มี telemetry
- ช่อง **MODE** เป็นการ์ดย่อยมีกรอบเด่น และเปลี่ยนสีตามโหมดบินที่รายงาน (GUIDED, LOITER, RTL, LAND)
- ซ่อนแถว Waypoint/WAVE จาก PRE-FLIGHT SUMMARY เมื่อปิด Waypoint Mode; จะกลับมาแสดงเมื่อเปิดโหมด หรือเมื่อกำลัง EXECUTE จริง
- เพิ่มความเด่นของกรอบ **MODE** ในการ์ดเล็กด้านบนของส่วน Leader Mode เป็นกรอบ 2px สีตามโหมด
- เปลี่ยนแถบกลุ่มใน Fleet เป็น `GROUP` / `ALL` และใช้คำอธิบายภาษาอังกฤษ
- ย้ายตัวเลือก TCP/UDP กับช่อง IP ออกจาก Fleet ไปอยู่ใน popup `CONNECT DRONE`; หน้า Fleet เหลือปุ่ม `SCAN` และ `CONNECT` เท่านั้น
- Map 3D รองรับการคลิกพื้นเพื่อวาง Waypoint เมื่อเปิด Waypoint Mode และปิด GOTO จาก double-click ชั่วคราวในโหมดนี้ เพื่อไม่ให้คำสั่งชนกัน
- ปุ่มเลือกโดรนใน Mission Log เป็นแคปซูลขนาดเล็ก กว้างตามชื่อ `Drone N` และมี horizontal scrollbar เฉพาะแถวตัวกรองเมื่อรายการเกินพื้นที่ จึงอ่านชื่อ Drone 10/11 ได้ครบ; แก้การคำนวณขนาดให้เกิดหลังใช้ฟอนต์จริง และกำหนดขนาด content หลัง rebuild เพื่อไม่ให้ปุ่มโดรนหายเมื่อรายการ telemetry อัปเดต
- การเลือก `Drone 1` ใน Mission Log กรองแบบชื่อเต็ม จึงไม่ดึง log ของ `Drone 10` หรือ `Drone 11` ปะปนมา

## 25 สิงหาคม 2026 — ระบบกลุ่มโดรนและ WAVE ตาม GROUP_WAVE.md

Codex ดำเนินการตาม `docs/GROUP_WAVE.md` โดยแก้เฉพาะ Python Cockpit ไม่แตะ Go core หรือ `.proto`:

- เพิ่ม `GroupStore` ใน `frontend/swarmgod_gui/core/group_store.py` เก็บกลุ่ม 1–6 ใน SQLite ไฟล์เดียวกับรายการ IP
- เพิ่มชิปกลุ่ม, จำนวนสมาชิก, การเลือกหลายกลุ่ม, เมนูคลิกขวา, คีย์ลัด และป้ายกลุ่มบนการ์ดใน `app.py`/`fleet_item.py`
- ส่งหมายเลขกลุ่มไปแสดงในรายการโดรน Map 3D
- เพิ่ม `Waypoint.action` สำหรับ Servo A/B พร้อมเมนูคลิกขวา ป้ายสี A/B และกล่องยืนยันก่อน EXECUTE
- เพิ่ม WAVE เฉพาะ Waypoint GROUPED พร้อมลำดับกลุ่ม, รอ Disarm, RTL/Land pipeline, timeout 5 นาที, banner, สถานะความคืบหน้า และการยกเลิกทุกช่องทาง
- เพิ่ม `frontend/tests/test_groups.py` และ `frontend/tests/test_wave.py` ครอบคลุม persistence, offline selection, Head, no-flight side effect, WAVE sequencing, E-STOP/cancel, timeout และ servo confirmation

เฟส C (Head แยกต่อกลุ่มซึ่งต้องแก้ Go core) ยังไม่ได้ทำตามข้อห้ามในสเปก

## 25 สิงหาคม 2026 — ปรับการใช้งาน Cockpit รอบที่ 2

### Map 3D

Codex แก้ `frontend/swarmgod_gui/assets/map3d.html`, `frontend/swarmgod_gui/core/map_bridge.py` และ `frontend/swarmgod_gui/app.py`:

- เพิ่มปุ่มพับ/กางการ์ดโดรน
- คลิกรายการใน Map 3D เพื่อเลือกโดรนและ sync กับการ์ด/คำสั่งหลัก
- แสดงชื่อที่ผู้ใช้กำหนดและไฮไลต์ลำที่เลือก

### การ์ดรายละเอียดโดรน

Codex แก้ `frontend/swarmgod_gui/widgets/fleet_item.py` และ `frontend/swarmgod_gui/app.py`:

- เพิ่มปุ่มดินสอสำหรับแก้ชื่อโดรน และบันทึกชื่อกับ endpoint เดิม
- ลดโมเดลโดรนจาก 92×78 เป็น 72×60 พิกเซล
- ปรับป้าย HEAD และ READY/สถานะให้สูงเท่ากัน 18 พิกเซล
- เน้นช่อง MODE ด้วยกรอบขาวและสีตาม GUIDED/LOITER/RTL/LAND โดยคงตำแหน่งเดิม
- เพิ่มคำอธิบาย TAKEOFF ALT และ SPACING ใต้ช่องตั้งค่า

### Mission Log และโหมดซ้อม

Codex แก้ `frontend/swarmgod_gui/widgets/mission_log.py`, `frontend/swarmgod_gui/widgets/controls.py` และ `frontend/swarmgod_gui/app.py`:

- จัด Mission Log เป็น toolbar สองแถวคงที่ ปิด horizontal scrollbar ทั้ง toolbar และตาราง
- คืนแถบเลือกประเภท All / Commands / Alerts / Telemetry และตัวกรอง System ให้มองเห็นชัด โดยไม่ทำให้หน้าต่างถูกดันกว้าง
- ย้าย “รีเซ็ตแบตเตอรี่จำลอง (SITL)” จากแผงหลักไปไว้ในเมนูสามจุดด้านขวา
- ให้ผู้ใช้เลือกแรงดันก่อนรีเซ็ต พร้อมคำเตือนว่าใช้กับ SITL เท่านั้น

### คู่มือและการทดสอบ

Codex อัปเดต `frontend/swarmgod_gui/widgets/help_dialog.py`, `docs/USAGE.md` และ `docs/MAP3D.md` พร้อมเพิ่ม regression tests สำหรับทุกพฤติกรรมข้างต้น

## 25 สิงหาคม 2026 — ปรับ UI แผนที่และข้อมูลโดรน

### 1. ป้องกันแผนที่ขยายและดันแถบคำสั่งล้นจอ

Codex แก้ที่ `frontend/swarmgod_gui/app.py`:

- ฟังก์ชัน `_build_map()` — ทำให้ Map container, QWebEngineView และ QStackedWidget ย่อตัวตามพื้นที่หน้าต่างได้
- ทำให้แถบ LAT/LNG และพิกัดเมาส์ไม่เพิ่มความกว้างขั้นต่ำเมื่อข้อความ telemetry ยาวขึ้น
- จำกัดความกว้างป้ายสถานะ Map 3D และเก็บข้อความเต็มไว้ใน tooltip
- ฟังก์ชัน `_make_map3d_view()` — ป้องกัน WebView 3D ที่สร้างภายหลังเปลี่ยน minimum width ของหน้าต่างหลัก

### 2. ขยายโมเดลโดรนในการ์ดล่าง

Codex แก้ที่ `frontend/swarmgod_gui/widgets/fleet_item.py` ในคลาส `SelectedDroneCard`:

- ขยายพื้นที่รูปจาก 34×26 เป็น 92×78 พิกเซล
- แสดงภาพโมเดลด้วย Smooth Transformation
- เพิ่มกรอบและพื้นหลังให้โมเดลแยกจากข้อมูลสถานะชัดเจน

### 3. ปรับแผง Map 3D ให้ใช้คำว่า “โดรน”

Codex แก้ที่ `frontend/swarmgod_gui/assets/map3d.html`:

- เปลี่ยนหัวข้อที่ผู้ใช้เห็นจาก `FLEET` เป็น `โดรน`
- เปลี่ยนชื่อรายการจาก `UAV_N` เป็น `โดรน N`
- เพิ่มจำนวนโดรน สีประจำลำ ความสูง และโหมดบิน
- จัดรายการเป็นการ์ดแยกรายลำและรองรับพื้นที่หน้าจอแคบ

### 4. ย่อป้าย CORE และ LINK

Codex แก้ที่ `frontend/swarmgod_gui/app.py`:

- เพิ่มรูปแบบ compact ในฟังก์ชัน `_pill()`
- ลด padding, radius และความสูงของป้าย `CORE`, `LINK OK` และ `LINK DOWN`
- ล็อกความสูงไว้ที่ 20 พิกเซลเพื่อไม่ให้ Top bar ขยายตามข้อความ

### 5. อัปเดตคู่มือ

Codex แก้เอกสารและคู่มือในแอปดังนี้:

- `frontend/swarmgod_gui/widgets/help_dialog.py` — เพิ่มคำอธิบาย Map 2D/3D, แผงโดรน และป้าย CORE/LINK
- `docs/USAGE.md` — เพิ่มหัวข้ออ่านหน้าจอ Cockpit และแก้วิธีสั่ง GOTO/HOLD ให้ตรงกับระบบปัจจุบัน
- `docs/MAP3D.md` — อธิบายการ์ดโดรนรูปแบบใหม่
- `README.md` และ `docs/SWARM_CONTROL.md` — อัปเดตจำนวนชุดทดสอบ

### Regression tests ที่ Codex เพิ่ม

- `frontend/tests/test_ui_polish.py` — ตรวจว่าแถบพิกัดย่อได้และป้าย CORE/LINK มีขนาด compact
- `frontend/tests/test_help_and_alerts.py` — ตรวจขนาดและคุณภาพโมเดลโดรนในการ์ดล่าง
- `frontend/tests/test_map3d.py` — ตรวจข้อความภาษาไทยและโครงสร้างการ์ดโดรนใน Map 3D

ผลตรวจหลังแก้ล่าสุด: Python unittest **595/595 ผ่าน** (849.290 วินาที) พร้อม Python syntax และ diff validation

> หมายเหตุ: บันทึกนี้ระบุเฉพาะการแก้ไขรอบ UI วันที่ 25 สิงหาคม 2026 ที่ Codex ดำเนินการ ไม่ใช่ประวัติทั้งหมดของโปรเจกต์

## 25 สิงหาคม 2026 — Real-flight readiness hardening

ดำเนินการและบันทึกโดย **Codex** ตามคำขอให้แก้จุดเสี่ยงก่อนนำโดรนจริงขึ้นบิน

### Core และ Safety Envelope

- โหมด `hil` และ `production` ต้องตั้ง `SWARMGOD_HOME_LOC` เอง ห้ามรับพิกัดจำลองเป็น home โดยไม่ตั้งใจ
- ปฏิเสธ home ที่เป็น `NaN`/`Inf` และปฏิเสธชื่อ profile ที่ระบบไม่รู้จัก
- `production` บังคับ MAVLink strict signing, signing key อย่างน้อย 32 ไบต์ และห้ามใช้สวิตช์ปิด signing
- TAKEOFF ต้องได้รับ `confirmed=true` จากการยืนยันของผู้ใช้จริง
- RC/manual movement ตรวจ telemetry freshness, position, heading และฉายตำแหน่งล่วงหน้าผ่าน altitude/radius/geofence/separation ก่อนส่ง velocity
- Geofence ต้องยืนยันก่อนใช้ รองรับ request id ป้องกันคำสั่งซ้ำ และปฏิเสธ polygon ที่จุดไม่ครบ, ซ้ำ, เป็นเส้น, ไขว้กัน หรือพิกัดไม่ถูกต้อง

### E-STOP, RETURN และ Landing

- E-STOP และ Swarm STOP ยกเลิก goroutine ของ RETURN/LAND และรอให้หยุดก่อนส่ง hold
- RETURN ตรวจทั้งระยะในแนวราบและชั้นความสูงก่อนเริ่ม LAND
- RETURN แยกจุดลงรอบ home ให้ทุกลำห่างกันอย่างน้อย `MinSeparation` ป้องกันลำถัดไปลงทับลำบนพื้น
- ย้าย multi-drone RTL จาก timer อิสระใน UI ไปเป็น operation เดียวที่ core ควบคุม รองรับเฉพาะลำที่เลือกและ E-STOP ยกเลิกได้
- ยืนยันการลงจอดเมื่อต่ำกว่า 1 เมตร **และ** disarmed เท่านั้น
- หาก LAND ลำใดล้มเหลวหรือหมดเวลายืนยัน ระบบหยุดลำดับทันที ไม่สั่งลำถัดไปลง และเปลี่ยนลำที่เหลือไปใช้ FC RTL

### Cockpit และ Launcher

- Waypoint ขณะ Swarm active ตรวจ/สั่ง TAKEOFF ให้สมาชิกทุกลำก่อน แล้วจึงส่งเส้นทางเฉพาะ Head
- เพิ่มกล่องยืนยัน TAKEOFF และ Geofence โดยค่าเริ่มต้นของปุ่มยืนยันยังเป็น “ยกเลิก”
- Launcher build core ใหม่จาก source ปัจจุบันทุกครั้ง ป้องกันเปิด binary เก่า
- โหมดโดรนจริงอนุญาตเฉพาะ `hil`/`production`, ต้องมี home จริงและ cert/key ที่เตรียมไว้; production ต้องมี session token
- เพิ่ม `frontend/requirements-dev.txt` และ GitHub Actions สำหรับ backend race/vet/build กับ frontend compile/test
- เพิ่ม `docs/REAL_FLIGHT_CHECKLIST.md` เป็น acceptance gate ตั้งแต่ props-off, tethered hover ถึงหลายลำ พร้อมช่องลงนาม

### Regression tests ที่ Codex เพิ่ม

- Config: HIL home, non-finite home, production signing และ profile ไม่รู้จัก
- Safety: geofence validation และ stale manual telemetry
- Swarm: cancel return, altitude arrival และ landing confirmation
- Cockpit: Waypoint ตรวจ TAKEOFF ครบทุกสมาชิก, TAKEOFF cancel และ launcher production gates

ผลตรวจหลังแก้ครบ: `go test ./...` ผ่านทุก package, `go vet ./...` ผ่าน, Python compile ผ่าน และ Python unittest **598/598 ผ่าน** (652.907 วินาที)
