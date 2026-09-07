# บันทึกการเพิ่มระบบย้ายคอมและยืนยันตัวตนอัตโนมัติ

วันที่จัดทำ: 6 กันยายน 2026

เอกสารนี้สรุปการเปลี่ยนแปลงที่เพิ่มขึ้นเพื่อให้ SwarmGod ย้ายไปใช้บนคอมพิวเตอร์
Windows เครื่องใหม่ได้ง่าย ผู้ใช้งานไม่ต้องสร้างบัญชีเอง ไม่ต้องจำรหัสผ่าน และไม่ต้อง
คัดลอก session token ไปใส่ใน environment ด้วยตนเอง

## วิธีใช้งานสำหรับผู้ใช้ทั่วไป

1. คัดลอกโฟลเดอร์ `DroneNew` ทั้งโฟลเดอร์ไปยังคอมเครื่องใหม่
2. ตรวจว่ามีโฟลเดอร์ `certs` และไฟล์ `ca.crt`, `server.crt`, `server.key`,
   `client.crt`, `client.key` และ `mavlink_key`
3. ดับเบิลคลิก `START_SWARMGOD_EASY.bat`
4. เลือกโหมดโดรนจริงหรือ SITL ใน Launcher แล้วกด **START**

ตัวเปิดจะตรวจเครื่อง สร้างบัญชีและ token ภายในเครื่อง ส่ง environment ที่จำเป็นให้
Launcher/Core/Cockpit และข้ามหน้ากรอกรหัสของ Launcher ให้อัตโนมัติ

## ไฟล์ใหม่ที่เพิ่ม

### `START_SWARMGOD_EASY.bat`

- เป็นไฟล์หลักที่แนะนำให้ผู้ใช้ดับเบิลคลิก
- เรียก Windows PowerShell ด้วย `ExecutionPolicy Bypass` เฉพาะ process นี้ จึงไม่ต้อง
  เปลี่ยน Execution Policy แบบถาวร
- ใช้ `%~dp0` หาโฟลเดอร์โครงการ จึงไม่ผูกกับชื่อผู้ใช้ ไดรฟ์ หรือพาธของคอมเดิม
- แสดงข้อผิดพลาดและหยุดรอเฉพาะเมื่อเริ่มระบบไม่สำเร็จ

### `scripts/Start-SwarmGodEasy.ps1`

- ตรวจ Go, Python และ Python packages ที่ Cockpit ต้องใช้
- เรียก `scripts/setup.ps1` เพื่อซ่อมหรือติดตั้ง dependency ที่ขาดในครั้งแรก
- ตรวจไฟล์ mTLS และ MAVLink signing key ก่อนเริ่มระบบ
- สร้างฐานข้อมูลประจำเครื่องที่ `%LOCALAPPDATA%\SwarmGod\data\swarmgod.db`
- เรียกตัวสร้างบัญชี/token อัตโนมัติ
- ตั้ง `SWARMGOD_DB`, `SWARMGOD_TOKEN` และ `SWARMGOD_AUTHED` ให้ process ลูก
- เปิด `START_SWARMGOD.bat` หลังตรวจทุกอย่างสำเร็จ
- รองรับ `-CheckOnly` สำหรับตรวจความพร้อมโดยไม่สร้าง token และไม่เปิด GUI

### `scripts/Set-SwarmGodToken.ps1`

- สร้างรหัส `admin` แบบสุ่มจาก cryptographic random 32 bytes
- สร้างบัญชี `admin` role `operator` เมื่อฐานข้อมูลยังไม่มีบัญชี
- หากบัญชีมีอยู่แต่ credential ที่บันทึกไว้หายหรือใช้ไม่ได้ จะตั้งรหัสใหม่ให้อัตโนมัติ
- ออก session token ใหม่ อายุ 12 ชั่วโมงทุกครั้งที่เรียก
- ตรวจรูปแบบ token ก่อนส่งให้ระบบ
- ตั้ง `SWARMGOD_TOKEN` ใน PowerShell process ปัจจุบันเพื่อส่งต่อให้ Core/Cockpit
- ล้าง `SWARMGOD_NEW_PASSWORD` และตัวแปรรหัสออกเมื่อทำงานเสร็จ
- เก็บรหัสและสำเนา token ด้วย Windows DPAPI ใน
  `%LOCALAPPDATA%\SwarmGod\credentials\<database-path-hash>`
- แยก credential ตามพาธฐานข้อมูล ป้องกันการนำ credential ของฐานข้อมูลคนละชุดมาใช้ปนกัน

### `OPEN_SWARMGOD_TOKEN_SHELL.cmd`

- เปิด PowerShell สำหรับงานตรวจสอบหรือดูแลระบบแบบ manual
- สร้าง/กู้บัญชีและ token ด้วยสคริปต์เดียวกับ Easy Start
- ใช้พาธสัมพัทธ์และแก้ปัญหา Execution Policy เฉพาะหน้าต่างนั้น

### `START_SWARMGOD_AUTHED.cmd`

- คงชื่อไฟล์เดิมไว้เพื่อความเข้ากันได้
- ส่งต่อการทำงานไปยัง `START_SWARMGOD_EASY.bat` เพื่อให้มีเส้นทางเริ่มระบบเพียงชุดเดียว

### `scripts/capture_hil_home.py`

- อ่าน Fleet Snapshot จาก Core ผ่าน gRPC
- เลือก telemetry ที่มีลายเซ็นยืนยันแล้วและมีอายุไม่เกิน 5 วินาที
- ยอมรับเฉพาะโดรนที่ DISARM, GPS fix ตั้งแต่ 3 ขึ้นไป และมีพิกัดไม่เป็นศูนย์
- ส่ง HOME ในรูปแบบ `latitude,longitude,0,0` ให้สคริปต์ HIL
- แสดงข้อความ gRPC แบบสั้นที่ผู้ควบคุมอ่านได้เมื่อ Core ติดต่อไม่ได้

### `scripts/Restart-SwarmGodHil.ps1`

- จับ HOME สดจากโดรนที่ผ่านเงื่อนไขความปลอดภัยก่อนหยุด Core เดิม
- ใช้ฐานข้อมูลและ token ประจำเครื่องชุดเดียวกับ Easy Start
- เก็บ HOME ที่ยืนยันแล้วไว้ที่ `%LOCALAPPDATA%\SwarmGod\data\hil-home.txt`
- ใช้ HOME สำรองได้ไม่เกิน 30 นาที และรองรับการย้าย cache เดิมจาก
  `backend\logs\hil-home.txt`
- ตั้ง profile เป็น `hil` พร้อม `SWARMGOD_HOME_LOC`
- หยุดเฉพาะ process ของ SwarmGod Launcher/Cockpit/Core แล้วเปิดชุดใหม่
- ใช้ `SWARMGOD_LAUNCHER_AUTOSTART=1` เพื่อเริ่ม HIL และเปิด Cockpit อัตโนมัติ

### `RESTART_SWARMGOD_HIL_AUTHED.cmd`

- เป็นตัวดับเบิลคลิกสำหรับเรียก HIL restart
- ใช้ `%~dp0` จึงทำงานได้หลังย้ายโฟลเดอร์หรือเปลี่ยนชื่อบัญชี Windows
- ใช้ Execution Policy แบบชั่วคราวเหมือน Easy Start

### `docs/MOVE_TO_NEW_PC.md`

- เพิ่มคู่มือย้ายโฟลเดอร์ไปเครื่องใหม่
- อธิบายไฟล์ certificate/private key ที่ต้องนำไปด้วย
- อธิบายขั้นตอนเปิดครั้งแรก การใช้โดรนจริง และข้อจำกัดของ SITL

## Backend ที่แก้ไข

### `backend/internal/store/users.go`

- เพิ่ม `ResetUserPassword(username, password)`
- บังคับรหัสใหม่ยาวอย่างน้อย 8 ตัว
- hash รหัสใหม่ด้วยกลไกเดียวกับบัญชีปกติ
- เปลี่ยนรหัสและ revoke session เดิมทั้งหมดภายใน database transaction เดียว
- คืนข้อผิดพลาดเมื่อไม่พบบัญชี เพื่อไม่สร้างบัญชีผิดตัวโดยเงียบ ๆ

### `backend/cmd/swarmadmin/main.go`

- เพิ่มคำสั่ง `user reset-password <username>`
- อ่านรหัสจาก `SWARMGOD_NEW_PASSWORD` ได้ จึงรองรับ automation โดยไม่แสดงรหัสบนจอ
- แจ้งชัดเจนว่า session เดิมถูก revoke หลัง reset สำเร็จ
- เพิ่มคำสั่งใหม่ในหน้า usage ของ `swarmadmin`

### `backend/internal/store/store_test.go`

- เพิ่มการทดสอบ reset password
- ยืนยันว่ารหัสเก่าใช้ไม่ได้ รหัสใหม่ใช้ได้ และ session เก่าถูก revoke

## Frontend และ Launcher ที่แก้ไข

### `frontend/swarmgod_gui/core/grpc_client.py`

- เพิ่ม `format_rpc_error()` เพื่อแปลง `_InactiveRpcError` เป็นข้อความสั้น เช่น
  `UNAVAILABLE: ...` หรือ `UNAUTHENTICATED: ...`

### `frontend/swarmgod_gui/app.py`

- ใช้ข้อความ gRPC แบบอ่านง่ายในคำสั่งด่วน คำสั่งบน drone card และคำสั่ง leader mode
- ผู้ใช้จึงไม่เห็น Python exception dump ยาวเมื่อคำสั่ง MODE GUIDED หรือ RPC อื่นล้มเหลว

### `frontend/swarmgod_gui/core/preflight.py`

- สถานะ ARMED ยังคงเป็น `FAIL` และบล็อกการทดสอบ
- กรณีโดรน DISARM แต่ `alt_rel` สูงจาก barometer/HOME drift เปลี่ยนเป็น `WARN`
- ป้องกัน false failure ว่าโดรนลอยอยู่ ทั้งที่วางอยู่บนพื้นและ FC รายงาน DISARM

### `frontend/tests/test_preflight.py`

- เพิ่ม/ปรับการทดสอบให้ยืนยันว่า ARMED บล็อก แต่ altitude drift ตอน DISARM ไม่บล็อก

### `frontend/swarmgod_gui/launcher.py`

- อ่าน `SWARMGOD_PROFILE` เพื่อเลือกการ์ด SITL หรือโดรนจริงให้ตรงกับ profile
- เพิ่ม `SWARMGOD_LAUNCHER_AUTOSTART` สำหรับเริ่มระบบจริงอัตโนมัติ
- คง `SWARMGOD_LAUNCHER_AUTO` ไว้สำหรับโหมด screenshot/test ที่ปิดตัวเองหลังทำงาน
  เพื่อไม่ให้ HIL autostart ถูกปิดตามโหมดทดสอบ

## เอกสารและตัวติดตั้งที่แก้ไข

### `README.md`

- เปลี่ยน Quick Start ให้แนะนำ `START_SWARMGOD_EASY.bat`
- เพิ่มลิงก์ไปคู่มือย้ายคอมและเอกสารบันทึกการเปลี่ยนแปลงนี้

### `scripts/setup.ps1`

- เปลี่ยนข้อความหลังติดตั้งสำเร็จให้แนะนำ `START_SWARMGOD_EASY.bat`

## ตำแหน่งข้อมูลประจำเครื่อง

| ข้อมูล | ตำแหน่ง |
|---|---|
| ฐานข้อมูลผู้ใช้/session | `%LOCALAPPDATA%\SwarmGod\data\swarmgod.db` |
| HOME ที่ยืนยันแล้ว | `%LOCALAPPDATA%\SwarmGod\data\hil-home.txt` |
| รหัส admin ที่เข้ารหัส DPAPI | `%LOCALAPPDATA%\SwarmGod\credentials\<hash>\admin.password.dpapi` |
| token ที่เข้ารหัส DPAPI | `%LOCALAPPDATA%\SwarmGod\credentials\<hash>\session.token.dpapi` |

การใช้ฐานข้อมูลใน `%LOCALAPPDATA%` ทำให้คอมแต่ละเครื่องมีบัญชีและ session แยกกัน
แม้โฟลเดอร์โครงการจะอยู่ใน OneDrive เครื่องหนึ่งจึงไม่ revoke session ของอีกเครื่องโดยไม่ตั้งใจ

## ลำดับการใช้งานกับ Flight Controller จริงในสถานที่ใหม่

1. เปิด `START_SWARMGOD_EASY.bat`
2. เลือก **โดรนจริง** แล้วกด **START**
3. Core จะใช้ profile `setup` แบบ telemetry-only หากยังไม่มี HOME
4. ต่อ Flight Controller แล้วกด Connect ขณะโดรน DISARM และอยู่บนพื้น
5. รอ GPS fix และ telemetry verified
6. ดับเบิลคลิก `RESTART_SWARMGOD_HIL_AUTHED.cmd`
7. ระบบจะจับ HOME สด ออก token ใหม่ เปิด profile `hil` และเปิด Cockpit ใหม่อัตโนมัติ

## สิ่งที่ยังต้องนำไปยังคอมเครื่องใหม่

- ต้องคัดลอกโฟลเดอร์ `certs` ที่ครบจากเครื่องที่ได้รับอนุญาตเมื่อใช้ Flight Controller จริง
- `git clone` อย่างเดียวไม่รวม certificate, private key และ MAVLink signing key ที่อยู่ใน `.gitignore`
- การติดตั้ง Go/Python ครั้งแรกผ่าน `winget` ต้องใช้อินเทอร์เน็ต และ Windows อาจขอสิทธิ์ติดตั้ง
- SITL ต้องมี WSL และ ArduPilot ที่ build แล้ว; งาน Flight Controller จริงไม่ต้องใช้ SITL

## ผลการตรวจสอบ

- PowerShell parser ผ่านสำหรับ `Start-SwarmGodEasy.ps1`, `Set-SwarmGodToken.ps1`,
  `Restart-SwarmGodHil.ps1` และ `setup.ps1`
- `python -m py_compile scripts/capture_hil_home.py` ผ่าน
- `Start-SwarmGodEasy.ps1 -CheckOnly` รายงานว่าเครื่องพร้อมใช้งาน
- `go test ./internal/store ./cmd/swarmadmin` ผ่าน
- frontend test ที่เกี่ยวข้องผ่าน 64 รายการ
- `git diff --check` ผ่าน
- ไม่พบพาธ `C:\Users\staff` ฝังอยู่ในไฟล์เริ่มระบบที่ใช้ย้ายเครื่อง
