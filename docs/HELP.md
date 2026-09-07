# SwarmGod Cockpit — Help & Operations Guide

> **ฉบับใช้งานหน้างาน + อ้างอิงโครงการ V3**
> อัปเดต: **2026-09-06**
> Active project: `C:\Users\staff\OneDrive\Desktop\DroneNew`

---

## 🧭 อ่านตรงนี้ก่อน — ระบบนี้คืออะไร

**SwarmGod / DroneGod_AI V3** คือ Ground Control Station (GCS) สำหรับควบคุมโดรน ArduPilot โดยแยกหน้าที่เป็น 3 ชั้น:

```text
┌──────────────────────────────────────────────┐
│  Python Cockpit / PyQt5                     │
│  UI · Map · Fleet · Operator Intent         │
└──────────────────┬───────────────────────────┘
                   │ gRPC + mTLS + Session Token
                   ▼
┌──────────────────────────────────────────────┐
│  Go Core                                     │
│  Command · Safety · Mission · Failsafe       │
│  Ownership · Dedup · Persistence · Audit     │
└──────────────────┬───────────────────────────┘
                   │ MAVLink 2 + Signing
                   ▼
┌──────────────────────────────────────────────┐
│  ArduPilot SITL / Flight Controller จริง    │
└──────────────────────────────────────────────┘
```

หลักการสำคัญที่สุดคือ **UI ไม่ควรเป็น flight authority สุดท้าย** สำหรับ scope ที่ย้ายมา Core แล้ว ทุกคำสั่งสำคัญต้องผ่าน Core, ownership guard และ safety path ก่อนถึง FC

---

# 🚦 CURRENT V3 STATUS

| Stage | สถานะปัจจุบัน | ความหมาย |
|---|---|---|
| V3-S01–S08 | ✅ **DONE** | Foundation, telemetry, command gateway, dedup, no-dual-authority ผ่านแล้ว |
| V3-S09 | 🟡 **PRE-FLIP REVIEW PASS** | 0 Critical / 0 High; **ยังไม่ flip authority** และยังไม่มีหลักฐาน administrative freeze ว่าเสร็จแล้ว |
| V3-S10 | ✅ **COMPLETE** | Persistence + restartability / no-auto-resume |
| V3-S11 | ✅ **COMPLETE** | Failure injection / Expected Safe State |
| V3-S12 | ✅ **CURRENT OPERATIONAL GATE COMPLETE** | 6h endurance + reconnect + client lifecycle ผ่านแล้ว |
| V3-H01 | ✅ **DONE** | Hardware bench tooling พร้อม |
| V3-H02 | 🟠 **IN PROGRESS** | Actual FC: 5.1–5.3 PASS; operator-visible GUIDED/ARM/DISARM reached; 5.4–5.9 ยังต้องพิสูจน์ |
| V3-R01 | 🔒 **LOCKED** | ยังไม่ใช่ controlled real-flight release |

### Authority ที่อนุญาตอยู่ตอนนี้

```text
core-single
core-single-wait
```

ยัง **ไม่เปิด live**:

```text
core-grouped-multi
core-separate
core-swarm-leader
core-wave
core-payload
```

> **สำคัญ:** PRE-FLIP implementation หรือ unit test ผ่าน **ไม่เท่ากับ** production authority ถูกเปิดแล้ว

---

# ⚡ QUICK START — เปิดระบบให้ถูกโหมด

## 1) ดู Profile ก่อน

| Profile | ใช้ทำอะไร | Flight-mutating RPC |
|---|---|---|
| `setup` | ตั้งค่า/อ่าน telemetry | ❌ telemetry-only |
| `hil` | Actual-FC bench ที่มี physical interlock | ✅ เฉพาะ approved bench scope |
| `sitl` | Simulator | ✅ ตาม SITL authority gate |
| `production` | Production profile | 🔒 ต้องผ่าน release gate/credential ที่กำหนด |

ถ้า Core log ขึ้น:

```text
profile=setup
setup profile: telemetry-only bootstrap
```

แล้วกด MODE GUIDED/ARM/mission command จะถูกปฏิเสธโดยตั้งใจ

## 2) เชื่อม Drone

Actual FC bench endpoint ที่ใช้ใน H02 ปัจจุบัน:

```text
Drone 1
TCP 192.168.9.184:5760
```

ก่อนสั่งอะไร ให้รอ log:

```text
[fleet] Drone 1 reader started (tcp://192.168.9.184:5760)
```

และหน้า Cockpit ต้องมี telemetry จริง ไม่ใช่แค่ TCP port เปิด

## 3) SYSTEM TEST

โหมด `hil` / `production` ต้องตรวจอย่างน้อย:

- Core reachable
- mTLS
- `SWARMGOD_TOKEN`
- profile ถูกต้อง
- HOME location
- telemetry freshness
- GPS / battery / link
- control ownership UI/REMOTE
- configuration / separation checks ตาม scope

## 4) CHECKLIST

SYSTEM TEST คือสิ่งที่เครื่องตรวจได้ ส่วน CHECKLIST คือสิ่งที่ operator ต้องตรวจด้วยตา/ขั้นตอนจริง เช่น physical setup, area, airframe, remote/manual path

## 5) H02 ไม่ใช่ real flight

H02 คือ Actual-FC **bench validation** เท่านั้น ต้องรักษา physical interlock ของ session และไม่ถือว่าผ่าน R01 เพียงเพราะ GUIDED/ARM/DISARM ทำงาน

---

# 🛡️ SAFETY / AUTHORITY MODEL

## Exactly one authority per drone

เป้าหมายของ V3 คือห้าม Python และ Go ส่ง navigation authority แข่งกันบนโดรนตัวเดียว

ลำดับความสำคัญโดยแนวคิด:

```text
Emergency / STOP / Takeover
          ↓
Battery / Link / FC Failsafe
          ↓
Safety Envelope
          ↓
Operator Cancel
          ↓
Mission progression
```

## Core restart

Mission ที่เคย active ต้องไม่กลับมาบินต่อเองหลัง Core restart

```text
Old active evidence
       ↓ restart
RECOVERY_REQUIRED / INTERRUPTED
Active = false
Authority = false
No automatic flight command
```

## UI restart

UI reconnect ควร query run เดิม ไม่สร้าง StartMission ซ้ำ

---

# 🧪 TEST STRATEGY

ลำดับ validation ของโครงการ:

```text
Unit / Regression
      ↓
Integration
      ↓
SITL
      ↓
Failure Injection
      ↓
Endurance
      ↓
Actual FC Bench (H02)
      ↓
Controlled Flight Matrix
      ↓
R01 Release Review
```

### Evidence สำคัญที่ผ่านแล้ว

- frontend regression checkpoints หลักระดับ 1000+ tests
- backend `go test ./...` ผ่านหลาย frozen checkpoints
- `go vet ./...` ผ่าน
- S11 failure families ผ่าน Expected Safe State
- S12 6h: `216,002 telemetry`, `723 RPC`, `0 RPC errors`, `0 stream errors`, `0 UI stalls`
- reconnect 15m: 15 cycles PASS
- client lifecycle 15m: 75 create/snapshot/close PASS
- Actual FC telemetry ~10 Hz
- Actual FC guarded mode ACK timing captured

---

# 🟠 H02 ACTUAL FC — ตอนนี้ทำถึงไหน

## 5.1 Parameter/config snapshot — ✅ PASS baseline

ค่าที่บันทึกแล้วรวม:

```text
SYSID_MYGCS=250
FS_GCS_ENABLE=1
ARMING_CHECK=1
BATT_FS_CRT_ACT=1
BATT_FS_LOW_ACT=2
FS_OPTIONS=1
FENCE_ENABLE=0
```

`FENCE_ENABLE=0` ยังเป็น field-policy review item ก่อน controlled real flight

## 5.2 Real telemetry timing — ✅ PASS

- ~10 Hz
- interval p95 ~101 ms
- source-age p95 ~1 ms
- link 99–100% ใน evidence run
- packet drop 0 permille ใน evidence run

## 5.3 COMMAND_ACK — ✅ PASS

Disarmed guarded test:

```text
LOITER → GUIDED ≈ 153.6 ms accepted
GUIDED → LOITER ≈ 105.3 ms accepted
```

## Operator-visible follow-up

มี operator-confirmed checks เพิ่ม:

```text
MODE GUIDED  PASS
ARM          PASS
DISARM       PASS
```

และ session bearer token ถูกออกผ่าน `swarmadmin` แล้ว โดย **ห้ามบันทึกค่า token จริงลงเอกสารหรือ Git**

## 5.4–5.9 — ⏳ ยังไม่ครบ

| H02 test | Status |
|---|---|
| 5.4 Core-owned single waypoint | ⏳ PENDING |
| 5.5 WAIT/HOLD | ⏳ PENDING |
| 5.6 Cancel | ⏳ PENDING |
| 5.7 Controlled Core/GCS loss | 🚫 MANDATORY BLOCKER |
| 5.8 Battery/link preemption | ⏳ PENDING |
| 5.9 Manual / STOP / E-STOP takeover | ⏳ PENDING |

---

# 🧯 TROUBLESHOOTING — อาการที่เจอบ่อย

## MODE GUIDED ขึ้น `_InactiveRpcError`

### สาเหตุที่เจอจริงในโปรเจกต์นี้

Core รันเป็น:

```text
profile=setup
```

ซึ่งเป็น telemetry-only จึงปฏิเสธ SetMode/Arm/mission mutation

### เช็ก

ดู `backend/logs/core-YYYYMMDD.log` หรือ `core-stdout.log` ว่า startup line เป็น:

```text
profile=hil
```

สำหรับ HIL bench ที่ตั้งใจจะใช้งาน

UI รุ่นปัจจุบันควรแสดง gRPC status อ่านง่าย เช่น:

```text
FAILED_PRECONDITION: setup profile is telemetry-only
```

แทนการโชว์ object `<_InactiveRpcError ...>` ยาว ๆ

---

## แจ้งเตือน `IP ซ้ำ`

`IP ซ้ำ` ใน Cockpit **ไม่ได้แปลว่า LAN มีสองเครื่องใช้ IP เดียวกันเสมอ**

ระบบมี duplicate guard ภายใน session เพื่อกันไม่ให้ Drone 2 ลำ connect endpoint เดียวกันพร้อมกัน

เช็กแยกเป็น 2 ชั้น:

### Network layer

- PC Wi-Fi IP ปัจจุบันที่ตรวจใน H02 session: `192.168.9.124`
- FC endpoint: `192.168.9.184:5760`
- ping ต้องตอบ
- TCP 5760 ต้องเปิด
- ARP ควรเห็นอุปกรณ์ปลายทางเดียว

### Cockpit session layer

ถ้า connect attempt ก่อนหน้าจอง endpoint ไว้ใน memory แต่ telemetry ยังไม่มา การกด Connect ซ้ำอาจถูกมองว่า endpoint นี้ถูกใช้แล้วใน session

สิ่งที่ต้องดูคือ:

```text
[fleet] Drone 1 reader started (...)
```

ถ้าไม่มี reader started และไม่มี telemetry แต่ UI บอก IP ซ้ำ ให้ตรวจ session reservation/card state ก่อนเปลี่ยน IP ของ FC

SQLite ที่เก็บ endpoint เดิม:

```text
~/.swarmgod/fleet_ips.db
```

แถวใน SQLite เป็น saved endpoint ไม่ได้แปลว่า connection ยัง active อยู่

---

## HIL เปิดแล้ว แต่ไม่มี telemetry

อาการ:

- Core `profile=hil` ขึ้นแล้ว
- gRPC `50051` listen แล้ว
- แต่ telemetry probe ได้ 0 samples

เช็ก:

1. Drone 1 ถูก Connect ใน Cockpit หรือยัง
2. endpoint ถูกต้อง `192.168.9.184:5760`
3. Core log มี `Drone 1 reader started` หรือไม่
4. ping/TCP เปิดไม่ได้แปลว่า MAVLink telemetry ถูก consume แล้ว

---

## SYSTEM TEST ติด token

ถ้าขึ้นว่า real mode ต้องมี `SWARMGOD_TOKEN`:

1. Token ต้องเป็น session token จริงจาก `swarmadmin session new`
2. ตั้ง token ใน **PowerShell เดียวกับที่ใช้เปิด Cockpit/Core**
3. restart Cockpit หลังตั้ง environment
4. ห้ามส่ง token/password เข้า chat, log, source หรือ commit

`swarmadmin` password ขั้นต่ำปัจจุบัน: **8 ตัวอักษร**

---

## ARM ถูกปฏิเสธ

อ่าน reason จาก Core/FC ก่อน อย่ากดซ้ำแบบเดา สาเหตุอาจเป็น GPS/EKF/pre-arm/battery/profile/ownership

เคยเห็น actual FC status:

```text
PreArm: High GPS HDOP
```

---

## กดปุ่มไม่ติดทั้งหมด

ตรวจ `UI / REMOTE` ownership ถ้าอยู่ REMOTE UI flight command ถูกล็อกโดยตั้งใจ

---

# 🗂️ LOG / EVIDENCE MAP

| Path | ใช้ดูอะไร |
|---|---|
| `backend/logs/core-YYYYMMDD.log` | Core runtime log |
| `backend/logs/core-stdout.log` | stdout/stderr จาก launcher |
| `backend/logs/audit/` | command audit |
| `evidence/h02/` | Actual-FC evidence |
| `docs/H02_ACTUAL_FC_BENCH_PROGRESS_20260903.md` | H02 canonical progress |
| `docs/V3_MASTER_ROADMAP.md` | V3 canonical roadmap |
| `docs/V3_FULL_PRODUCTION_VALIDATION_PLAN.md` | validation order หลัง H02 |
| `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` | handoff/history |

---

# 🗺️ ROADMAP หลัง H02

```text
H02-A single + WAIT bench
        ↓
S09-A Multi GROUPED
        ↓
S09-B SEPARATE
        ↓
S09-C SWARM_LEADER
        ↓
S09-E Payload
        ↓
S09-D WAVE
        ↓
S09-F Return Policy live validation
        ↓
Full-system integration
        ↓
Multi-drone/full-system endurance
        ↓
Full hardware matrix
        ↓
Controlled flight matrix
        ↓
R01 Full Production Release Review
```

แต่ละ authority scope ต้องเปิด **ทีละ scope** และผ่าน SITL → failure → soak → hardware → controlled flight ของ scope นั้นก่อน promotion

---

# 📚 เอกสารที่ควรอ่านตามลำดับ

1. `docs/HELP.md` — หน้านี้: คู่มือใช้งาน + status map
2. `docs/V3_MASTER_ROADMAP.md` — สถานะ V3 canonical
3. `docs/V3_FULL_PRODUCTION_VALIDATION_PLAN.md` — ลำดับทดสอบ full production
4. `docs/H02_ACTUAL_FC_BENCH_PROGRESS_20260903.md` — actual FC progress
5. `docs/HARDWARE_BENCH_F9_GATE.md` — bench procedure/tooling
6. `docs/REAL_FLIGHT_CHECKLIST.md` — checklist ที่ใช้เมื่อ release gate อนุญาต
7. `docs/ARCHITECTURE.md` — architecture
8. `docs/SECURITY.md` — security/safety details

---

# ✅ สรุปสั้นที่สุด

```text
Software foundation        = แข็งแรง / ผ่าน regression มาก
S09 advanced authority     = PRE-FLIP / ยังไม่ live
S12 endurance current gate = PASS
Actual FC H02              = IN PROGRESS
H02 5.1–5.3               = PASS
H02 5.4–5.9               = PENDING
Controlled real flight     = LOCKED
Full production            = NOT RELEASED
```

**อย่าใช้จำนวน test หรือคำว่า “GUIDED/ARM ผ่าน” เป็นหลักฐานว่า production-ready — จุดตัดสินคือ evidence ladder และ release gate ของ V3**
