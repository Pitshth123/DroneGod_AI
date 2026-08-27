# SwarmGod — Runbook (วิธีรัน + บทเรียนสำคัญ)

## ⚡ วิธีที่ง่ายที่สุด — ดับเบิลคลิก `START_SWARMGOD.bat`
Launcher จัดการให้ครบ (certs → build → core → SITL → cockpit) + checklist สด + ปุ่ม STOP ALL
เอาเครื่องหมาย "ใช้ SITL" ออก = ต่อโดรนจริง (ข้ามการเปิด SITL)

## ✈️ Pre-flight โดรนจริง (นอกเหนือจาก SITL)
1. **ตั้ง profile/home/session ก่อนเปิด launcher** (PowerShell ตัวอย่าง):
   `$env:SWARMGOD_PROFILE="production"`; `$env:SWARMGOD_HOME_LOC="13.7563,100.5018,0,0"`;
   `$env:SWARMGOD_TOKEN="<ค่าที่ swarmadmin แสดง>"`
   ออก token ด้วย `go run ./cmd/swarmadmin session new <ชื่อผู้ใช้>` จากโฟลเดอร์ `backend`
2. **certs สำหรับ production**: เตรียม `ca.crt`, server/client cert+key และ `mavlink_key` ใน `certs/` เอง
   Launcher จะไม่สร้าง secret ชุดใหม่เงียบ ๆ ในโหมดโดรนจริง
3. **MAVLink signing key ให้ตรงกับ FC**: ตั้ง shared key เดียวกับ `certs/mavlink_key` ที่ FC
   (Mission Planner/MAVProxy: SETUP_SIGNING); production เปิด strict ให้เองและห้ามปิด signing
4. **FC-side failsafe params** (ตั้งที่ FC — เป็น backstop เมื่อ core ดับ):
   `FS_THR_ENABLE`, `FS_GCS_ENABLE=1`, `BATT_LOW_VOLT`/`BATT_CRT_VOLT`, `FS_OPTIONS`
5. **geofence/alt limit ให้เหมาะกับพื้นที่**: cockpit → DRAW FENCE วาดรั้วรอบพื้นที่ปฏิบัติงาน → SET;
   ปรับ `SWARMGOD_MAX_ALT` / `SWARMGOD_MAX_RADIUS` (env ของ core) ให้ตรงข้อกำหนดพื้นที่
6. ทดสอบ props-off → tethered hover → E-STOP/RTL/LAND ทีละลำ ก่อนทดสอบหลายลำ
7. cache แผนที่ offline ล่วงหน้า (ปุ่ม CACHE THIS AREA) ถ้าสนามไม่มีเน็ต


## รัน Phase 1 (Go core + SITL) — ครบทั้ง chain

### 1) ติดตั้ง toolchain (ครั้งเดียว)
```bash
winget install GoLang.Go Google.Protobuf
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
# PATH: C:\Program Files\Go\bin  และ  %USERPROFILE%\go\bin  และ protoc จาก winget
```

### 2) generate stubs + build
```powershell
cd backend
# (proto stubs อยู่ใน backend/gen แล้ว — regenerate ด้วย protoc ถ้าแก้ .proto)
go build ./...
go test ./internal/safety/      # 6/6 pass
```

### 3) รัน SITL ใน WSL — ⚠️ ต้องใช้ sim_vehicle.py (สำคัญมาก)
```bash
cd ~/ardupilot
Tools/autotest/sim_vehicle.py -v ArduCopter -I0 --no-rebuild --no-mavproxy \
    --custom-location=14.9581695,102.0986187,0,0
```
SITL เปิด TCP `127.0.0.1:5760` (เข้าถึงจาก Windows ได้ผ่าน WSL2 localhost forwarding)

### 4) รัน core + client
```powershell
cd backend
go run ./cmd/swarmgod-core                       # gRPC :50051
go run ./cmd/telemctl -connect 127.0.0.1:5760    # Connect + stream telemetry
```

### 5) รัน Python cockpit (Phase 2)
```powershell
cd frontend
pip install -r requirements.txt                  # ครั้งเดียว (Python 3.14 มี wheel ครบ)
# core ต้องรันอยู่ (ข้อ 4) + SITL (ข้อ 3)
python -m swarmgod_gui                            # กด + CONNECT หรือ:
# $env:SWARMGOD_AUTOCONNECT="127.0.0.1:5760"; python -m swarmgod_gui   # auto-connect
```
เห็นการ์ด UAV_1 อัปเดตสด (READY/STABILIZE/SAT/BAT) + telemetry console

> Python stubs สร้างด้วย: `python -m grpc_tools.protoc -I ../proto --python_out=swarmgod_gui/gen --grpc_python_out=swarmgod_gui/gen ../proto/swarmgod/v1/*.proto`

### 6) ทดสอบคำสั่งบิน (Phase 3)
```powershell
# core + SITL รันอยู่ แล้ว:
cd backend
go run ./cmd/flyctl -core 127.0.0.1:50051 -sitl 127.0.0.1:5760
# connect -> รอ GPS -> takeoff 20m (ไต่จริง) -> goto -> safety reject alt 250m -> RTL
```
หรือใน cockpit: กด **+ CONNECT** → **▲ TAKEOFF** (ตั้ง ALT) → เห็นการ์ดเป็น FLYING + alt ไต่
audit log: `backend/logs/audit/audit-YYYY-MM-DD.jsonl` (ทุกคำสั่ง + safety reject)

> ⚠️ arm อาจถูก FC ปฏิเสธช่วงแรก ("Need Position Estimate" — EKF ยัง converge ไม่เสร็จ ~40-60s หลัง SITL boot)
> Takeoff retry arm 10 ครั้ง (×2s). ทดสอบ takeoff ให้ warm SITL อย่างน้อย ~60s ก่อน

### 7) แผนที่ offline (Phase 4)
- cockpit มีแผนที่ Leaflet + ภาพดาวเทียมจริง (Esri) — หมุดโดรนวิ่งตาม GPS + trail + คลิก=GOTO
- **tile cache** อัตโนมัติ: ดูออนไลน์ครั้งเดียว → tiles เซฟที่ `~/.swarmgod/tiles/` → ใช้ offline ได้
- ปุ่ม **⬇ CACHE THIS AREA**: prefetch พื้นที่ปัจจุบัน (zoom ปัจจุบัน..+2) ล่วงหน้าก่อนออกสนาม
- offline: ไม่มีเน็ต → เสิร์ฟจาก cache; tile ที่ไม่มีใน cache = เทา (marker/trail ยังทำงาน)

### 8) Swarm (Phase 5) — จัดขบวน + ทดสอบหลายลำ
**รูปแบบขบวน (cockpit → dropdown SWARM):**
`WEDGE ลิ่ม · LINE หน้ากระดาน · COLUMN แถวตอน · DIAMOND เพชร · ECHELON ทแยง`
- ตั้ง SEP (ระยะห่าง) → กด FORM UP → ลูกจัดตำแหน่งตามรูปที่เลือก (offset หมุนตาม heading ตัวแม่)
- **ความปลอดภัย:** core คำนวณระยะห่างจริง**ทุกคู่**ในขบวน (รวมตัวแม่) — ถ้ามีคู่ใกล้กว่า
  MinSeparation (default 5m) → **ปฏิเสธ + แถบเตือนเหลือง** ("formation ปฏิเสธ: ...") ไม่ให้ตั้งค่า
- เปลี่ยนรูป/ระยะระหว่างบินได้ (กด FORM UP ใหม่ → ขบวนจัดใหม่สด)


```bash
# WSL: 3 SITL copters (ports 5760/5770/5780)
bash scripts/wsl_sitl_multi.sh
```
```powershell
# หลัง warm SITL ~60s (EKF):
go run ./cmd/swarmctl     # connect3 -> takeoff -> FORM UP -> move leader -> failover
```
ใน cockpit: **▣ FORM UP** (ตั้ง SEP) → ฝูงเกาะฟอร์เมชัน; การ์ดตัวแม่โชว์ **★ LEADER** + วงแหวนทองบนแผนที่
- **failover**: ตัวแม่ (telemetry ขาด > LinkLostSec) → เลื่อนตัวถัดไปเป็นแม่อัตโนมัติ (sticky)
- **safety**: spacing ต้อง ≥ MinSeparation; ทุกจุดเป้า follower ผ่าน safety.CheckPoint (alt+radius+geofence)
- **SET ALTITUDE**: กรอกเลข → GO TO ALT (ทุกลำที่เลือกไปความสูงนั้น ณ ตำแหน่งเดิม)

### 9) Hardening / Security (Phase 6)
```powershell
cd backend
go run ./cmd/gencerts -out ../certs   # สร้าง CA/server/client cert + mavlink_key (ครั้งเดียว)
```
- มี cert → core เปิด **mTLS** อัตโนมัติ (client ต้องมี cert), cockpit/Go clients ต่อ mTLS เอง
- มี certs/mavlink_key → **MAVLink signing** (HMAC) เปิด — เซ็นคำสั่งที่ส่งออก
  (strict mode reject frame ที่ไม่เซ็น: `SWARMGOD_MAVLINK_STRICT=1` — ห้ามใช้กับ SITL ที่ไม่ตั้ง signing)
- **Failsafe** อัตโนมัติ: telemetry ขาด >3s = WARN, >10s = ALARM + RTL; battery <15% (armed) = RTL
- **Geofence**: cockpit กด DRAW FENCE → คลิกวางจุด ≥3 → SET → รั้วแดง + core reject goto/swarm นอกรั้ว
- **Alarm banner**: event WARN/ALARM เด้งแถบบนสุด + ลง console
- ⚠️ certs (*.key/*.crt/mavlink_key) อยู่ใน .gitignore — ไม่ commit (เป็น secret)
ผลที่ควรเห็น:
```
UAV_1 LINK_STATUS_READY mode=FLIGHT_MODE_STABILIZE armed=false bat=100% 12.60V sat=10 fix=6 pos=14.958170,102.098619
```

---

## ⚠️ บทเรียนสำคัญ (LESSONS LEARNED)

### 1. ต้อง launch SITL ผ่าน `sim_vehicle.py` — อย่ารัน binary ดิบ
รัน `./arducopter -S ...` ตรงๆ → **lockstep พัง, sim clock ค้างที่ 0.001s → ไม่ส่ง telemetry เลย**
(บางครั้ง SITL exit ทันทีที่ client MAVLink ต่อเข้า)
`sim_vehicle.py` ส่ง args ถูก (`--slave 0 --sim-address=127.0.0.1` ไม่มี `-S`) → telemetry ไหลปกติ

### 2. WSL2 networking
- SITL bind `0.0.0.0:5760` → Windows ต่อ `127.0.0.1:5760` ได้ (localhost forwarding)
- **อย่าใช้ Test-NetConnection กับ SERIAL0** — มัน connect แล้วตัด ทำให้ SITL (รับ TCP client เดียว) หลุด

### 3. WSL process lifecycle
- `pkill -f <name>` จะแมตช์ shell ตัวเองถ้า command line มีคำนั้น → ฆ่าตัวเอง (exit 9/15)
  ใช้ `pkill -x arducopter` (แมตช์ชื่อ process ตรงตัว) แทน
- background process ใน WSL ตายเมื่อ session ปิด — ให้ launch แบบ foreground แล้วถือ task ไว้

### 4. gomavlib ParseError ตอนต้น = ปกติ
SITL พิมพ์ banner text ("Init ArduCopter V4.8..." ฯลฯ) ผ่าน serial ก่อน MAVLink frame
→ gomavlib log "invalid magic byte" แล้วข้ามให้เอง ไม่ใช่บั๊ก

---

## Verified message coverage (จาก SITL จริง)
core parse ครบ: `HEARTBEAT · GLOBAL_POSITION_INT · SYS_STATUS · GPS_RAW_INT · ATTITUDE · VFR_HUD`
(SITL ส่ง ~28 ชนิด — ที่เหลือ เช่น BATTERY_STATUS, EKF_STATUS จะเพิ่ม parse ภายหลังตามต้องการ)
