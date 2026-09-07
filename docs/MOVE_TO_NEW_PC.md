# 🖥️ ย้าย SwarmGod ไปคอมเครื่องใหม่ — คู่มือแบบทีละขั้น

เอกสารนี้เป็น **ลำดับใช้งานที่แนะนำสำหรับเครื่องใหม่** โดยเฉพาะกรณีจะต่อ Flight Controller จริง
และต้องการให้ระบบสร้างบัญชี/token ให้อัตโนมัติโดยไม่ต้องคัดลอก secret ด้วยมือ

รายละเอียดทางเทคนิคและรายการไฟล์ที่เพิ่มอยู่ใน
[`AUTOMATIC_AUTH_PC_MIGRATION_CHANGELOG.md`](AUTOMATIC_AUTH_PC_MIGRATION_CHANGELOG.md)

---

## ✅ สรุปสั้นที่สุด: ต้องรันอะไรบ้าง

สำหรับ **เครื่องใหม่ + Flight Controller จริง** ให้จำลำดับนี้:

```text
Git clone / คัดลอก DroneNew
        ↓
คัดลอก certs ของระบบจริงจากเครื่องที่ได้รับอนุญาต
        ↓
SETUP.bat  (ครั้งแรก แนะนำเลือก [2])
        ↓
START_SWARMGOD_EASY.bat
        ↓
Launcher: เลือก "โดรนจริง" → START → Connect FC
        ↓
รอ DISARM + telemetry สด + GPS fix + พิกัดจริง
        ↓
RESTART_SWARMGOD_HIL_AUTHED.cmd
        ↓
ระบบจับ HOME สด + ออก token ใหม่ + เปิด profile=hil
        ↓
SYSTEM TEST
```

> **ไฟล์หลักที่ต้องจำมี 2 ตัว**
>
> 1. `START_SWARMGOD_EASY.bat` — เปิดระบบปกติและสร้าง token อัตโนมัติ
> 2. `RESTART_SWARMGOD_HIL_AUTHED.cmd` — ใช้หลัง Connect FC และได้ HOME สดแล้ว เพื่อเข้าสู่ authenticated HIL

`OPEN_SWARMGOD_TOKEN_SHELL.cmd` **ไม่ใช่ขั้นตอนหลัก** และไม่ต้องรันก่อนสองไฟล์ข้างบน

---

## 1) นำโปรเจกต์ไปเครื่องใหม่

Clone จาก GitHub หรือคัดลอกโฟลเดอร์โปรเจกต์ทั้งชุดก็ได้

Repository หลักของผู้ใช้:

```text
Pitshth123/DroneGod_AI
```

หลัง clone จะได้ source code, scripts และ documentation แต่ **จะไม่ได้ private certificate/key**
เพราะไฟล์ลับถูก `.gitignore` ไว้โดยตั้งใจ

---

## 2) สำคัญมาก: นำ `certs` ของระบบจริงมาด้วย

ก่อนเปิดระบบกับ Flight Controller จริง ให้คัดลอกโฟลเดอร์ `certs` จากเครื่องที่ได้รับอนุญาต
มาไว้ใน root ของโปรเจกต์ โดยอย่างน้อยต้องมี:

```text
certs\
  ca.crt
  server.crt
  server.key
  client.crt
  client.key
  mavlink_key
```

### ทำไม GitHub ไม่มีไฟล์พวกนี้

เพราะ certificate/private key และ MAVLink signing key เป็น secret ของระบบ จึงไม่ควร push ขึ้น Git

### ข้อควรระวัง

ถ้าจะใช้ FC จริง ให้ **นำ certs เดิมมาก่อนรัน setup** เพื่อไม่ให้เผลอสร้าง development certs ชุดใหม่
แล้วคิดว่าเป็นชุดที่ใช้กับระบบจริงเดิมได้

---

## 3) เครื่องใหม่ครั้งแรก: รัน `SETUP.bat`

ดับเบิลคลิก:

```text
SETUP.bat
```

แนะนำเลือก:

```text
[2] ตรวจ + ติดตั้งสิ่งที่ขาด
```

ตัว setup จะตรวจ/จัดเตรียม:

- Go
- Python 3.10+
- Python packages ของ Cockpit
- Go Core binary
- certificate readiness
- WSL/SITL readiness (ถ้าจะใช้ simulation)

> สำหรับ Flight Controller จริง ไม่จำเป็นต้องติดตั้ง ArduPilot SITL

`START_SWARMGOD_EASY.bat` สามารถตรวจ dependency และเรียก setup อัตโนมัติได้ด้วย แต่เครื่องใหม่
แนะนำให้รัน `SETUP.bat` หนึ่งรอบเพื่อเห็นสถานะครบชัดเจน

---

## 4) เปิดระบบด้วย `START_SWARMGOD_EASY.bat`

ดับเบิลคลิก:

```text
START_SWARMGOD_EASY.bat
```

นี่คือ **ตัวเปิดหลักสำหรับการใช้งานทั่วไป**

ระบบจะทำงานอัตโนมัติประมาณนี้:

```text
ตรวจ Go / Python / packages / certs
        ↓
สร้างฐานข้อมูลเฉพาะเครื่อง
        ↓
สร้างหรือกู้ admin account
        ↓
สร้าง password แบบสุ่มด้วย cryptographic RNG
        ↓
เก็บ credential ด้วย Windows DPAPI
        ↓
ออก SWARMGOD_TOKEN อายุ 12 ชั่วโมง
        ↓
ส่ง SWARMGOD_DB + SWARMGOD_TOKEN ให้ process ลูก
        ↓
เปิด Launcher
```

### ผู้ใช้ต้องสร้าง token เองไหม?

**ไม่ต้อง**

เมื่อใช้ `START_SWARMGOD_EASY.bat` ระบบเรียก `scripts/Set-SwarmGodToken.ps1` ให้อัตโนมัติ
และออก session token ใหม่ให้เอง

ผู้ใช้ไม่ต้อง:

- จำ admin password
- copy token จากเครื่องเก่า
- paste token ลง environment
- เก็บ token ลง GitHub

---

## 5) Token และข้อมูลแต่ละเครื่องเก็บที่ไหน

SwarmGod แยกฐานข้อมูลและ credential ตามคอมเครื่องนั้น:

```text
%LOCALAPPDATA%\SwarmGod\
├─ data\
│  ├─ swarmgod.db
│  └─ hil-home.txt
└─ credentials\
   └─ <database-path-hash>\
      ├─ admin.password.dpapi
      └─ session.token.dpapi
```

- `swarmgod.db` = user/session database ของเครื่องนั้น
- `admin.password.dpapi` = admin credential ที่ Windows DPAPI เข้ารหัส
- `session.token.dpapi` = token cache ที่ DPAPI เข้ารหัส
- `hil-home.txt` = HOME ล่าสุดที่ผ่านการตรวจยืนยันสำหรับ HIL

จึงไม่ควร copy `%LOCALAPPDATA%\SwarmGod` ข้ามเครื่องเพื่อหวัง reuse secret เดิม
ให้เครื่องใหม่สร้างของตัวเองอัตโนมัติแทน

---

## 6) ต่อ Flight Controller ครั้งแรกในเครื่องใหม่

หลัง `START_SWARMGOD_EASY.bat` เปิด Launcher แล้ว:

1. เลือก **โดรนจริง**
2. กด **START**
3. Connect ไปยัง Flight Controller
4. ให้ aircraft อยู่บนพื้นและ **DISARM**
5. รอ telemetry สด
6. รอ GPS fix อย่างน้อยระดับที่ระบบยอมรับ
7. ตรวจว่าพิกัด latitude/longitude เป็นค่าจริง ไม่ใช่ 0,0

ช่วงนี้ setup/Core ใช้สำหรับอ่าน telemetry และหา HOME ที่ถูกต้องก่อน

> อย่าใช้ HOME จากสถานที่เก่าเพียงเพราะเคยใช้ได้ เครื่องใหม่ควรอ่าน HOME จาก telemetry ปัจจุบัน

---

## 7) เมื่อ telemetry พร้อมแล้ว: รัน `RESTART_SWARMGOD_HIL_AUTHED.cmd`

หลัง FC เชื่อมแล้วและยัง DISARM ให้ดับเบิลคลิก:

```text
RESTART_SWARMGOD_HIL_AUTHED.cmd
```

ไฟล์นี้จะเรียก `scripts/Restart-SwarmGodHil.ps1` และทำงานดังนี้:

1. อ่าน Fleet Snapshot จาก Core เดิม
2. เลือก telemetry ที่สดและ verified
3. ตรวจว่า aircraft **DISARM**
4. ตรวจ GPS fix และพิกัดจริง
5. สร้าง `SWARMGOD_HOME_LOC` จาก GPS สด
6. เก็บ HOME ที่ยืนยันแล้วไว้ใน `%LOCALAPPDATA%\SwarmGod\data\hil-home.txt`
7. สร้าง/กู้ local admin credential
8. **ออก token ใหม่อัตโนมัติ**
9. ตั้ง `SWARMGOD_PROFILE=hil`
10. ตั้ง `SWARMGOD_HOME_LOC`
11. หยุดเฉพาะ SwarmGod Launcher/Cockpit/Core ชุดเก่า
12. เปิด SwarmGod ชุดใหม่แบบ authenticated HIL
13. เปิด Cockpit อัตโนมัติ

ดังนั้น **ไม่ต้องเปิด `OPEN_SWARMGOD_TOKEN_SHELL.cmd` ก่อน**

### HOME cache

ถ้าการอ่าน HOME สดพลาดชั่วคราว ระบบยอมใช้ HOME ที่เพิ่ง verify ไว้ไม่นาน โดยกำหนดอายุสูงสุดประมาณ
30 นาที เพื่อป้องกันการนำ HOME จากสถานที่เก่ามาใช้โดยไม่ตั้งใจ

---

## 8) หลัง HIL เปิดแล้ว

ก่อนใช้งาน bench/actual-FC ต่อ ให้ตรวจหน้า Cockpit ว่า:

- Core connected
- profile = `hil`
- mTLS พร้อม
- token/auth พร้อม
- telemetry สด
- Drone ID ถูกต้อง
- aircraft ยัง DISARM ตามแผนทดสอบ

จากนั้นรัน **SYSTEM TEST** ตามขั้นตอน preflight ของโปรเจกต์

> การที่ HIL เปิดสำเร็จไม่ได้แปลว่า V3-H02 / real-flight release ผ่านครบแล้ว ต้องอ้างอิง Roadmap และ H02 gate ล่าสุดด้วย

---

## 9) `OPEN_SWARMGOD_TOKEN_SHELL.cmd` ใช้ตอนไหน

ไฟล์:

```text
OPEN_SWARMGOD_TOKEN_SHELL.cmd
```

เป็น **เครื่องมือ manual/admin/debug** ไม่ใช่ไฟล์สำหรับเปิดระบบตามปกติ

เมื่อรัน จะเปิด PowerShell แล้วเรียก:

```text
scripts\Set-SwarmGodToken.ps1
```

ใช้กรณีเช่น:

- ต้องการ PowerShell session ที่มี `SWARMGOD_TOKEN`
- ตรวจระบบ authentication ด้วยมือ
- ใช้คำสั่ง admin/debug
- แยกวิเคราะห์ปัญหา token/session

### ไม่ต้องทำแบบนี้ในการเปิดปกติ

```text
OPEN_SWARMGOD_TOKEN_SHELL.cmd
        ↓
START_SWARMGOD_EASY.bat
```

เพราะ Easy Start สร้าง token ให้เองอยู่แล้ว

และไม่ต้องทำ:

```text
OPEN_SWARMGOD_TOKEN_SHELL.cmd
        ↓
RESTART_SWARMGOD_HIL_AUTHED.cmd
```

เพราะ HIL restart ก็ออก token ใหม่ให้เองเช่นกัน

---

## 10) `START_SWARMGOD_AUTHED.cmd` คืออะไร

ไฟล์นี้เก็บไว้เพื่อ compatibility กับ workflow เก่า:

```text
START_SWARMGOD_AUTHED.cmd
```

ปัจจุบันมันส่งต่อไปยัง:

```text
START_SWARMGOD_EASY.bat
```

ดังนั้นผู้ใช้ใหม่ควรเรียก `START_SWARMGOD_EASY.bat` ตรง ๆ เพื่อไม่ให้สับสน

---

## 11) ตารางสรุปไฟล์ที่เกี่ยวข้อง

| ไฟล์ | ใช้เมื่อไร | สร้าง token อัตโนมัติ | ผู้ใช้ทั่วไปต้องรัน? |
|---|---|---:|---:|
| `SETUP.bat` | เครื่องใหม่ / ซ่อม dependency | ไม่ใช่หน้าที่หลัก | ครั้งแรกแนะนำ |
| `START_SWARMGOD_EASY.bat` | เปิดระบบปกติ | ✅ | ✅ |
| `RESTART_SWARMGOD_HIL_AUTHED.cmd` | หลัง FC connected + telemetry/GPS พร้อม เพื่อเข้า HIL | ✅ | ✅ เมื่อใช้ actual FC/HIL |
| `OPEN_SWARMGOD_TOKEN_SHELL.cmd` | manual/admin/debug | ✅ | ❌ ปกติไม่ต้อง |
| `START_SWARMGOD_AUTHED.cmd` | compatibility กับ workflow เก่า | ✅ ผ่าน Easy Start | ❌ ใช้ Easy Start แทน |
| `START_SWARMGOD.bat` | low-level/legacy launcher path | ไม่รับประกัน auto-auth แบบ Easy Start | ❌ สำหรับ workflow ใหม่ |

---

## 12) ถ้าเครื่องใหม่เปิดไม่ได้ ให้เช็กตามลำดับนี้

### A. แจ้งว่า certs หาย

ตรวจ:

```text
certs\ca.crt
certs\server.crt
certs\server.key
certs\client.crt
certs\client.key
certs\mavlink_key
```

ถ้าใช้ FC จริง ให้เอาชุดที่ได้รับอนุญาตจากเครื่องเดิมมา ไม่ควรส่ง private key ผ่านช่องทางสาธารณะ

### B. Go/Python/package ขาด

รัน:

```text
SETUP.bat
```

เลือก `[2]`

### C. Token/Auth fail

เริ่มใหม่ด้วย:

```text
START_SWARMGOD_EASY.bat
```

ตัวระบบจะพยายามกู้ credential/reset local admin password และออก session ใหม่ให้อัตโนมัติ

`OPEN_SWARMGOD_TOKEN_SHELL.cmd` ใช้เมื่อจำเป็นต้อง debug ด้วยมือเท่านั้น

### D. `RESTART_SWARMGOD_HIL_AUTHED.cmd` แจ้งหา HOME ไม่ได้

ให้กลับไปที่ setup/telemetry path แล้วตรวจว่า:

- FC connected
- telemetry สด
- aircraft DISARM
- GPS fix พร้อม
- พิกัดไม่ใช่ 0,0

จากนั้นรัน HIL restart ใหม่

### E. ย้ายสถานที่แล้ว HIL ใช้ HOME เก่าไม่ได้

เป็นพฤติกรรมที่ตั้งใจไว้ ระบบจำกัดอายุ HOME cache เพื่อให้กลับไปอ่าน GPS สดจากสถานที่ปัจจุบัน

---

## 13) สิ่งที่ GitHub ตั้งใจไม่เก็บ

GitHub repository ไม่ควรมี:

- private keys
- mTLS private certificates
- MAVLink signing secret
- admin password
- session token
- DPAPI credential blobs ของผู้ใช้

ระบบถูกออกแบบให้ **source code อยู่ใน GitHub แต่ secret อยู่ที่เครื่องที่ได้รับอนุญาต**

---

## 14) Flow ที่แนะนำให้จำ

```text
เครื่องใหม่
   │
   ├─► Clone Pitshth123/DroneGod_AI
   │
   ├─► Copy authorized certs
   │
   ├─► SETUP.bat [2]
   │
   ├─► START_SWARMGOD_EASY.bat
   │       └─► Auto local DB + admin + DPAPI + token
   │
   ├─► Launcher → Real Drone → START → Connect FC
   │
   ├─► DISARM + Telemetry + GPS verified
   │
   ├─► RESTART_SWARMGOD_HIL_AUTHED.cmd
   │       ├─► Capture fresh HOME
   │       ├─► Issue token automatically
   │       └─► Restart as profile=hil
   │
   └─► SYSTEM TEST / H02 procedure
```

**สรุป:** เครื่องใหม่ไม่ต้องสร้าง token ด้วยมือ และไม่ต้อง copy token จากเครื่องเก่า
ให้ใช้ `START_SWARMGOD_EASY.bat` เป็นทางเข้าหลัก และใช้ `RESTART_SWARMGOD_HIL_AUTHED.cmd`
หลัง telemetry ของ FC จริงพร้อมแล้วเท่านั้น
