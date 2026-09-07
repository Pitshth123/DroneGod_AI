# ย้าย SwarmGod ไปคอมเครื่องใหม่แบบง่าย

รายการไฟล์และการเปลี่ยนแปลงทางเทคนิคทั้งหมดอยู่ใน
[`AUTOMATIC_AUTH_PC_MIGRATION_CHANGELOG.md`](AUTOMATIC_AUTH_PC_MIGRATION_CHANGELOG.md)

## สิ่งที่ต้องคัดลอก

คัดลอกโฟลเดอร์ `DroneNew` ทั้งโฟลเดอร์ผ่านสื่อที่เชื่อถือได้ โดยต้องมีโฟลเดอร์
`certs` มาด้วย โดยเฉพาะ `ca.crt`, `server.crt`, `server.key`, `client.crt`,
`client.key` และ MAVLink signing key ที่จับคู่กับ Flight Controller จริง

การ clone จาก Git อย่างเดียวไม่มีไฟล์ลับเหล่านี้ เพราะ repository ตั้งใจไม่เก็บ
certificate/private key ไว้ใน Git

## เปิดใช้งานครั้งแรกและครั้งต่อไป

ดับเบิลคลิก `START_SWARMGOD_EASY.bat`

ตัวเปิดจะทำงานต่อไปนี้อัตโนมัติ:

1. ตรวจ Go, Python และแพ็กเกจที่จำเป็น
2. เรียกตัวติดตั้งเมื่อพบว่าส่วนประกอบขาด
3. สร้างฐานผู้ใช้แยกเฉพาะคอมเครื่องนั้นใน `%LOCALAPPDATA%\SwarmGod\data`
4. สร้างรหัส `admin` แบบสุ่มและเก็บด้วย Windows DPAPI
5. ออก session token อายุ 12 ชั่วโมงและส่งให้ Core/Cockpit
6. ข้ามช่องรหัสหน้า Launcher และเปิด Launcher ให้ทันที

ผู้ใช้ไม่ต้องเห็น คัดลอก หรือกรอก token และการเปิดครั้งใหม่จะออก token ใหม่ให้เอง

## ใช้ Flight Controller จริงในสถานที่ใหม่

ครั้งแรกให้เปิดด้วย `START_SWARMGOD_EASY.bat`, เลือก **โดรนจริง**, กด START และ
Connect ขณะโดรน DISARM เพื่ออ่าน telemetry ก่อน จากนั้นดับเบิลคลิก
`RESTART_SWARMGOD_HIL_AUTHED.cmd`

ตัวรีสตาร์ตจะรับ HOME จาก GPS สดของโดรนที่ telemetry verified และ DISARM เท่านั้น
แล้วเปิด Core ใหม่ด้วย HIL + mTLS + token ให้อัตโนมัติ ค่า HOME สำรองใช้ได้ไม่เกิน
30 นาทีเพื่อป้องกันการนำพิกัดจากสถานที่เก่าไปใช้

## ข้อจำกัดการติดตั้งครั้งแรก

การติดตั้ง Go/Python ผ่าน `winget` ต้องใช้อินเทอร์เน็ตและอาจต้องอนุญาต Windows หนึ่งครั้ง
ส่วน SITL ต้องติดตั้ง WSL/ArduPilot เพิ่มด้วย `BUILD_SITL.bat`; การต่อ Flight Controller
จริงไม่ต้องติดตั้ง SITL
