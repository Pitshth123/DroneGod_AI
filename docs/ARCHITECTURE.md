# SwarmGod — สถาปัตยกรรมระบบ (Master Design)

> เอกสารนี้คือ "แผนที่ใหญ่" ของทั้งระบบ — อธิบายว่าแต่ละส่วนสัมพันธ์กันยังไง
> ทุกไฟล์โค้ดต้องอ้างอิงกลับมาที่เอกสารนี้ได้

---

## 1. หลักการออกแบบ (Design Principles)

1. **Single source of truth = proto contract** — Python กับ Go ไม่คุยกันด้วยข้อความมั่วๆ
   แต่ผ่าน schema ที่ generate จาก `proto/` ทั้งคู่ → เปลี่ยน field ที่เดียว sync ทั้งระบบ
2. **Core owns safety** — การตัดสินใจอันตราย (arm, takeoff, kill, geofence) บังคับใช้ที่ **Go core**
   ไม่ใช่ที่ UI — เพราะ UI ถูก bypass ได้ แต่ core เป็นด่านเดียวที่คุยกับโดรน
3. **Fail-safe by default** — ถ้าไม่แน่ใจ = ไม่ทำ. link ขาด = โดรนเข้า failsafe. command ไม่มี ACK = ถือว่าล้มเหลว
4. **UI คือกระจกสะท้อน ไม่ใช่สมอง** — Python แค่ render state + ส่ง intent. logic อยู่ Go
5. **ทุก command ถูก audit** — append-only log ที่ core ก่อนส่งออกทุกครั้ง

---

## 2. ภาพรวม 3 ชั้น (Three-Tier)

```
┌─────────────────────────────────────────────────────────────────────┐
│  TIER 1 — COCKPIT (Python / PyQt5)                                   │
│  • หน้าจอ cockpit: fleet cards, map, video, command panels          │
│  • gRPC client (telemetry stream ↓ + command calls ↑)               │
│  • OpenCV target tracking, Leaflet map                              │
│  ไม่แตะ MAVLink โดยตรงเลย                                            │
└───────────────────────────────┬─────────────────────────────────────┘
                        gRPC over mTLS (localhost:50051)
                        - Telemetry: server-streaming
                        - Commands : unary + ACK
                        - Events   : server-streaming (alarms, audit)
┌───────────────────────────────┴─────────────────────────────────────┐
│  TIER 2 — CORE (Go)                                                  │
│  ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌──────────────┐     │
│  │  api/      │  │ command/   │  │ safety/  │  │  swarm/      │     │
│  │ gRPC server│─▶│ dispatcher │─▶│ envelope │  │ formation    │     │
│  └────────────┘  └─────┬──────┘  └────┬─────┘  └──────┬───────┘     │
│         ▲              │              │               │             │
│  ┌──────┴─────┐   ┌────▼──────────────▼───────────────▼──────┐     │
│  │ telemetry/ │◄──│           fleet/ (manager)               │     │
│  │ aggregator │   │  ถือ Drone หลายตัว (1 goroutine/ลำ)      │     │
│  └────────────┘   └────────────────────┬─────────────────────┘     │
│  ┌──────────┐                          │                           │
│  │ audit/   │  บันทึกทุก command       │ mavlink/ (gomavlib)       │
│  └──────────┘                          ▼                           │
└────────────────────────────────────────┬────────────────────────────┘
                            MAVLink (UDP/TCP/serial, HMAC-signed)
┌────────────────────────────────────────┴────────────────────────────┐
│  TIER 3 — VEHICLES                                                   │
│  ArduPilot SITL (WSL)  /  Flight Controllers จริง (sysid 1..N)      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Mapping จาก GCS_1 เดิม → SwarmGod (ทุกอย่างมีที่อยู่ใหม่)

| ของเดิม (GCS_1) | ย้ายไปที่ | ภาษา |
|------------------|-----------|------|
| `drone.py` (connect, recv thread, commands) | `backend/internal/mavlink/` + `command/` | Go |
| `drone_manager.py` (multi-drone, threadpool) | `backend/internal/fleet/` | Go |
| `telemetry.py` (bridge, throttle) | `backend/internal/telemetry/` | Go |
| `heartbeat_monitor.py` | `backend/internal/fleet/heartbeat.go` | Go |
| `command_tracker.py` (uuid lifecycle) | `backend/internal/command/tracker.go` | Go |
| `safety.py` (alt/distance limits) | `backend/internal/safety/` (บังคับใช้จริง) | Go |
| `swarm_manager.py` (leader-follower) | `backend/internal/swarm/` | Go |
| `event_logger.py` (JSONL) | `backend/internal/audit/` | Go |
| `param_manager.py` | `backend/internal/command/params.go` | Go |
| `main.py` GUI (6368 บรรทัด!) | `frontend/swarmgod_gui/` (แตกเป็นหลายไฟล์) | Python |
| `widgets.py` DroneCard | `frontend/.../widgets/drone_card.py` | Python |
| `dialogs.py` | `frontend/.../widgets/dialogs.py` | Python |
| `themes.py` | `frontend/.../core/theme.py` (hacker theme) | Python |
| `video_stream.py`, `cv_tracker.py` | `frontend/.../core/` (คง OpenCV ไว้) | Python |
| `bridges.py` + `map.html` + Leaflet | `frontend/.../core/map_bridge.py` + assets | Python/JS |
| SITL launcher (WSL) | `backend` มี endpoint + `frontend` มี panel | ทั้งคู่ |

---

## 4. Data Flow (ต้องเข้าใจ 2 เส้นนี้ = เข้าใจทั้งระบบ)

### 4.1 Telemetry (โดรน → หน้าจอ)
```
FC/SITL ─MAVLink─► mavlink.Conn (goroutine/ลำ) อ่าน HEARTBEAT/GLOBAL_POSITION_INT/SYS_STATUS/...
   ▼ parse → อัปเดต fleet.Drone.state (mutex-guarded)
   ▼ push เข้า telemetry.Aggregator (throttle ~10Hz/ลำ, emit ทันทีถ้า critical เปลี่ยน)
   ▼ gRPC server-stream `SubscribeTelemetry` → ส่ง Telemetry message
Python: TelemetryClient รับ stream → emit Qt signal → DroneCard/Map อัปเดต
```

### 4.2 Command (กดปุ่ม → โดรน)
```
Python: กด TAKEOFF → grpc client เรียก SendCommand(TakeoffCommand{alt})
   ▼ gRPC (mTLS + token) → api.Server
   ▼ command.Dispatcher: validate → safety.Envelope.Check() → ผ่าน?
   ▼ audit.Log(command)  ← บันทึกก่อนส่งเสมอ
   ▼ fleet.Drone.Send(MAV_CMD_NAV_TAKEOFF) → รอ COMMAND_ACK (context timeout)
   ▼ ได้ ACK → CommandResult{ok, result_code} → ตอบกลับ Python
Python: แสดงผลสำเร็จ/ล้มเหลว + reason
```

**กฎเหล็ก:** ทุก command ผ่าน `safety.Envelope.Check()` ก่อนเสมอ — ไม่มีทางลัด

---

## 5. รายการค่าที่ต้องแสดง (สืบทอดจาก GCS_1 — ต้องครบ)

**ต่อโดรน (DroneCard):**
`name · status(badge) · IP · PORT · MODE · ALT · LINK(uptime+clock) · BAT%(+Voltage) · sat · gps_fix`

Status badges: `OFFLINE · CONNECTING · RECONNECTING · READY · ARMED · TAKEOFF · FLYING · HOLD · MOVING · LANDING · RTL · DISARMED`

**Telemetry เต็ม (ในตาราง/overlay):**
`lat · lon · alt_rel · alt_abs · vx/vy/vz · speed · heading · roll · pitch · battery% · voltage · mode · armed · gps_fix · sat_count · total_distance · flight_time`

---

## 6. รายการคำสั่ง (Command Surface — ต้องครบตาม GCS_1)

| กลุ่ม | คำสั่ง |
|------|--------|
| Connection | connect / disconnect(logout) / reconnect |
| Arm | arm / disarm / **kill (emergency)** |
| Takeoff/Land | takeoff(alt) / land / rtl / hold(loiter) |
| Navigate | goto(lat,lon,alt) / change_alt(±/set) / equalize_alt / change_speed |
| Attitude/RC | rc_move(fwd/bwd/left/right/up/down) / yaw(L/R/set) / stop_all |
| Mode | set_mode(STABILIZE/GUIDED/LOITER/RTL/LAND/ALT_HOLD/POSHOLD/AUTO/…) |
| Mission | upload / start / pause / resume / stop / clear |
| Orbit | orbit(lat,lon,radius) |
| Gimbal | pan / tilt / center / release |
| Servo | set / release / reset |
| Swarm | set_leader / set_offset(N,E,Up) / heading_mode / yaw_mode / start / hold / stop |
| Params | fetch / get / set |

---

## 7. Concurrency Model (Go core)

- **1 goroutine อ่าน (reader)** ต่อโดรน 1 ลำ — loop `conn.Read()` แล้ว dispatch ตาม msg type
- **1 goroutine heartbeat** ต่อลำ — ส่ง GCS heartbeat + เช็ค timeout (โดรนหาย)
- **fleet.Manager** ถือ `map[uint32]*Drone` ป้องกันด้วย `sync.RWMutex`
- **telemetry.Aggregator** ใช้ channel รวม state จากทุกลำ → fan-out ไป subscriber (gRPC streams)
- **command.Dispatcher** ส่งขนานด้วย goroutine + `errgroup` (แทน ThreadPoolExecutor เดิม)
- **context.Context** ทุก command มี deadline — ไม่มี hang ค้าง

---

## 8. Build Phases (แผนพัฒนาเป็นเฟส — แต่ละเฟสรันได้จริง)

- [x] **Phase 0 — Foundation**: โครงสร้าง, proto contract, docs, security design
- [x] **Phase 1 — Go MAVLink core**: ✅ connect SITL (gomavlib), อ่าน telemetry, gRPC stream — **verified end-to-end กับ ArduCopter SITL** (ดู docs/RUNBOOK.md)
- [x] **Phase 2 — Python cockpit shell**: ✅ PyQt5 hacker UI, gRPC client (telemetry thread), fleet cards + live console — **verified: การ์ด UAV_1 อัปเดตสดจาก SITL** (Python 3.14, grpcio cp314)
- [x] **Phase 3 — Commands**: ✅ arm/takeoff/land/rtl/hold/goto/mode ผ่าน gRPC + safety envelope + audit — **verified: สั่งจาก cockpit → SITL ไต่ 0→20m + goto เคลื่อนที่จริง + safety บล็อก alt 250m + audit ครบ**
- [x] **Phase 4 — Map (Leaflet + offline)**: ✅ QWebEngine + Leaflet + ภาพดาวเทียมจริง (Esri), หมุดโดรน+trail ตาม GPS, คลิก=GOTO, tile-cache proxy (offline), ปุ่ม CACHE AREA — **verified: โดรนบินบนแผนที่จริง + GOTO + offline cache**. (video + CV tracking = Phase 4b)
- [x] **Phase 5 — Swarm**: ✅ leader-follower formation + **auto leader-failover** + SET ALTITUDE — verified 3-SITL: ฝูงเกาะฟอร์เมชัน (wedge), ตัวแม่หาย→ตัวถัดไปเป็นแม่ใน 1s, UI โชว์ ★ LEADER + วงแหวนทอง, spacing≥MinSeparation, ทุกจุดผ่าน safety.CheckPoint + audit
- [x] **Phase 6 — Hardening**: ✅ mTLS (cockpit↔core, auto-enable เมื่อมี cert) + MAVLink signing (HMAC, core↔drone) + failsafe (link-loss WARN→ALARM+RTL, battery) + geofence (วาดจาก UI, บังคับที่ core) + event/alarm stream. verified: flyctl บินผ่าน mTLS+signed, kill SITL→failsafe alarm, geofence reject นอกรั้ว. cert gen: `go run ./cmd/gencerts`
- [ ] Phase 4b — FPV **camera** (ยังไม่ทำ) · ✅ **CV tracking โครงแรกพร้อม** (Demo/File, CSRT/KCF/MOSSE ใน cockpit — ยังไม่ต่อกล้อง)

> แต่ละเฟสจบแล้วต้องรันได้และทดสอบได้ ไม่ทำ big-bang
