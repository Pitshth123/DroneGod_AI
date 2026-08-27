# ตั้งค่า FC ด้วย Mission Planner (signing + failsafe) — ทีละขั้น

คู่มือนี้ตั้งค่า **Flight Controller (FC) บนโดรนจริง** ให้พร้อมใช้กับ SwarmGod
ทำครั้งเดียวต่อโดรน 1 ลำ (ทำซ้ำทุกลำ)

> FC = บอร์ดควบคุมบนตัวโดรน (Pixhawk/Cube/…) · Mission Planner = โปรแกรมตั้งค่า FC บน Windows

---

## 0. เตรียม
- Mission Planner ติดตั้งแล้ว (เครื่องนี้มีที่ `C:\Program Files (x86)\Mission Planner\`)
- สาย USB ต่อ FC (หรือ telemetry radio)
- ไฟล์ **`certs/mavlink_passphrase.txt`** (SwarmGod สร้างให้จาก `gencerts`) — จะเอา passphrase ในนี้ไปใส่

---

## 1. เปิด + ต่อ FC
1. เปิด **Mission Planner**
2. เสียบ FC เข้า USB → มุมขวาบนเลือก **COM port** ที่ขึ้นมา + baud **115200** → กด **CONNECT**
3. รอโหลด parameter เสร็จ (ขึ้น "Getting Params")

---

## 2. MAVLink Signing (กันคนแอบสั่งโดรน) 🔐
> ต้องใส่ **passphrase เดียวกับ SwarmGod** → FC กับ core จะได้ key ตรงกัน (ทั้งคู่ = SHA256(passphrase))

1. เปิดไฟล์ `certs/mavlink_passphrase.txt` คัดลอก passphrase (เช่น `swarmgod-01b9...`)
2. ใน Mission Planner: **คลิกขวาที่ปุ่ม CONNECT** (มุมขวาบน) → เลือก **MAVLink Signing / Signing Setup**
   *(ตำแหน่งเมนูอาจต่างตามเวอร์ชัน — ถ้าหาไม่เจอ ให้ค้นคำว่า "signing" หรือส่ง screenshot มาให้ผมชี้)*
3. **Enable signing** → วาง passphrase ลงช่อง → **OK/Apply**
   - MP จะส่ง key = SHA256(passphrase) ไปเก็บที่ FC
4. เปิดฝั่ง SwarmGod ให้ strict: ตั้ง env ก่อนรัน core
   ```
   set SWARMGOD_MAVLINK_STRICT=1
   ```
   (หรือใส่ใน `START_SWARMGOD.bat`)

> ✅ เช็ค: ถ้า key ตรง → SwarmGod ต่อโดรนได้ปกติ + คำสั่งถูกเซ็น; ถ้า key ผิด → โดรนจะ **ปฏิเสธคำสั่ง**

---

## 3. Failsafe Parameters (กันโดรนหาย/แบตหมด) 🛟
ไปที่ **CONFIG → Full Parameter List** → ค้นชื่อ param → แก้ค่า → **Write Params**

| Param | ตั้งเป็น | ความหมาย |
|-------|---------|----------|
| `FS_GCS_ENABLE` | **1** | สัญญาณ GCS ขาด → RTL อัตโนมัติ (backstop เมื่อ core ดับ) |
| `FS_THR_ENABLE` | **1** | สัญญาณ RC ขาด → RTL |
| `BATT_LOW_VOLT` | เช่น **14.0** (4S) | แรงดันต่ำ → เตือน/RTL |
| `BATT_CRT_VOLT` | เช่น **13.2** | แรงดันวิกฤต → LAND |
| `BATT_FS_LOW_ACT` | **2** (RTL) | ทำอะไรเมื่อแบตต่ำ |
| `BATT_FS_CRT_ACT` | **1** (LAND) | ทำอะไรเมื่อแบตวิกฤต |
| `FS_OPTIONS` | **0** (ค่าเริ่ม) | ตัวเลือกเสริม failsafe |

> ค่าแรงดันปรับตามจำนวนเซลล์แบต (3S/4S/6S) ของโดรนคุณ

**Geofence ที่ FC (เสริม backstop นอกจาก core):**
| `FENCE_ENABLE` | **1** | เปิดรั้ว |
| `FENCE_ALT_MAX` | เช่น **120** | เพดานสูง (m) |
| `FENCE_RADIUS` | เช่น **300** | รัศมี (m) |
| `FENCE_ACTION` | **1** (RTL) | ทำอะไรเมื่อชนรั้ว |

---

## 4. ก่อนบินครั้งแรก (calibrate — ทำครั้งเดียว)
ไปที่ **SETUP → Mandatory Hardware**:
- [ ] **Accel Calibration** (วางโดรน 6 ด้าน)
- [ ] **Compass Calibration** (หมุนโดรนรอบทิศ)
- [ ] **Radio Calibration** (ถ้ามีรีโมท — โยกสติ๊กจนสุดทุกทาง)
- [ ] **ESC Calibration** (ถ้าจำเป็น)
- [ ] `ARMING_CHECK` = **1** (เปิดไว้เพื่อความปลอดภัย; ตั้ง 0 เฉพาะตอนทดสอบ)

---

## 5. Verify (ตรวจว่าพร้อม)
1. ปิด Mission Planner (ปล่อย COM port ให้ว่าง)
2. รัน SwarmGod (`START_SWARMGOD.bat` — เอา ✕ ออกจาก "ใช้ SITL")
3. ใน cockpit เชื่อมโดรน (IP:port) → ถ้า **signing ตรง** จะ READY + คำสั่งทำงาน
4. ทดสอบสั่ง Arm/Takeoff ในที่โล่งปลอดภัย

---

## ⚠️ Troubleshooting
| อาการ | แก้ |
|-------|-----|
| SwarmGod ต่อได้แต่คำสั่ง**ไม่ทำงาน** (strict on) | key ไม่ตรง — passphrase ใน MP ต้องตรงกับ `mavlink_passphrase.txt` เป๊ะ (รวมตัวพิมพ์เล็ก/ใหญ่) |
| หาเมนู signing ใน MP ไม่เจอ | เวอร์ชันต่างกัน — ส่ง screenshot หน้า MP มา ผมชี้ให้ |
| อยากปิด signing ชั่วคราว | ไม่ตั้ง `SWARMGOD_MAVLINK_STRICT` (core จะเซ็นออกอย่างเดียว ไม่บังคับ incoming) |
| ต่อ COM ไม่ได้ | ปิดโปรแกรมอื่นที่จับ COM (รวม SwarmGod core) ก่อน |

> เปลี่ยน passphrase: รัน `go run ./cmd/gencerts -passphrase "ข้อความของคุณ" -out ../certs`
> แล้วเอา passphrase ใหม่ไปใส่ MP ใหม่ทั้งสองฝั่ง
