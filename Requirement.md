# Requirement — SwarmGod / DroneGod

> ข้อกำหนดของระบบ Ground Control Station (GCS) ควบคุมฝูงโดรน ArduPilot
> อ้างอิงจากโค้ด/เอกสารจริงในโปรเจค (README, docs/, requirements.txt, go.mod)
> รูปแบบ ID: `REQ-<หมวด>-NNN` เพื่อ trace ได้

---

## 1. ภาพรวมระบบ

SwarmGod = GCS แบบ **hybrid**: **Go core (backend)** + **Python cockpit (frontend)** คุยกันผ่าน **gRPC (mTLS)** และต่อโดรนด้วย **MAVLink 2 (signed)** — รองรับทั้ง ArduPilot SITL และ Flight Controller จริง หลายลำพร้อมกัน

```
Python Cockpit (PyQt5 + Leaflet + OpenCV) ──gRPC/mTLS──► Go Core (gomavlib/goroutine ต่อลำ, safety, swarm, audit) ──MAVLink2 signed──► โดรน/SITL
```

---

## 2. System Requirements (สภาพแวดล้อม)

| ID | ข้อกำหนด | รายละเอียด |
|----|----------|-----------|
| REQ-SYS-001 | OS | Windows 10/11 (หลัก) · Linux ใช้ได้ |
| REQ-SYS-002 | Go | **≥ 1.25** (build core) |
| REQ-SYS-003 | Python | **3.x** (แนะนำ 3.12+) + pip |
| REQ-SYS-004 | WSL2 + ArduPilot | จำเป็นเฉพาะเมื่อใช้ **SITL** (`sim_vehicle.py`) — localhost forwarding |
| REQ-SYS-005 | เครือข่าย | เข้าถึง endpoint MAVLink ของโดรน (TCP/UDP) · gRPC bind `127.0.0.1` |
| REQ-SYS-006 | ติดตั้งครั้งเดียว | `SETUP.bat` ตรวจ+ติดตั้ง Go/Python/deps/certs/build core (ดู docs/MIGRATION.md) |

---

## 3. Software Dependencies

**Python (frontend/requirements.txt)** — REQ-DEP-001
- `PyQt5`, `PyQtWebEngine` (UI + Leaflet map ใน QWebEngine)
- `grpcio`, `grpcio-tools`, `protobuf` (คุยกับ core)
- `opencv-python`, `numpy` (CV tracking / video)
- `matplotlib` (3D formation viz)

**Go (backend/go.mod)** — REQ-DEP-002
- `bluenviron/gomavlib/v3` (MAVLink connect/parse/sign)
- `google.golang.org/grpc`, `protobuf` (gRPC server)
- `golang.org/x/crypto` (mTLS/security)
- `modernc.org/sqlite` (registry/audit index — pure Go SQLite)

---

## 4. Functional Requirements (ความสามารถ)

### 4.1 การเชื่อมต่อ + Telemetry
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-CONN-001 | ต่อโดรนหลายลำผ่าน MAVLink 2 (TCP/UDP) · สแกน/จำ IP ได้ |
| REQ-F-CONN-002 | รองรับ MAVLink signing (mTLS ระหว่าง cockpit↔core) |
| REQ-F-TEL-001 | แสดง telemetry สด: mode, armed, ตำแหน่ง, ความสูง, ความเร็ว, heading, แบต (V/%/A), GPS/sat |
| REQ-F-TEL-002 | **Datalink แสดงค่าจริง** — RSSI (SiK) + link quality (จาก heartbeat rate); ไม่มีข้อมูล = `N/A` (ไม่เดาว่าเต็ม) |

### 4.2 คำสั่งบิน (รายลำ + กลุ่ม)
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-CMD-001 | Arm/Disarm (ถามยืนยัน), Takeoff (2 โหมด), Land, RTL, เปลี่ยน Mode, Goto |
| REQ-F-CMD-002 | คำสั่งกลุ่มรายงานผล**รายลำ** (partial success — spec 8.3) |
| REQ-F-CMD-003 | ทุก mutating command มี **request_id** กัน duplicate (idempotency — spec 8.2) |
| REQ-F-CMD-004 | ลงจอดเสร็จเข้าโหมด **GUIDED อัตโนมัติ** |

### 4.3 Swarm
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-SWARM-001 | เลือกหลายลำ (Ctrl+Click / FLEET) · กำหนด Head + Auto-Reassign |
| REQ-F-SWARM-002 | รูปขบวน (formation) + Take off 2 โหมด · RTL แยกชั้นกันชน |
| REQ-F-SWARM-003 | **Collision Detection** + Command Summary |

### 4.4 Servo / ปล่อยของ (A/B)
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-SERVO-001 | ปุ่ม **A (CH7) / B (CH8)** สำหรับกลไกปล่อยของ — สั่งได้ทั้ง cockpit และสวิตช์รีโมท |
| REQ-F-SERVO-002 | ใช้ `RC_CHANNELS_OVERRIDE` + FC param `SERVO7/8_FUNCTION = RCIN7/8` · กดซ้ำ = คืนช่องให้รีโมท |
| REQ-F-SERVO-003 | ถามยืนยันทุกครั้ง · ป้ายสถานะ **A=แดง / B=เหลือง** อ่านจาก RC จริง (เห็นแม้โยกที่รีโมท) |

### 4.5 แผนที่ + วางแผนเส้นทาง
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-MAP-001 | Leaflet map + ภาพดาวเทียม + **offline tile cache** (ไม่พึ่ง CDN) |
| REQ-F-MAP-002 | Tactical map: จุดเป้ากะพริบ, เส้นนำทางสีตามโดรน (หดสด), Cancel Nav, เครื่องมือวาด (เส้น/รูป/วงกลม/ข้อความ/สัญลักษณ์) |
| REQ-F-MAP-003 | Waypoint 2 โหมด: **GROUPED** (ทั้งขบวนไปพร้อมกัน) / **SEPARATE** (อิสระ) · Undo/Clear/Execute |
| REQ-F-MAP-004 | **กันชนก่อนบิน** (SEPARATE): บล็อกถ้าคู่ระดับเดียวกันเส้นทางตัดกัน |
| REQ-F-MAP-005 | Geofence แยกจากเครื่องมือวาด · Save/Load เป็น GeoJSON |

### 4.6 UI / UX + ช่วยเหลือ
| ID | ข้อกำหนด |
|----|----------|
| REQ-F-UI-001 | ปุ่ม "?" เปิดคู่มือ + แผนผังความสัมพันธ์ฟังก์ชัน (modal) |
| REQ-F-UI-002 | แจ้ง banner แดง (เปลี่ยน Head ไม่ได้) / เหลือง (REMOTE เปิด) พร้อมเหตุผล · banner ไม่ทำ layout ขยับ |
| REQ-F-UI-003 | SAVE/EXPORT/LOAD · CV tracking · เลขอารบิก · โลโก้หน่วย |

---

## 5. Non-Functional Requirements

| ID | ด้าน | ข้อกำหนด |
|----|------|----------|
| REQ-NF-SAFE-001 | Safety | **Safety envelope บังคับที่ Go core** — UI ข้ามไม่ได้ (bypass ไม่ได้) |
| REQ-NF-SAFE-002 | Safety | ถ้าไม่แน่ใจว่าปลอดภัย → ไม่ส่งคำสั่ง · FC มี failsafe เป็น backstop |
| REQ-NF-SEC-001 | Security | mTLS (cockpit↔core) + MAVLink signing · certs ไม่ commit (gitignored) |
| REQ-NF-AUD-001 | Audit | **append-only audit log** ทุกคำสั่งที่ core + request_id ใน trail (spec 16.1) |
| REQ-NF-PERF-001 | Performance | 1 goroutine/โดรน (ไม่มี GIL) · telemetry throttle ที่ core, UI แค่ render · รองรับหลายสิบลำ |
| REQ-NF-REL-001 | Reliability | GUI ไม่ค้างเมื่อ telemetry ถี่ · reconnect ได้ |
| REQ-NF-TEST-001 | Test | มี automated tests (Python ~438 tests + Go ทุก package) |

---

## 6. Hardware / Drone-side Requirements

| ID | ข้อกำหนด |
|----|----------|
| REQ-HW-001 | Flight Controller รัน **ArduPilot** (ArduCopter) รองรับ MAVLink 2 |
| REQ-HW-002 | ตั้ง FC params: `SERVO7/8_FUNCTION=RCIN7/8` (servo A/B), MAVLink signing key ตรงกับ core, failsafe (FS_GCS/BATT/THR), geofence |
| REQ-HW-003 | Battery monitor ตั้งถูก (BATT_MONITOR + capacity) — ไม่งั้น safety gate บล็อก arm |
| REQ-HW-004 | Telemetry link: วิทยุ SiK (RSSI) หรือ WiFi/4G datalink ที่ยิง MAVLink สองทาง |
| REQ-HW-005 | (ทดสอบบนโต๊ะ) ถอดใบพัดก่อนทดสอบ arm/servo — ดู checklist ใน docs/SERVO_DATALINK.md |

---

## 7. ข้อจำกัด / สมมติฐาน (Constraints)

- Core bind `127.0.0.1` — ไม่เปิดออก public internet
- Python cockpit สั่ง raw MAVLink เองไม่ได้ — ต้องผ่าน core เท่านั้น
- SITL ต้องรันผ่าน `sim_vehicle.py` (ห้ามรัน binary ดิบ — lockstep พัง)
- การผ่าน automated test ≠ ปลอดภัยสำหรับบินจริง — ต้องผ่าน SITL → bench/HIL → staged flight → human review

---

## 8. เอกสารอ้างอิงในโปรเจค
`docs/ARCHITECTURE.md` · `docs/SECURITY.md` · `docs/RUNBOOK.md` · `docs/USAGE.md` · `docs/MIGRATION.md` · `docs/MISSIONPLANNER_SETUP.md` · `docs/SWARM_CONTROL.md` · `docs/SERVO_DATALINK.md` · `docs/PROGRESS.md`
