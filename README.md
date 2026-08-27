# 🛰️ SwarmGod

**Ground Control Station แบบ hybrid — Go core + Python cockpit**
ควบคุมฝูงโดรน (MAVLink) ได้พร้อมกันหลายลำ ออกแบบใหม่จากบทเรียนของ GCS_1
เน้น **ความเสถียร (stability)** และ **ความปลอดภัย (security)** เป็นอันดับหนึ่ง

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│   PYTHON COCKPIT (frontend)  │        │      GO CORE (backend)       │
│  PyQt5 hacker UI · Leaflet   │◄─gRPC─►│  gomavlib · goroutine/ลำ     │
│  OpenCV tracking · video     │  mTLS  │  telemetry · safety · swarm  │
└──────────────────────────────┘        └───────────────┬──────────────┘
        แสดงผล + สั่งการ                                 │ MAVLink (signed)
                                                โดรนจริง / ArduPilot SITL
```

## ทำไมต้องแยก Go + Python
| ปัญหาเดิมใน GCS_1 | ทางแก้ใน SwarmGod |
|--------------------|--------------------|
| `threading`+`asyncio`+`ThreadPoolExecutor` ปนกันจนเปราะ | **goroutine ต่อโดรน 1 ลำ** ใน Go — สะอาด, ไม่มี GIL |
| Safety เช็คแค่ฝั่ง UI (bypass ได้) | **Safety envelope บังคับที่ Go core** — UI ข้ามไม่ได้ |
| GUI ค้างเมื่อ telemetry มาถี่ | telemetry stream + throttle ที่ core, UI แค่ render |
| ไม่มี audit trail จริงจัง | **append-only audit log** ทุกคำสั่งที่ core |
| สเกลเกิน 5 ลำไม่ไหว | Go core รองรับหลายสิบลำ |

## โครงสร้างโปรเจค
```
SwarmGod/
├── proto/          # 📜 สัญญากลาง (gRPC) — Python & Go ใช้ร่วมกัน = แกนที่ทำให้ทุกอย่างสัมพันธ์
├── backend/        # 🐹 Go core: MAVLink, telemetry, safety, swarm, gRPC server
│   ├── cmd/swarmgod-core/   # entry point (binary เดียว)
│   └── internal/            # mavlink, fleet, telemetry, command, swarm, safety, api, audit
├── frontend/       # 🐍 Python cockpit: PyQt5 hacker UI, gRPC client, OpenCV, map
├── certs/          # 🔐 mTLS certificates (dev self-signed)
├── docs/           # 📖 ARCHITECTURE.md · SECURITY.md · การทำงาน
└── scripts/        # ⚙️ gen-proto, gen-certs, run-sitl
```

## ติดตั้งบนเครื่องใหม่ — ดับเบิลคลิก `SETUP.bat` 🔧

ตรวจและติดตั้งทุกอย่างที่ขาด (Go · Python · deps · certs · build core) ในคลิกเดียว
· รันซ้ำได้ปลอดภัย · มีโหมด "ตรวจอย่างเดียว" ถ้าอยากดูสถานะก่อน
— รายละเอียดใน [docs/MIGRATION.md](docs/MIGRATION.md)

## เริ่มใช้งาน — กดปุ่มเดียว ✅

**ดับเบิลคลิก `START_SWARMGOD.bat`** → Launcher จะจัดการให้ครบ (certs → build → core → SITL → cockpit)
พร้อม checklist โชว์สถานะสดจน "พร้อมใช้งาน" แล้ว cockpit เด้งขึ้นมาให้ใช้ทันที
(ปุ่ม STOP ALL ปิดทุกอย่างในคลิกเดียว · เอาเครื่องหมาย "ใช้ SITL" ออกถ้าจะต่อโดรนจริง)

<details><summary>หรือรันเองทีละส่วน (manual)</summary>

```bash
cd backend && go run ./cmd/gencerts -out ../certs   # certs (ครั้งเดียว)
# WSL: bash scripts/wsl_sitl.sh                       # SITL
cd backend && go run ./cmd/swarmgod-core             # core
cd frontend && python -m swarmgod_gui                # cockpit
```
ดูละเอียดใน [docs/RUNBOOK.md](docs/RUNBOOK.md)
</details>

> **ต้องมี:** Go, Python 3 (+ `pip install -r frontend/requirements.txt`), WSL+ArduPilot (สำหรับ SITL)

## สถานะการพัฒนา
ดูสรุปงานที่ทำไปแล้วใน [docs/PROGRESS.md](docs/PROGRESS.md)  
รายละเอียดแผนเฟส: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) หัวข้อ "Build Phases"  
ระบบควบคุมฝูงโดรน (Head, Take off, RTL, กันชน): [docs/SWARM_CONTROL.md](docs/SWARM_CONTROL.md)

บันทึกจุดที่ Codex แก้ไข: [docs/CODEX_CHANGES.md](docs/CODEX_CHANGES.md)
ก่อนใช้โดรนจริงต้องผ่าน: [docs/REAL_FLIGHT_CHECKLIST.md](docs/REAL_FLIGHT_CHECKLIST.md)  
ด่านทดสอบก่อนบินในโปรแกรม (2 ปุ่ม + ถามก่อน TAKEOFF): [docs/PREFLIGHT_TEST.md](docs/PREFLIGHT_TEST.md)

**อัปเดต 13 ส.ค. 2026:** รันครบ pipeline (core + mTLS + SITL + cockpit) · redesign UI · CV tracking · สแกน/จำ IP · SAVE/EXPORT/LOAD · รูปขบวน Swarm กลับมาใน cockpit — รายละเอียดใน [docs/PROGRESS.md](docs/PROGRESS.md)

**Swarm Control:** เลือกโดรนหลายลำ (Ctrl+Click / FLEET / Groups 1–6) · Head + Auto-Reassign · Waypoint WAVE + Servo action + AUTO TAKEOFF หลังยืนยัน · Take off 2 โหมด · RTL แยกชั้นกันชน · Collision Detection · Command Summary — ทดสอบอัตโนมัติ Python 598 tests + Go ทุก package — รายละเอียดใน [docs/SWARM_CONTROL.md](docs/SWARM_CONTROL.md)

**Tactical Map:** จุดเป้ากะพริบ + เส้นประนำทางสีตามโดรน (หดสั้น real-time) · ปุ่ม Cancel Nav · เครื่องมือวาดแผนยุทธวิธี (เส้น/รูป/วงกลม/ข้อความ/สัญลักษณ์อัปโหลดเอง) แยกจาก Geofence · Save/Load เป็น GeoJSON — ทำงาน offline ไม่พึ่ง CDN

**UI/UX:** ปุ่มเครื่องมือเป็นไอคอนเรขาคณิต (คำอธิบายโผล่ตอน hover) · ปุ่ม Cancel Nav สีแดงในหมวด FLIGHT · เมนู Save/Export/Load รวมไว้หัวแผงขวา · พิกัด Lat/Long ตามเมาส์แสดงใต้แผนที่แบบ real-time · โลโก้หน่วยบน top bar / launcher / ไอคอนโปรแกรม

**Waypoint Route Planning:** สวิตช์ Waypoint Mode ในแผงขวา — เปิดแล้วคลิกแผนที่ = วางจุด+เส้นประ (ไม่บินทันที) · **2 โหมด**: GROUPED (เส้นทางร่วม ทั้งขบวนไปพร้อมกัน คงรูปขบวน) และ SEPARATE (แต่ละลำมีเส้นทางของตัวเอง บินอิสระไม่รอกัน) · Undo/Clear/Execute · **กันชนก่อนบิน**: SEPARATE จะตรวจว่ามีคู่ไหนอยู่ระดับความสูงเดียวกันแล้วเส้นทางตัดกันไหม ถ้าเจอจะบล็อกไว้พร้อมบอกคู่ที่เสี่ยง · โหมด Swarm กำหนดได้แค่ลำแม่ (Leader Path) · reuse Goto RPC เดิม ไม่แก้ backend

**Servo A/B + Datalink:** ปุ่ม **A (CH7)** / **B (CH8)** ในหมวด FLIGHT สำหรับกลไกปล่อยของ — **สั่งได้ทั้งจาก cockpit และจากสวิตช์บนรีโมท** (ใช้ `RC_CHANNELS_OVERRIDE` ร่วมกับ `SERVO7/8_FUNCTION = RCIN7/8`) · กดซ้ำ = คืนช่องให้รีโมท · ถามยืนยันทุกครั้ง (DISARM ก็ถามยืนยันด้วย) · ป้ายสี่เหลี่ยม **A=แดง / B=เหลือง** อ่านจาก RC จริงจึงเห็นแม้โยกสวิตช์ที่รีโมท · ลงจอดเสร็จเข้าโหมด **GUIDED อัตโนมัติ** · **Datalink แสดงค่าจริง** (RSSI จากวิทยุ SiK + link quality จากอัตรา heartbeat; ไม่มีข้อมูลจะโชว์ `N/A` ไม่ใช่เดาว่าเต็ม) — รายละเอียด + **checklist ทดสอบบนโต๊ะก่อนบิน** ใน [docs/SERVO_DATALINK.md](docs/SERVO_DATALINK.md)

**PRE-FLIGHT TEST (ก่อนบินจริง):** หมวดบนสุดของแผงขวา 2 ปุ่ม — ปุ่ม **SYSTEM TEST** ไล่ตรวจให้เองเป็นรายการ (core/mTLS/token · telemetry สด · GPS/แบต/อยู่บนพื้น · ระยะห่างกันชน · ค่า TAKEOFF/RTL/Geofence/หัวขบวน) แล้ว**ยิงคำสั่งจริง**ดูว่า FC ตอบรับโหมด GUIDED กลับมาไหม · ติ๊ก "ถอดใบพัดแล้ว" เพื่อเทส ARM→DISARM และตรวจว่า core ปฏิเสธ TAKEOFF ที่ไม่ยืนยัน — และปุ่ม **CHECKLIST** ที่เอาเช็คลิสต์ของ Codex มาเป็น checkbox ติ๊กได้ จำข้ามรอบเปิดโปรแกรม · ยังไม่ผ่านครบ = ป้าย **● PREFLIGHT** แดงบน top bar (ทรงเดียวกับ ● CORE / LINK · เขียวเมื่อผ่านครบ) — ปุ่ม LAN (Field Tablet) ย้ายลงไปหัวแผง COMMANDS เพื่อให้ top bar เหลือเฉพาะป้ายสถานะ และทุกครั้งที่สั่ง TAKEOFF ระบบจะถามก่อน (**RUN TEST** = เทสให้แล้วบินต่อให้เลย / **SKIP** = บินได้แต่ขึ้น banner แดง + บันทึก log / **ยกเลิก**) — รายละเอียดใน [docs/PREFLIGHT_TEST.md](docs/PREFLIGHT_TEST.md)

**Help modal + แจ้งเตือน:** ปุ่ม "?" สีฟ้าเด่นบน top bar (หรือเมนู ⚙) เปิดคู่มือการใช้งาน + แผนที่ความสัมพันธ์ระหว่างฟังก์ชันแบบ modal · แจ้ง banner แดงพร้อมเหตุผลทุกครั้งที่เปลี่ยน Head ไม่ได้ (RTL/TAKEOFF-LANDING/ขบวนกำลังเคลื่อนที่) · แจ้ง banner เหลืองค้างตลอดเวลาที่ REMOTE เปิด และเตือนซ้ำทุกครั้งที่กดคำสั่งบินแล้วถูกบล็อก · banner เว้นพื้นที่ไว้เสมอ ไม่ทำแถบขยับ/แผนที่วาบขาว · แถบสีใต้ป้ายโหมดบน topbar เปลี่ยนตามโหมดบิน (จางกว่า banner กันสีซ้ำ) · ฟอนต์เริ่มต้น 130%
