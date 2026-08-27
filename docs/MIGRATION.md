# SwarmGod — คู่มือย้ายโปรเจค / ติดตั้งเครื่องใหม่ (ละเอียด)

เอกสารนี้ใช้เมื่อ **ย้ายโปรเจคไปเครื่องใหม่** หรือ **ก๊อปโฟลเดอร์ไปที่อื่น**
อ่านจบแล้วจะรัน `START_SWARMGOD.bat` ได้เหมือนเดิม

---

## ⚠️ ทำก่อนย้าย (Pre-move checklist)

**1. เช็คว่ามีงานที่ยังไม่ commit ไหม — สำคัญที่สุด**

```bash
git status --short
```

ถ้ามีไฟล์ขึ้นมา แปลว่างานนั้น**อยู่แค่ในเครื่องนี้เครื่องเดียว**
- ย้ายด้วย **git clone / pull** → งานที่ยังไม่ commit **หายทั้งหมด**
- ย้ายด้วย **ก๊อปทั้งโฟลเดอร์** → ปลอดภัย (ได้ทั้งที่ commit แล้วและยังไม่ commit)

แนะนำให้ commit ก่อนเสมอ:
```bash
git add -A && git commit -m "wip: ก่อนย้ายเครื่อง"
```

> ยังไม่มี git remote ในโปรเจคนี้ (`git remote -v` ว่าง) — ต่อให้ commit แล้วก็ยังต้อง
> ก๊อปโฟลเดอร์/ใส่ USB อยู่ดี ถ้าจะ push ขึ้น GitHub ต้อง `git remote add origin <url>` ก่อน

**2. เลือกวิธีย้าย**

| วิธี | ได้อะไร | เหมาะกับ |
|------|---------|----------|
| **ก๊อปทั้งโฟลเดอร์** (USB / ไดรฟ์เน็ตเวิร์ก) | ทุกอย่างรวมงานที่ยังไม่ commit + certs เดิม | ย้ายเครื่องตัวเอง (แนะนำ) |
| **git clone/pull** | เฉพาะที่ commit แล้ว · ไม่มี certs · ไม่มี `~/.swarmgod/` | แชร์ให้คนอื่น / เครื่องใหม่สะอาด |

**3. ไฟล์ที่ไม่ต้องเอาไป** (สร้างใหม่ได้ / เป็นขยะ runtime)

| ไฟล์/โฟลเดอร์ | หมายเหตุ |
|---------------|----------|
| `backend/bin/*.exe` | launcher build ใหม่ให้เอง |
| `backend/logs/core-*.log` · `core-stdout.log` | log ของ core (สร้างใหม่ตอนรัน) |
| `backend/logs/audit/*.jsonl` | audit เก่า — **เอาไปด้วยถ้าต้องเก็บหลักฐานการบิน** |
| `backend/logs/swarmgod.db` | SQLite ของ core (สร้างใหม่ได้) |
| `__pycache__/` · `frontend/mission_log.csv` | ขยะ runtime |
| `certs/` | สร้างใหม่อัตโนมัติ — ดูข้อ 2 |

---

## 0. ภาพรวม — โปรเจคประกอบด้วยอะไร

```
SwarmGod/
├── START_SWARMGOD.bat     ← ดับเบิลคลิกเปิด (launcher)
├── backend/               🐹 Go core (MAVLink, gRPC, safety, swarm, failsafe)
│   ├── cmd/               swarmgod-core, gencerts, flyctl, swarmctl, telemctl, mavprobe
│   ├── internal/          mavlink, fleet, telemetry, command, swarm, safety, audit, events, api, config
│   ├── gen/               ✅ generated gRPC stubs (อยู่ใน git — ไม่ต้อง regen)
│   └── go.mod / go.sum
├── frontend/              🐍 Python cockpit (PyQt5)
│   ├── requirements.txt
│   └── swarmgod_gui/       launcher.py, app.py, core/, widgets/, assets/(map+leaflet), gen/
├── proto/                 📜 gRPC contract (source of truth)
├── certs/                 🔐 mTLS + signing key (⚠️ ไม่อยู่ใน git — สร้างใหม่)
├── docs/                  📖 ARCHITECTURE, SECURITY, RUNBOOK, MIGRATION (ไฟล์นี้)
└── scripts/               wsl_sitl.sh, wsl_sitl_multi.sh, gen-*.sh
```

**3 ส่วนที่ทำงานร่วมกัน:** SITL (WSL) → Go core (Windows) → Python cockpit (Windows)

---

## ⚡ ทางลัด — ดับเบิลคลิก `SETUP.bat`

บนเครื่องใหม่ ดับเบิลคลิก **`SETUP.bat`** แล้วเลือกโหมด แทนการทำเองทีละขั้น:

| โหมด | ทำอะไร |
|------|--------|
| **1** ตรวจอย่างเดียว | รายงานสถานะทุกอย่าง ไม่ติดตั้ง/ไม่แก้อะไรเลย — ใช้ดูว่าขาดอะไร |
| **2** ตรวจ + ติดตั้ง *(แนะนำ)* | ลง Go/Python/deps ที่ขาด · สร้าง certs · build core |
| **3** เหมือน 2 + protoc plugins | เพิ่มเครื่องมือ regen proto (เฉพาะคนที่จะแก้ `.proto`) |

สคริปต์ตรวจ 7 หมวด: Go · Python · Python packages · certs · build core ·
protoc plugins · WSL+ArduPilot · แล้วปิดท้ายด้วยการลอง `import swarmgod_gui.app` จริง

**คุณสมบัติ**
- **idempotent** — รันซ้ำกี่รอบก็ได้ ข้ามของที่มีอยู่แล้ว
- **ไม่แตะข้อมูลผู้ใช้** ใน `~/.swarmgod/` เลย
- **ไม่ลง WSL/ArduPilot ให้อัตโนมัติ** (หนัก ~1.5 GB + build 20 นาที + ต้องรีสตาร์ต)
  → บอกขั้นตอนให้แทน ทำเองตามข้อ 3
- จบด้วยรายการ "สิ่งที่ต้องทำต่อ" ถ้ามีอะไรค้าง

```powershell
# หรือรันตรงจาก terminal
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -CheckOnly
```

> ⚠️ ถ้าเครื่องยังไม่มี Go/Python สคริปต์จะลงผ่าน `winget` ให้ (ต้องมี winget)
> แต่**ต้องเปิด terminal ใหม่**หลังติดตั้งเพื่อให้ PATH อัปเดต แล้วรัน `SETUP.bat` อีกรอบ

---

## 1. สิ่งที่ต้องมีบนเครื่องใหม่ (Prerequisites)

| ต้องมี | เวอร์ชันที่ใช้ | ติดตั้ง |
|--------|----------------|---------|
| **Go** | 1.26.5 | `winget install GoLang.Go` |
| **Python** | 3.14 (หรือ 3.12+) | python.org / winget |
| **WSL2 + Ubuntu** | Ubuntu-22.04 | `wsl --install -d Ubuntu-22.04` |
| **ArduPilot (ใน WSL)** | build sitl แล้ว | ดูข้อ 3 |
| protoc (เฉพาะถ้าจะแก้ .proto) | 35.1 | `winget install Google.Protobuf` |

> gen stubs อยู่ใน git แล้ว → **ไม่ต้องมี protoc** ถ้าไม่แก้ไฟล์ `.proto`

---

## 2. อะไรถูก copy / อะไรต้องสร้างใหม่

| สิ่งของ | อยู่ใน git? | บนเครื่องใหม่ |
|---------|-----------|---------------|
| โค้ด Go/Python, proto, gen stubs, docs, scripts, assets | ✅ | มากับ repo |
| `certs/` (*.key *.crt mavlink_key) | ❌ ignore | **launcher สร้างให้อัตโนมัติ** (`go run ./cmd/gencerts`) |
| `backend/bin/*.exe` | ❌ ignore | **launcher build ให้อัตโนมัติ** (`go build`) |
| `logs/`, tile cache (`~/.swarmgod/`) | ❌ | สร้างตอนรัน |
| Python packages | ❌ | `pip install -r frontend/requirements.txt` |
| Go modules | ❌ (มี go.sum) | โหลดอัตโนมัติตอน build |

**สรุป:** ก๊อปโฟลเดอร์ (หรือ git clone) → ติดตั้ง prerequisites → `pip install` → รัน launcher (มันจัดการ certs+build ให้)

### 2.1 ⚠️ ข้อมูลที่ "ไม่ได้อยู่ในโฟลเดอร์โปรเจค" — ไม่ตามไปเครื่องใหม่

ข้อมูลพวกนี้อยู่ที่ `~/.swarmgod/` (คือ `C:\Users\<ชื่อ>\.swarmgod\`) **นอกโปรเจค**
ก๊อปโฟลเดอร์โปรเจคอย่างเดียวจะไม่ได้ไปด้วย:

| ไฟล์ | คืออะไร | ถ้าไม่เอาไป |
|------|---------|-------------|
| `fleet_ips.db` | **IP โดรนทุกลำที่เคยเพิ่มไว้** | ต้องแอดโดรนใหม่ทั้งหมด (หรือกด SCAN หาใหม่) |
| `cockpit_settings.json` | ขนาดฟอนต์, RTL alt, endpoint, ค่าที่เคย SAVE | กลับไปใช้ค่า default ทั้งหมด |
| `tiles/` | แผนที่ offline ที่ cache ไว้ (~13 MB) | ครั้งแรกบนเครื่องใหม่ต้องมีเน็ตโหลด tiles ใหม่ |
| `logos/` | **รูปไอคอนหน่วยที่อัปโหลดเองบน top bar** (สูงสุด 5 รูป) | กลับไปใช้โลโก้ default ที่มากับโปรเจค |

**อยากเอาไปด้วย** → ก๊อปทั้งโฟลเดอร์ `~/.swarmgod/` ไปวางที่ path เดียวกันบนเครื่องใหม่
(ปิดแอปก่อนก๊อป — `fleet_ips.db` เป็น SQLite มีไฟล์ `-wal`/`-shm` คู่กัน ต้องเอาไปครบทั้งชุด)

> **หมายเหตุ**: `cockpit_settings.json` เก่าจะ**ทับค่า default ใหม่**เสมอ เช่นถ้าเคยเซฟฟอนต์ไว้
> 120% ไฟล์นั้นจะบังคับ 120% แม้โค้ดจะตั้ง default ไว้ 130% แล้ว — ถ้าอยากได้ค่า default ใหม่
> ล้วน ๆ ก็ **ไม่ต้อง**ก๊อปไฟล์นี้ไป

---

## 3. ติดตั้ง ArduPilot ใน WSL (ครั้งเดียว)

**วิธีที่แนะนำ** — ดับเบิลคลิก **`BUILD_SITL.bat`** ที่รากโปรเจค
(เรียก `scripts/wsl_build_sitl.sh` ให้: ลง prerequisites → clone/sync source → `waf configure` → `waf copter` → ตรวจผล)
ใช้เวลา ~15-30 นาที และถามรหัสผ่าน sudo ของ Linux 1 ครั้ง

**ทำเองทีละขั้น** (ถ้าอยากคุมเอง):

```bash
# ใน WSL Ubuntu
sudo apt update && sudo apt install -y build-essential ccache g++ gawk make wget \
  pkg-config git rsync python3-dev python3-setuptools python3-numpy \
  python3-pexpect python3-empy python3-future python3-lxml python3-serial
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git ~/ardupilot
cd ~/ardupilot
./waf configure --board sitl
./waf copter                 # build ครั้งแรก (~10-25 นาที)
# ทดสอบ:
Tools/autotest/sim_vehicle.py -v ArduCopter --no-mavproxy   # ควรเปิด TCP 5760
```

> ที่ไม่ใช้ `Tools/environment_install/install-prereqs-ubuntu.sh` เพราะมันลง MAVProxy/wxPython/OpenCV
> ที่ launcher ไม่ได้ใช้ (`--no-mavproxy`) และบน Ubuntu 24.04 มันสร้าง venv แล้วเขียนลง `~/.profile`
> ซึ่ง launcher (`bash -lc`) อาจไม่ได้โหลด → build ผ่านแต่ launcher ยังหา module ไม่เจอ

> ถ้า ArduPilot อยู่ path อื่น (ไม่ใช่ `~/ardupilot`) → ตั้ง env `SWARMGOD_AP_DIR` (ดูข้อ 5)

---

## 4. ติดตั้งฝั่ง Windows

```powershell
# 1) Go + Python + protoc(ถ้าต้อง)
winget install GoLang.Go
# (ติดตั้ง Python 3 จาก python.org ถ้ายังไม่มี)

# 2) Python deps
cd <SwarmGod>\frontend
pip install -r requirements.txt

# 3) (ครั้งแรก/ถ้าไม่มี gen) protoc plugins — เฉพาะถ้าจะ regen proto
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
```

> **ไม่ต้องลง protoc แยก** — `grpcio-tools` (มากับ `requirements.txt`) มี protoc ในตัวแล้ว
> ใช้คู่กับ plugin ของ Go ได้เลย · คำสั่ง regen ที่ใช้จริง:
>
> ```bash
> export PATH="$HOME/go/bin:$PATH"
> python -m grpc_tools.protoc -I proto \
>   --plugin=protoc-gen-go="$HOME/go/bin/protoc-gen-go.exe" \
>   --plugin=protoc-gen-go-grpc="$HOME/go/bin/protoc-gen-go-grpc.exe" \
>   --go_out=backend/gen --go_opt=paths=source_relative \
>   --go-grpc_out=backend/gen --go-grpc_opt=paths=source_relative \
>   proto/swarmgod/v1/*.proto
> python -m grpc_tools.protoc -I proto \
>   --python_out=frontend/swarmgod_gui/gen \
>   --grpc_python_out=frontend/swarmgod_gui/gen proto/swarmgod/v1/*.proto
> ```

จากนั้น **ดับเบิลคลิก `START_SWARMGOD.bat`** — launcher จะ:
สร้าง certs → build core → เปิด core → เปิด SITL → เปิด cockpit (พร้อม checklist)

---

## 5. ⚙️ ค่าที่ผูกกับเครื่อง — แก้ถ้าต่างจากเดิม (สำคัญตอนย้าย)

launcher อ่านค่าเหล่านี้จาก **environment variable** (ตั้งก่อนเปิด หรือใส่ใน .bat):

| env | ค่า default | ความหมาย |
|-----|-------------|----------|
| `SWARMGOD_WSL_DISTRO` | `Ubuntu` | ชื่อ distro WSL (เช็คด้วย `wsl -l -q`) — **ถ้าเครื่องใหม่ติดตั้งเป็น `Ubuntu-22.04` ต้องตั้ง env ตัวนี้** |
| `SWARMGOD_GO_BIN` | `C:\Program Files\Go\bin` | path ของ Go (ถ้า Go ไม่อยู่ใน PATH) |
| `SWARMGOD_AP_DIR` | `~/ardupilot` | path ArduPilot ใน WSL |
| `SWARMGOD_HOME_LOC` | `14.9581695,102.0986187,0,0` | พิกัด home (lat,lon,alt,hdg) — เปลี่ยนเป็นพื้นที่ของคุณ |
| `SWARMGOD_PROFILE` | `sitl` | `sitl`, `hil` หรือ `production`; โหมดโดรนจริงใช้ `hil`/`production` เท่านั้น |
| `SWARMGOD_TOKEN` | ไม่มี | session token ที่ production ต้องใช้ |

**วิธีตั้ง** (แก้ `START_SWARMGOD.bat` เพิ่มบรรทัดก่อน start):
```bat
set SWARMGOD_WSL_DISTRO=Ubuntu
set SWARMGOD_AP_DIR=~/src/ardupilot
set SWARMGOD_HOME_LOC=13.7563,100.5018,0,0
set SWARMGOD_PROFILE=production
set SWARMGOD_TOKEN=<ค่าที่ swarmadmin แสดง>
```

ค่าอื่นๆ ของ **core** (ปรับ safety/failsafe) ก็ผ่าน env เช่นกัน:
`SWARMGOD_MAX_ALT` (120) · `SWARMGOD_MAX_RADIUS` (500) · `SWARMGOD_TELEMETRY_HZ` (10) ·
`SWARMGOD_MAVLINK_STRICT=1` (เปิด strict signing) — ตั้งก่อนรัน core

---

## 6. รันแบบ manual (ถ้าไม่ใช้ launcher)

```powershell
# ก) certs (ครั้งเดียว)
cd backend
go run ./cmd/gencerts -out ../certs

# ข) SITL (WSL) — 1 ลำ หรือหลายลำ
wsl -d Ubuntu-22.04 bash /mnt/c/…/SwarmGod/scripts/wsl_sitl.sh          # 1 ลำ
wsl -d Ubuntu-22.04 bash /mnt/c/…/SwarmGod/scripts/wsl_sitl_multi.sh    # 3 ลำ

# ค) core
cd backend; go run ./cmd/swarmgod-core

# ง) cockpit
cd frontend; python -m swarmgod_gui
```

---

## 7. ตรวจว่าพร้อม (verification)

```powershell
cd backend
go build ./...                 # ควรผ่าน
go test ./internal/safety/     # ควร PASS ทุกเคส
cd ..\frontend
python -c "import swarmgod_gui.app; print('ok')"
```

---

## 8. Troubleshooting (ปัญหาที่เจอบ่อย — บทเรียนจริง)

| อาการ | สาเหตุ / วิธีแก้ |
|-------|------------------|
| `go` ไม่เจอ | เปิด terminal ใหม่ (winget เพิ่ง add PATH) หรือใช้ full path `C:\Program Files\Go\bin\go.exe` |
| cockpit ต่อ core ไม่ได้ / TLS error | มี core เก่าค้าง port 50051 → `Get-Process swarmgod-core \| Stop-Process -Force` |
| TAKEOFF/arm ไม่ขึ้น ("Need Position Estimate") | SITL EKF ยังไม่พร้อม — รอ ~40-60s หลัง SITL boot (core retry arm 10 ครั้งให้แล้ว) |
| SITL ไม่เปิด / ตายทันที | ต้องใช้ `sim_vehicle.py` (ผ่าน launcher/scripts) **ห้าม**รัน `arducopter -S` ดิบ (lockstep พัง) |
| แผนที่ไม่ขึ้น (offline) | ครั้งแรกต้องมีเน็ตให้โหลด tiles; กด CACHE THIS AREA เก็บ offline ก่อน |
| grpcio/PyQt5 ติดตั้งไม่ได้ | ใช้ Python 3.12 ถ้า 3.14 ยังไม่มี wheel (เครื่องนี้ 3.14 มีครบ) |
| `pkill -f arducopter` ฆ่า shell ตัวเอง | ใช้ `pkill -x arducopter` (แมตช์ชื่อ process ตรงตัว) |
| WSL2 ต่อ localhost:5760 ไม่ได้ | SITL ต้อง bind 0.0.0.0 (sim_vehicle ทำให้); localhost forwarding เปิด default |

---

## 9. ⚠️ ก่อนใช้กับโดรนจริง
ดู `docs/RUNBOOK.md` หัวข้อ "Pre-flight โดรนจริง":
signing key ให้ตรง FC · FC-side failsafe params · geofence/alt limit ตามพื้นที่ · cache แผนที่ offline

---

## 10. อ้างอิงเพิ่มเติม
- `docs/ARCHITECTURE.md` — สถาปัตยกรรม 3-tier + mapping จาก GCS_1 + build phases
- `docs/SECURITY.md` — 6 ชั้นความปลอดภัย + threat model
- `docs/RUNBOOK.md` — วิธีรันละเอียด + บทเรียน + pre-flight
