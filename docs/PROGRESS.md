# สิ่งที่ทำไปแล้ว (Progress Log)

อัปเดตล่าสุด: **15 ส.ค. 2026**

บันทึกงานที่ทำให้ SwarmGod รันได้จริง, redesign UI, และฟีเจอร์ cockpit ที่เพิ่มต่อเนื่องบนเครื่องนี้

---

## 1. ทำให้โปรเจครันได้

### ปัญหาที่พบ
- Python dependencies ยังไม่ได้ติดตั้ง (`PyQt5`, `grpcio`, `opencv-python`, `protobuf` ฯลฯ) → ดับเบิลคลิก `START_SWARMGOD.bat` แล้วเงียบ
- Go ยังไม่ได้ติดตั้งในเครื่อง
- Backend คอมไพล์ไม่ผ่าน:
  - ไม่มี entry point `backend/cmd/swarmgod-core`
  - ไม่มี package `backend/internal/audit` (ถูก import แต่ไฟล์หาย)
- Cockpit crash เพราะ QtWebEngine (แผนที่ Leaflet) — บนเครื่องนี้ Windows Text Services / TSF พัง ทำให้ Chromium engine crash

### สิ่งที่แก้
- สร้าง `backend/internal/audit` — append-only JSONL audit logger ตามที่ fleet/command/swarm เรียกใช้
- สร้าง `backend/cmd/swarmgod-core` — ประกอบ store → audit → telemetry/events/safety → fleet → command → swarm → gRPC server
- ติดตั้ง Python deps จาก `frontend/requirements.txt`
- ติดตั้ง Go แบบ portable (winget ติด UAC จึงใช้ zip แทน) และให้ launcher หา Go เจอ
- แก้ cockpit ให้ทนต่อ QtWebEngine ที่พังได้ (fallback เมื่อแผนที่ใช้ไม่ได้) — หลังรีสตาร์ตเครื่องหลายรอบ TSF กลับมา แผนที่ใช้ได้อีก

### ผลที่ยืนยัน
- Go core เปิด gRPC `:50051` ด้วย mTLS ได้
- Python cockpit เชื่อม core แบบ end-to-end (`GetFleetSnapshot` / `GetSwarmState`) ได้
- หน้าต่าง Launcher เปิดได้ (อาการดับเบิลคลิกแล้วเงียบหาย)

---

## 2. ติดตั้ง SITL (โดรนจำลอง) จนครบ pipeline

### ปัญหาที่พบ
- Launcher แจ้ง `✗ ล้มเหลว: SITL port 5760 not up`
- เครื่องยังไม่มี WSL distro / ArduPilot
- Launcher default distro เป็น `Ubuntu-22.04` แต่ที่ติดตั้งจริงคือ `Ubuntu` (24.04)

### สิ่งที่ทำ
1. เปิด Windows features: Virtual Machine Platform + WSL (ต้องรีสตาร์ต 2 รอบ เพราะรอบแรกติด elevation)
2. ลงทะเบียน **Ubuntu 24.04** ใน WSL 2
3. สร้าง user `pilot` (passwordless sudo) เป็น default user
4. ติดตั้ง prerequisites + clone ArduPilot (~1.5G พร้อม submodules)
5. build **ArduCopter SITL** สำเร็จ → `~/ardupilot/build/sitl/bin/arducopter`
6. แก้ `frontend/swarmgod_gui/launcher.py` ให้ default distro เป็น `Ubuntu`
7. ทดสอบพอร์ต `5760` เปิดถึงจาก Windows ผ่าน WSL2 localhost forwarding

### ผลที่ยืนยัน (checklist เขียวครบ)
- ตรวจเครื่องมือ (Go / Python / WSL)
- Certificates (mTLS + MAVLink signing key)
- Build core
- Core gRPC `:50051` [mTLS + signing]
- SITL ArduPilot พร้อม (`:5760`)
- Cockpit เปิดแล้ว

---

## 3. Redesign UI Cockpit

ธีมเดิม: hacker/tactical นีออนเขียว monospace  
ธีมใหม่: ดาร์คหรู สะอาดตา (graphite) อ้างอิง SPA / iOS dark

### กระบวนการ
- สร้าง mockup หลายรอบ (หลายแนวเลย์เอาต์) จนผู้ใช้ล็อกแบบที่เลือก
- ลงมือทำในโค้ด PyQt5 จริง แล้วรัน live กับ core + SITL

### เลย์เอาต์ที่ยืนยันแล้ว
| โซน | หน้าที่ |
|-----|--------|
| **บน** | แบรนด์, สถานะระบบ, สวิตช์ **UI / REMOTE** (แคปซูลสไลด์), นาฬิกา |
| **ซ้าย** | รายชื่อโดรนสูงสุด 5 ลำก่อนเลื่อน + การ์ดโดรนที่เลือก (รูป + IP + ping + CONN/DISC/DEL) + Quick Actions |
| **กลาง** | แผนที่เต็มพื้นที่ โล่ง — เหลือแค่ Layers + Zoom |
| **ล่าง** | Mission Log (แท็บ All/Commands/Alerts/Telemetry, กรองโดรนแบบ Stage pills, ค้นหา, Pause/Clear/Export) |
| **ขวา** | คำสั่งแบบ accordion: FLIGHT / MOVEMENT / FORMATION / SAFETY / GEOFENCE & MAP / CV TRACK |

### พฤติกรรมสำคัญ
- ค่าตัวเลขทุกช่องเป็น `− / slider / ช่องพิมพ์ / +` (ลาก กด หรือพิมพ์ได้)
- สวิตช์ **UI / REMOTE**: โหมด REMOTE ล็อกคำสั่งทุกหมวด (ยกเว้น CV TRACK)
- เมนูรอง/ตั้งค่าซ่อนไว้ กดค่อยเปิด
- กดคำสั่งแล้วมี **toast ลอยมุมล่างขวา** บอกสำเร็จ/ล้มเหลว

### ไฟล์หลักที่แตะ
- `frontend/swarmgod_gui/app.py`
- `frontend/swarmgod_gui/core/theme.py`
- `frontend/swarmgod_gui/widgets/*` (controls, fleet, mission log, drone card, formation, scan ฯลฯ)
- `frontend/swarmgod_gui/assets/drone_hero.png`

---

## 4. Redesign Launcher

ปรับ `frontend/swarmgod_gui/launcher.py` ให้เข้าชุดกับ cockpit และมือใหม่เข้าใจง่าย

### สิ่งที่เพิ่ม/เปลี่ยน
- การ์ดเลือกโหมดใหญ่ 2 อัน: **SITL (โดรนจำลอง)** กับ **โดรนจริง** พร้อมคำอธิบายภาษาไทย
- ปุ่ม **+/−** เลือกจำนวนโดรนจำลอง (แสดงเมื่อเลือกโหมด SITL)
- คำอธิบายสั้นๆ ใต้แต่ละขั้นใน checklist
- แบนเนอร์แนะนำสำหรับคนเปิดครั้งแรก
- ปรับเลย์เอาต์ไม่ให้สูงยาวเกิน — หน้าต่างประมาณ **960×560**, การ์ดโหมดคู่ซ้าย–ขวา
  - ซ้าย: จำนวนโดรน +/− · START/STOP · เปิด Cockpit
  - ขวา: checklist + log

ตรรกะ worker เดิมคงไว้ (certs → build → core → SITL → cockpit)

---

## 5. CV Tracking (ยังไม่ต่อกล้อง)

### สิ่งที่เพิ่ม
- `frontend/swarmgod_gui/core/cv_tracker.py` — Demo scene + file source, tracker worker (MIL / COLOR; CSRT/KCF ถ้า OpenCV มี)
- `frontend/swarmgod_gui/widgets/cv_track_panel.py` — แผงพรีวิว ลากเลือก ROI → TRACK / STOP
- ต่อใน cockpit หมวด **CV TRACK** (ไม่ถูกล็อกเมื่อโหมด REMOTE)
- รูปโดรน `assets/drone_hero.png` กราฟิกดำ+ไฟ cyan พื้น**โปร่งใส** (ไม่มีขาว/checkerboard)

### วิธีใช้สั้นๆ
1. เปิด cockpit → ขวา กาง **CV TRACK**
2. Source = **Demo** → กด **PREVIEW**
3. ลากกรอบบนเป้าเขียว → กด **TRACK**
4. ดูสถานะ LOCKED + ค่า dx/dy (offset จากกลางภาพ)

กล้องจริงยังไม่เปิด — เลือก File ได้ถ้ามีวิดีโอบนเครื่อง

---

## 6. ปรับเลย์เอาต์แถบซ้าย + การ์ดโดรน (12–13 ส.ค.)

### รายชื่อโดรน
- แถวสูงคงที่ (~52–56px) โชว์ครบตามจำนวนจริง
- **สูงสุด 5 ลำ** เห็นทั้งหมดโดยไม่เลื่อน — เกิน 5 ค่อยมีสกอลล์
- เลือกแล้วมีพื้นสีจางตามสีโดรนนั้น + จุด/ป้าย ID สี Stage
- รูปโดรนตัดพื้นชัดขึ้น (ช่องรูปสว่างกว่าแผง)

### การ์ด VEHICLE (ลำที่เลือก)
- ดันเนื้อหาไปทางขวา สมดุล ไม่ล้นขอบ
- **IP เต็มแถว** + PORT แถวถัดไป (ไม่ตัด `192.168.x.x` เป็น `...`)
- ปุ่ม **CONN / DISC / DEL** ใช้งานจริง (เชื่อม / ตัด / ลบออกจากฝูงเลย)
- ปุ่ม **PING** + สถานะ RTT (ICMP แล้ว fallback TCP)
- จุดสีเล็กเลือกสีโดรนได้ (palette แบบ Stage)
- ช่อง IP ยาวพอเห็นครบ, ปุ่ม SCAN + CONNECT จัดแถวบนใหม่
- แถบสถานะ **N ONLINE** เป็นเขียวมีจุด

### ไฟล์
- `frontend/swarmgod_gui/widgets/fleet_item.py`
- `frontend/swarmgod_gui/app.py` (`_update_fleet_scroll_height`, connect/disconnect/delete)

---

## 7. ปุ่มควบคุม + ฟอนต์ + toast

### Control widgets
- `Segmented` เป็น **แคปซูลสวิตช์** สไตล์ iOS (แท็บ log, target ฯลฯ)
- `CapsuleSwitch` — เม็ดสไลด์ไปมา **UI ↔ REMOTE**
- แผงข้างโปรขึ้น: hairline, มุมโค้ง, ไม่ฉูดฉาด

### FLIGHT
- ปุ่มโลโก้ + ข้อความ โทนเดียว ไม่หลากสี: ARM / DISARM / LAND / RTL / HOLD / TAKE OFF

### MOVEMENT
- D-pad ลูกศร ▲▼◀▶ + STOP กลาง
- ป้ายตัวหนังสือเล็กใต้ปุ่ม: FWD / BACK / LEFT / RIGHT / YAW L / YAW R / UP / DOWN

### Feedback
- กดคำสั่งแล้วมี **toast ลอยมุมล่างขวา** (สำเร็จ / เตือน / error) ไม่บังแผนที่
- ฟอนต์ UI = Segoe UI ให้คมบน Windows, ตัวอักษรหลักตัดพื้นมืดชัด (`#f3f6fa`)

### ไฟล์
- `frontend/swarmgod_gui/widgets/controls.py`
- `frontend/swarmgod_gui/core/theme.py`

---

## 8. สีโดรน + Mission Log กรองแบบ Stage

อ้างอิง UI แบบ Stage pills (สีทึบตัวขาว) จากภาพที่ผู้ใช้แนบ

- สีต่อโดรนใน `theme.DRONE_COLORS` — ผู้ใช้เปลี่ยนได้จากการ์ด
- คลิกโดรนบนแผนที่/รายการ → วงแหวนสีจางตามสีลำนั้น
- Mission Log: แถว pill **All + D1 D2 …** สีเดียวกับโดรน กรอง log ตามลำ
- pill ที่เลือกมีขอบขาว

### ไฟล์
- `frontend/swarmgod_gui/core/theme.py` (`drone_color`, `stage_pill_qss`)
- `frontend/swarmgod_gui/widgets/mission_log.py`
- `frontend/swarmgod_gui/assets/map.html` (สีหมุด + วงเลือก)

---

## 9. แผนที่ — หลายชั้น + เครื่องมือวาด + โล่ง

### ชั้นแผนที่ (Layers มุมบนขวา, ยุบได้)
Satellite · Hybrid · Streets · Road (OSM) · Topo · Terrain · OpenTopo · Dark · Light · Gray  
cache ผ่าน `core/tile_cache.py` เหมือนเดิม (offline ได้)

### เครื่องมือวาด geofence
- หลายแบบ: **polygon / สี่เหลี่ยม / วงกลม** (วงกลมแปลงเป็น polygon ให้ core)
- แผนที่โล่ง — ซ่อน attribution, เหลือแค่ **Layers + Zoom**

### ไฟล์
- `frontend/swarmgod_gui/assets/map.html`
- `frontend/swarmgod_gui/core/tile_cache.py`
- `frontend/swarmgod_gui/core/map_bridge.py`

---

## 10. Swarm — รูปขบวนกลับมาเป็นพระเอก

ตอน redesign UI แผง PATTERN หายจาก cockpit — **logic ใน Go ยังอยู่ครบ**

### สิ่งที่ทำ
- สร้าง `widgets/formation_picker.py` — ปุ่ม 5 รูปขบวนพร้อม schematic จุดโดรน
  สูตร offset เดียวกับ `backend/internal/swarm/formation.go`
  **WEDGE / LINE / COLUMN / DIAMOND / ECHELON**
- คลิกแล้วไฮไลต์**เฉพาะอันที่เลือก** (แก้บั๊กเหลืองทุกอัน / เหลืองค้างที่ WEDGE)
- ต่อ FORM UP ตามเดิม (spacing + safety ที่ core)

---

## 11. สแกน IP + จำ IP + กันซ้ำ

### Scan LAN
- `core/ip_scan.py` — สแกน subnet ปัจจุบัน พอร์ต MAVLink/SITL (5760, 5770, … 14550 ฯลฯ)
- `widgets/scan_dialog.py` — หน้าต่าง SCAN → เลือก hit → ใส่ช่อง IP

### จำ IP ใน SQLite
- `core/ip_store.py` — `~/.swarmgod/fleet_ips.db`
- เปิด cockpit แล้วโหลด IP ที่เคย add ไว้
- กด **DEL** = เอาออกจากฝูง **และ** ลบออกจากฐาน

### กัน IP ซ้ำ
- ถ้า host:port ซ้ำกับลำอื่น → **ไม่ add** + toast เตือนว่า IP ซ้ำ

### Ping
- `core/pinger.py` — ICMP แล้ว fallback TCP connect latency โชว์บนการ์ด

---

## 12. Save / Export / Load การตั้งค่า

- `core/settings_io.py` — JSON ที่ `~/.swarmgod/cockpit_settings.json`
- ปุ่ม **SAVE / EXPORT / LOAD** ใน cockpit (+ เมนู)
- SAVE = บันทึกค่าปัจจุบัน (รวม IP, สีโดรน ฯลฯ)
- EXPORT = เลือกที่เซฟไฟล์ `.json`
- LOAD = เลือกไฟล์แล้ว apply (sync ลงช่อง SAVE ด้วย)
- เปิดแอปแล้ว autoload ไฟล์ SAVE (ถ้ามี IP ใน SQLite แล้ว ไม่ทับจาก JSON)

---

## สถานะปัจจุบัน

| หัวข้อ | สถานะ |
|--------|--------|
| รัน core + cockpit (mTLS) | ✅ |
| SITL 1 ลำผ่าน launcher | ✅ |
| UI cockpit ธีมใหม่ตามดีไซน์ที่ล็อก | ✅ |
| Launcher ธีมใหม่ + อธิบายมือใหม่ | ✅ |
| แถบซ้ายโชว์โดรนครบถึง 5 ลำ | ✅ |
| CONN / DISC / DEL ใช้งานจริง | ✅ |
| สแกน IP ในวง LAN | ✅ |
| จำ IP ใน SQLite + กันซ้ำ | ✅ |
| SAVE / EXPORT / LOAD settings | ✅ |
| Ping ทดสอบลิงก์บนการ์ด | ✅ |
| รูปขบวน Swarm (PATTERN) | ✅ กลับมาใน UI |
| แผนที่หลายชั้น + วาดสี่เหลี่ยม/วงกลม | ✅ |
| Mission Log กรองโดรนสี Stage | ✅ |
| Toast ลอยมุมล่างขวา | ✅ |
| CV tracking (Demo/File, ไม่มีกล้อง) | ✅ แผง **CV TRACK** |
| รูปโดรนไม่มีพื้นขาว | ✅ `assets/drone_hero.png` |
| สั่งการโดรน SITL จริงผ่าน cockpit (arm/takeoff ฯลฯ) | ยังไม่ได้ทดสอบเต็มหลัง redesign |
| Multi-drone (2–5 ลำ) ลื่นขึ้น | launcher มี +/− แล้ว ยังไม่ได้ทดสอบ swarm ครบรอบนี้ |
| กล้อง FPV จริง | ยังไม่ทำ (จองไว้ใน UI เป็น Camera soon) |

---

## ระบบควบคุมฝูงโดรน (Swarm Control)

งานชุดใหญ่: การเลือกโดรน, Head/Leader, Take off 2 โหมด, RTL แยกชั้นกันชน,
ตรวจจับการชน, Command Summary และการแก้บั๊ก 10 รายการ

> รายละเอียดเต็ม + วิธีทดสอบ → **[SWARM_CONTROL.md](SWARM_CONTROL.md)**

| ฟีเจอร์ | สถานะ |
|---------|-------|
| เลือกโดรนจากการ์ดซ้าย + Ctrl+Click หลายลำ | ✅ |
| ปุ่ม FLEET (เลือก/ล้างทุกลำ) | ✅ |
| Head badge เหลือง + ตั้ง Head + Pop-up ยืนยัน | ✅ |
| Auto-Reassign Head เมื่อหัวหลุด | ✅ |
| เงื่อนไขห้ามเปลี่ยน Head (ตอน takeoff/land/ขบวนเคลื่อนที่) | ✅ |
| Take off 2 โหมด (All / Sequential) ดีฟอลต์ 20 m | ✅ |
| Swarm Take off = แนบ Form up อัตโนมัติ | ✅ |
| RTL แยกชั้นความสูง (+offset จากระดับปัจจุบัน) | ✅ |
| RTL แต่ละลำอิสระ (climb → return → land ไม่รอกัน) | ✅ |
| RTL config (base alt + min gap) + ปุ่มบันทึก | ✅ |
| สถานะ RTL บนการ์ดโดรน | ✅ |
| Collision Detection แจ้งเตือน real-time | ✅ |
| Collision Avoidance จัดคิวการเคลื่อนที่ | ✅ |
| คลิกแผนที่ = ทุกลำที่เลือกบินไป (คงรูปขบวน) | ✅ |
| Emergency Stop (รายลำ + ALL, ยืนยัน 2 ชั้น) | ✅ |
| Pre-flight Command Summary | ✅ |
| ป้ายโหมดการบิน FLIGHT / SWARM / RTL | ✅ |
| Quick Actions รายลำ (Arm/Disarm/RTL/Land/Hold) | ✅ |
| Map tooltip แบบ hover (ไม่รกจอ) | ✅ |
| ขยายฟอนต์ทั้งระบบ (274 widgets) | ✅ |
| จุดเป้ากะพริบ + เส้นประนำทาง (สีตามโดรน, หดสั้น real-time) | ✅ |
| ปุ่ม Cancel Nav (ล้างเป้า + หยุดลอยอยู่กับที่) | ✅ |
| Tactical Planning — วาดเส้น/รูป/วงกลม/ข้อความ/สัญลักษณ์ | ✅ |
| อัปโหลดสัญลักษณ์ยุทธวิธี + Save/Load GeoJSON | ✅ |
| ปุ่มเครื่องมือเป็นไอคอนเรขาคณิต + tooltip ตอน hover | ✅ |
| ปุ่ม Cancel Nav สีแดง ย้ายเข้าหมวด FLIGHT | ✅ |
| เมนู Save/Export/Load ย้ายไปแผงขวา | ✅ |
| พิกัด Lat/Long ตามเมาส์ (ใต้แผนที่, real-time) | ✅ |
| Tooltip โดรนพื้นขาวตัวหนังสือดำ | ✅ |
| โลโก้หน่วย (top bar / launcher / ไอคอนโปรแกรม) | ✅ |
| Waypoint Route Planning — วางจุด+เส้นประหลายจุด | ✅ |
| Waypoint: โดรนเดี่ยว + Swarm (คงรูปขบวน/Leader Path) | ✅ |
| Waypoint: Execute ทีละจุด + Undo + Clear | ✅ |
| Waypoint: โหมด GROUPED / SEPARATE (แยกเส้นทางรายลำ) | ✅ |
| Waypoint: กันชนก่อนบิน (ตรวจระดับความสูง+เส้นทางตัดกัน) | ✅ |
| Waypoint: โหมด Swarm กำหนดได้แค่ลำแม่ | ✅ |
| คู่มือการใช้งาน — Help modal (ปุ่ม "?" + เมนู ⚙) รวมวิธีใช้ + แผนที่ความสัมพันธ์ฟังก์ชัน | ✅ |
| Banner แดงอธิบายเหตุผลเมื่อเปลี่ยน Head ไม่ได้ (ไม่ใช่บั๊ก — ล็อกความปลอดภัยตั้งใจ) | ✅ |
| Banner เหลืองค้าง (persistent) ตอน REMOTE เปิด + เตือนซ้ำทุกครั้งที่กดคำสั่งถูกบล็อก | ✅ |
| Banner เว้นพื้นที่ไว้เสมอ — ไม่ทำ layout ขยับ/แผนที่วาบขาวตอนโผล่-หาย | ✅ |
| ฟอนต์เริ่มต้นแอป 130% (เดิม 120%) | ✅ |
| ปุ่ม Help สีชัดขึ้น (tinted accent แทน ghost เดิมที่กลืนกับปุ่มอื่น) | ✅ |
| แถบสีสถานะโหมดบน topbar (ป้ายโหมด → ก่อน CONTROL) เปลี่ยนสีตามโหมด แบบจางกว่า banner | ✅ |
| การ์ดเล็กในลิสต์ FLEET: เอาข้อความ "★ HEAD" ออก เหลือแค่สีปุ่มดาว — ไม่บังชื่อโดรน | ✅ |
| **Datalink จริง** — rssi/link_quality/drop_rate เข้า proto+Go (เดิมโชว์ 100% ปลอม) | ✅ |
| **Servo A/B** — implement Servo RPC ที่ค้างไว้ · A=ch7 B=ch8 | ✅ |
| **RC_CHANNELS_OVERRIDE** — สั่งได้ทั้งจาก cockpit และรีโมทพร้อมกัน (SERVO7/8_FUNCTION=57/58) | ✅ |
| อ่านสถานะ A/B จาก RC_CHANNELS จริง — เห็นได้เมื่อโยกสวิตช์ที่รีโมท | ✅ |
| กดปุ่มซ้ำ = ปล่อย override คืนช่องให้รีโมท · ปุ่มติดไฟบอกสถานะ | ✅ |
| **กติกา "1 ห้อง"** — เจ้าของสิทธิ์ต่อช่อง (UI/RC/ว่าง) · ฝ่ายที่ไม่ได้ถือสั่งไม่ได้ · ปุ่ม 3 สถานะ `A` `A ●` `A 🔒` | ✅ |
| ปุ่ม A/B ฝั่งขวาหมวด FLIGHT · DISARM ถามยืนยัน (A/B ตัดกล่องยืนยันออกภายหลัง §11.19) | ✅ |
| ป้ายสถานะ A(แดง)/B(เหลือง) สี่เหลี่ยมเล็กบนการ์ดโดรน | ✅ |
| ลงจอดเสร็จ → เข้าโหมด GUIDED อัตโนมัติ (กันยิงซ้ำ · REMOTE บล็อก) | ✅ |
| ป้ายโหมดปัจจุบันในหมวด FLIGHT (GUIDED เขียวเด่น) | ✅ |
| ป้ายโหมดกลาง topbar เลื่อนลงมาแบบอนิเมชัน (ถอด pill ซ้ายบนออก) | ✅ |
| ตั้งค่ารูปไอคอน top bar อัปโหลดได้สูงสุด 5 รูป เรียงซ้าย→ขวา | ✅ |
| SCAN: ติ๊กเลือก IP / SELECT ALL / เชื่อมต่อพร้อมกันหลายลำ | ✅ |
| **Hold ไม่ทำให้โดรนตก** — Cancel Nav / ยกเลิก Waypoint ค้างที่เดิมใน GUIDED (เดิมสั่ง LOITER แล้วร่วง) | ✅ |
| ปุ่ม SERVO A/B ไฟติดทันทีที่กด (ไม่รอ telemetry) + ค่าจริงชนะเมื่อมาถึง | ✅ |
| กดปุ่มคำสั่ง**ครั้งแรก**หลังเปิดโปรแกรมไม่ค้างอีก (prewarm dialog: 850 ms → 2.7 ms) | ✅ |
| ปุ่ม A/B ตัดกล่องยืนยันออก — กดแล้วยิงเลย ~2.4 ms 1 คลิก (ปุ่มอื่นยังถามเหมือนเดิม) | ✅ |
| **PWM ตรงกับรีโมทรายช่อง** — A=1950 · B=**2100** (เดิมส่ง 1950 ทั้งคู่ B ไปไม่สุดระยะ §11.22) | ✅ |
| `DIAG_RC.bat` / `SWARMGOD_RC_DEBUG=1` — log RC+SERVO ครบ 16 ช่อง + ตอน core ส่ง override (§11.23) | ✅ |
| **แก้ "กดครั้งแรกไม่ติด"** — คำสั่งวิ่งไปหาลำ offline ที่กู้จาก SQLite แทนลำที่ต่ออยู่จริง (§11.24) | ✅ |
| เปิดโปรแกรมมาฝูงว่าง — เลิกกู้การ์ด OFFLINE จาก SQLite (ผู้ใช้กด SCAN/CONNECT เอง §11.25) | ✅ |
| **หาต้นเหตุดีเลย์เจอ: MAVLink signing timestamp** — ปรับนาฬิกา GCS ลด 24 วิ → 6 วิ (§11.26) | ✅ |
| `TEST_NOSIGNING.bat` / `SWARMGOD_MAVLINK_SIGNING=off` — พิสูจน์ว่าเป็น signing จริงไหมใน 1 นาที (§11.30) | ✅ |
| **ผลทดสอบ: signing ไม่ใช่ต้นเหตุ** — ปิดแล้วดีเลย์ยังอยู่ · FC ไม่ได้บังคับ signing (§11.31) | ✅ |
| RC warm-up ตั้งแต่เชื่อมต่อ — ดีเลย์คำสั่งแรก **6-24 วิ → 2-3 วิ** (§11.32) | ✅ ยืนยัน n=5 |
| ปรับเกณฑ์เตือน 2 → 5 วิ (เดิมเด้งแทบทุกเที่ยวบินจนกลายเป็น noise) + วัดเวลาทุกเที่ยวบิน (§11.33) | ✅ |
| **`SYSID_MYGCS` ไม่ตรง** — FC ตั้ง 250 แต่ core ส่ง 255 · ตั้งได้แล้วผ่าน env + `START_SYSID250.bat` (§11.34) | ✅ ตั้งตรงแล้ว แต่ไม่ช่วยเรื่องดีเลย์ |
| **ปุ่ม A/B สถานะที่ 4 `⋯` = รอเครื่องบินตอบรับ** — ผู้ใช้ไม่ต้องเดาว่าต้องรอกี่วิ (§11.35) | ✅ |
| **หน้ารหัสผ่านก่อนเข้าโปรแกรม** — ธีมเข้าชุด cockpit · เก็บเป็น SHA-256 · เปลี่ยนรหัสได้ทาง env/ไฟล์ (§11.36) | ✅ |
| **อุ่นเครื่องช่อง A/B ให้เองหลังเชื่อมต่อ** + ปุ่มขึ้นสถานะ ⌛ → พร้อม (§11.37) | ✅ |
| ตรวจจับ "FC ไม่รับ override" แล้วเตือนใน Mission Log + บอกตอนกลับมารับ (§11.26, §11.28) | ✅ |
| **แก้ CONNECT พอร์ตซ้ำ** `host:5760:5760` ทำให้ต่อไม่ติดเลย (§11.27ก) | ✅ |
| **แก้แถวเก่าใน SQLite บล็อก CONNECT ถาวร** — ตัดด่าน SQLite ออกจากการเช็ค IP ซ้ำ (§11.27ข) | ✅ |
| ปิดเส้นทางกู้การ์ด offline เส้นที่สองผ่าน `cockpit_settings.json` (§11.27ค) | ✅ |
| **แก้ปุ่ม B ถูกบล็อกเงียบ** — RC8 ดีฟอลต์ 1800 ตอนไม่มีรีโมท ทำให้ UI คิดว่ารีโมทถือห้อง (§11.20) | ✅ |
| วัดเวลาจริงลง Mission Log ทุกครั้งที่กด A/B (ถึง core / เครื่องบินยืนยัน) | ✅ |
| Help modal อธิบาย Hold/LOITER + ตารางสถานะปุ่ม A/B | ✅ |
| **เทสต์อัตโนมัติ** | Python **486** · Go ผ่านทุก package |

| PRE-FLIGHT SUMMARY V2 — Flight Plan ที่เรียงคงที่ + frozen run snapshot | ✅ |
| PRE-FLIGHT SUMMARY V2 — execution timeline สำหรับ Takeoff/Swarm/Waypoint/Wave | ✅ |
| Callback เก่าหลัง Cancel/operation ใหม่ไม่ทับ timeline ปัจจุบัน (`run_id`) | ✅ |

**บั๊กสำคัญที่แก้** (ต้นตอ + วิธีแก้ ดูใน SWARM_CONTROL.md §11):
`SetLeader` ไม่ได้ implement ที่ Go · Form up เริ่มก่อนลำแม่ลอย ·
ลำดับ swarm takeoff กลับหัว · takeoff timeout ไม่พอ · GPS transient ถูกปฏิเสธทิ้ง ·
คลิกแผนที่บินลำเดียว · Quick Action disable ค้าง · ขยายฟอนต์ไม่ครอบคลุม ·
QTimer ทำงานหลังปิดหน้าต่าง (segfault) · checkbox หลุดเองจาก telemetry สะดุด ·
E-STOP ไม่เคลียร์สถานะ waypoint ที่กำลังบิน · `help_dialog.py` ลืม import `QWidget` ·
banner ทำ layout ขยับ/แผนที่วาบขาว · `theme._UI_SCALE` ไม่ sync กับ `self._font_scale` ·
★ HEAD บนการ์ดเล็ก (FleetItem) บังชื่อโดรนตอนฟอนต์ขยาย/ชื่อยาว ·
**LINK QUALITY โชว์ 100% ปลอม** (proto ไม่มี rssi เลย) · **Servo RPC ไม่เคยถูก implement** ·
**ID โดรนชนกันเมื่อ CONNECT หลายลำพร้อมกัน** (ดู SERVO_DATALINK.md) ·
**Hold สั่ง LOITER → โดรนตกตอนกด Cancel Nav** (LOITER เอา climb rate จากสติ๊กคันเร่ง §11.16) ·
**optimistic update ของปุ่ม SERVO เป็น dead code** (วางหลัง return ผิดฟังก์ชัน §11.17) ·
**กล่องยืนยันใบแรกของ process กิน 850 ms** ไปตกที่ปุ่มคำสั่งแรกพอดี (§11.18) ·
**ปุ่ม B ถูกบล็อกเงียบเพราะอ่าน RC input ดิบ** (ArduPilot ส่ง RC8=1800 แม้ไม่มีรีโมท §11.20)

---

## PRE-FLIGHT TEST — ด่านทดสอบก่อนบินจริง

หมวดบนสุดของแผงคำสั่ง มี 2 ปุ่มที่ต้องผ่านก่อนปล่อยบินจริง

- ปุ่ม **SYSTEM TEST** — ไล่ตรวจ 15 ข้อที่มีผลต่อความปลอดภัย/การสั่งการ
  (core+mTLS+token+profile · telemetry สด · สวิตช์ CONTROL · GPS/แบต/อยู่บนพื้น/พิกัด/home ·
  ระยะห่างกันชน · TAKEOFF ALT/RTL/Geofence/หัวขบวน/เส้นทาง Waypoint)
  แล้วยิงคำสั่งจริง: **HOLD** และ **สั่งโหมด GUIDED แล้วรอ FC รายงานกลับ** —
  พิสูจน์เส้นทาง cockpit → core → FC → telemetry ครบวง และจบด้วยโหมดที่ TAKEOFF ต้องใช้
  ถ้ามีข้อ critical ตกตั้งแต่รอบตรวจแรก จะไม่ยิงคำสั่งใด ๆ ใส่โดรนเลย
  ติ๊ก "ถอดใบพัดแล้ว" ปลดล็อกอีก 2 ข้อ: ARM→DISARM และตรวจว่า core ปฏิเสธ TAKEOFF ที่ไม่ยืนยัน
  (ถ้า core ดันยอมรับ = ไม่ผ่านทันที และสั่ง DISARM กลับให้เอง)
- ปุ่ม **CHECKLIST** — อ่าน `REAL_FLIGHT_CHECKLIST.md` มาเป็น checkbox 29 ข้อ
  จำสถานะที่ `~/.swarmgod/preflight_checklist.json`
- **ยังไม่ผ่านครบ** = ป้าย `● PREFLIGHT` แดงบน top bar (ทรง/ข้อความเดียวกับ `● CORE` — บอกสถานะด้วยสี เขียวเมื่อผ่าน) และทุกทางที่ทำให้โดรนลอยขึ้น
  (TAKEOFF · แผง All/Sequential · SWARM TAKE OFF · AUTO TAKEOFF ก่อน EXECUTE ROUTE)
  จะถามก่อน: ทดสอบก่อน (เทสให้แล้วบินต่อให้เลย) / ไม่เทส บินเลย (banner แดง + log ERROR) / ยกเลิก
- ผลเทสอยู่ตลอดการเปิดโปรแกรมครั้งนั้น เก็บในหน่วยความจำเท่านั้น — ปิดแล้วเปิดใหม่ต้องเทสใหม่ (fail-closed)

รายละเอียด: [PREFLIGHT_TEST.md](PREFLIGHT_TEST.md) · เทส 37 เคสใน `frontend/tests/test_preflight.py`

---

## วิธีเปิดใช้งาน

ดับเบิลคลิก `START_SWARMGOD.bat` หรือดูรายละเอียดใน [RUNBOOK.md](RUNBOOK.md)

ข้อมูลที่จำในเครื่อง (ไม่ได้อยู่ใน git):
- IP โดรน → `~/.swarmgod/fleet_ips.db`
- การตั้งค่า cockpit → `~/.swarmgod/cockpit_settings.json`
- แผนที่ offline → `~/.swarmgod/tiles/`
