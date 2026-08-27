# Servo (A/B) + Datalink + UI รอบปรับปรุง

> งานรอบนี้ 11 ข้อ — เชื่อมระบบ servo เข้ากับ backend จริง, แก้ datalink ที่ไม่มีข้อมูลจริง,
> และปรับ UI หลายจุด · อ้างอิงสเปก servo จาก `servo.md` (GCS_1 เดิม)

---

## 0. ⚠️ อ่านก่อน — เจอ `UNIMPLEMENTED: method Servo not implemented`

```
RC: ERROR <_InactiveRpcError of RPC that terminated with:
    status = StatusCode.UNIMPLEMENTED
    details = "method Servo not implemented"
```

**ไม่ใช่บั๊กของโค้ด** — แปลว่า **core ที่รันอยู่เป็น binary เก่า** ที่ build ไว้ก่อนจะมี
handler `Servo` · แก้ Go source อย่างเดียวไม่พอ ต้อง rebuild + restart core เสมอ

```bash
cd backend
go build -o bin/swarmgod-core.exe ./cmd/swarmgod-core
```

แล้ว **ปิด core เดิมที่ค้างอยู่** ก่อนเปิดใหม่ (ไม่งั้นพอร์ต 50051 ถูกจอง):

```powershell
Get-Process swarmgod-core -ErrorAction SilentlyContinue | Stop-Process -Force
```

> บทเรียนเดียวกับที่ `SWARM_CONTROL.md` §13.1 เคยเตือนไว้ — RPC ที่เพิ่มใหม่จะขึ้น
> `UNIMPLEMENTED` เสมอถ้า binary ยังเก่า เพราะ `UnimplementedSwarmGodServiceServer`
> ตอบแทนให้

---

## 0.1 📁 log ของ core อยู่ที่ไหน

| ไฟล์ | เนื้อหา |
|------|---------|
| `backend/logs/core-YYYYMMDD.log` | **log หลักของ core** (เริ่ม/หยุด, MAVLink, error) |
| `backend/logs/core-stdout.log` | stdout/stderr ดิบจาก launcher (เผื่อ core พังก่อนตั้ง logger ได้) |
| `backend/logs/audit/audit-YYYYMMDD.jsonl` | **audit ทุกคำสั่ง** (ใครสั่งอะไร ผลเป็นยังไง) |
| `backend/logs/swarmgod.db` | SQLite (fleet, index ของ audit) |

**ก่อนหน้านี้ไม่มีไฟล์ log ของ core เลย** — launcher เปิด core แบบไม่มีหน้าต่างแล้ว
ทิ้ง stdout/stderr ลง `DEVNULL` ทั้งหมด เวลาเกิดปัญหาจึงไล่หาสาเหตุไม่ได้
· ตอนนี้ core เขียนไฟล์เอง (ผ่าน `io.MultiWriter`) จึงมี log ไม่ว่าจะเปิดด้วยวิธีไหน

**วิธีไล่ปัญหาคำสั่งที่ล้มเหลว** — เทียบ 2 ที่:

```bash
# UI บอกอะไร (Mission Log ใน cockpit)
# core บอกอะไร:
tail -50 backend/logs/core-$(date +%Y%m%d).log
grep -i servo backend/logs/audit/audit-$(date +%Y%m%d).jsonl | tail
```

> ⚠️ audit ใช้เวลา **UTC** ส่วน cockpit แสดงเวลาเครื่อง (ไทย = UTC+7)
> เช่น UI 15:05:42 → หาใน audit ที่ `08:05:42`

---

## 0.2 🐛 บั๊ก: คำสั่งสำเร็จแต่ UI ขึ้น FAILED

**อาการ**: กด A/B แล้วขึ้น `SERVO B D1: FAILED — ไม่ได้รับคำตอบจาก core`
แต่กลไกทำงานจริง · หลังจากนั้นสักพักก็ดูเหมือนใช้ได้ปกติ

**วิธีจับ**: เทียบ audit กับ UI — audit บันทึก `ACCEPTED` ที่ `08:05:42.127`
ในวินาทีเดียวกับที่ UI ขึ้น FAILED → พิสูจน์ว่า core รับคำสั่งสำเร็จ ปัญหาอยู่ฝั่ง UI

**ต้นตอ**: `_safe()` ใน `app.py` ลืมใส่ `return`

```python
def _safe(self, fn):
    try:
        fn()          # ← ไม่มี return → คืน None เสมอ
    except Exception as e:
        self.cmd_result.emit(f"RC: ERROR {e}")
```

ผู้เรียกเช็คว่า `r is not None and r.ok` → **เป็น False ทุกครั้ง** แม้คำสั่งจะสำเร็จ
· ที่ผ่านมาไม่มีใครสังเกตเพราะจุดอื่นเรียก `_safe()` แบบไม่สนใจค่าคืน
มีแค่คำสั่ง servo ที่เช็คผลลัพธ์

**แก้**: ใส่ `return fn()` + คืน `None` เมื่อ exception · มี regression test 3 เคสคุม
(`SafeReturnsResultTests`)

---

## 0.3 ⚡ UI หน่วงตอนกดปุ่ม — main thread ตันเพราะ telemetry

**อาการ**: กดปุ่ม A/B แล้วรอนานกว่าจะตอบสนอง

**วิธีวัด** (ไม่เดา):

```
_on_telemetry: 14.15 ms/แพ็กเก็ต
```

telemetry จริงมา **10 Hz ต่อลำ × 5 ลำ = 50 แพ็กเก็ต/วิ**
→ `14.15 ms × 50 = 708 ms/วิ` = **กิน main thread 70%**
เหลือให้ Qt รับคลิกแค่ 30% คลิกจึงไปต่อคิวรอ

### ต้นตอที่ profiler ชี้

| จุด | ความถี่ | ปัญหา |
|-----|---------|-------|
| `_ip_store.upsert()` | **1 ครั้ง/แพ็กเก็ต** | เปิด SQLite connection + เขียนดิสก์ **50 ครั้ง/วิ** ทั้งที่ IP แทบไม่เคยเปลี่ยน |
| `setStyleSheet()` | **~8 ครั้ง/แพ็กเก็ต** | Qt แปลง stylesheet + คำนวณ style + repaint ใหม่ทุกครั้ง ทั้งที่ค่าซ้ำเดิม |

### วิธีแก้

1. **`_remember_endpoint`** — ข้ามทั้งหมดถ้า `(host, port)` ตรงกับที่จำไว้แล้ว
2. **`set_qss(widget, qss)`** — helper ใหม่ใน `fleet_item.py` เก็บค่าล่าสุดไว้ใน
   `_qss_cache` property แล้วเรียก `setStyleSheet` เฉพาะตอนค่าเปลี่ยนจริง
   · ใช้แทนที่ทุกจุดใน hot path (badge, dot, battery, swatches, LINKQ, ชื่อ)

### ผลลัพธ์

| | ก่อน | หลัง |
|---|------|------|
| `_on_telemetry` | 14.15 ms/แพ็กเก็ต | **0.36 ms** |
| CPU ที่ 50 pkt/s | ~70% | **1.8%** |

**เร็วขึ้น ~39 เท่า** · ยืนยันแล้วว่า cache ไม่บังการอัปเดต — แบต/สถานะ/ป้าย servo
ยังเปลี่ยนตามจริงทุกกรณี (มีเทสต์ `TelemetryPerfTests` 4 เคสคุม รวมทั้งเกณฑ์
< 4 ms/แพ็กเก็ต กันถอยหลัง)

---

## 1. ⚠️ Datalink — เดิมโชว์ "100%" ปลอม

### สิ่งที่พบ
`telemetry.proto` **ไม่มี field `rssi` เลย** แต่ cockpit เขียน `getattr(t, "rssi", 0)`
→ ได้ `0` ทุกครั้ง แล้วสูตรคำนวณ LINK QUALITY เดิม:

```
linkq = (rssi + 90) / 60 * 100  →  (0 + 90) / 60 * 100 = 150  → clamp → 100%
```

**ผลคือหน้าจอที่ใช้ตัดสินใจบินโชว์ "LINK 100%" สีเขียวตลอดเวลา** ไม่ว่าสัญญาณจริงจะเป็นยังไง
(อันตราย — ผู้ใช้เชื่อว่าลิงก์เต็มทั้งที่ระบบไม่เคยรู้ค่าจริงเลย)

### สิ่งที่แก้

**proto** — เพิ่ม 4 field (`telemetry.proto`):

| field | ชนิด | ความหมาย |
|-------|------|----------|
| `rssi` | int32 | dBm จากวิทยุ SiK (RADIO_STATUS) |
| `rssi_valid` | bool | **false = ไม่มีวิทยุรายงาน** → UI ต้องโชว์ N/A ไม่ใช่ 0 |
| `link_quality` | uint32 | 0..100 คำนวณจริง ใช้ได้ทุก transport |
| `drop_rate` | uint32 | ‰ packet drop จาก SYS_STATUS |

**Go (`internal/fleet/drone.go`)** — รับข้อมูลจริงจาก MAVLink:
- `MessageRadioStatus` → แปลงเป็น dBm ตามสูตร SiK: `dBm = rssi/1.9 - 127`
- `MessageSysStatus.DropRateComm` → drop rate
- `MessageHeartbeat` → วัดระยะห่าง heartbeat จริง (EMA) ใช้เป็น link quality
  เมื่อไม่มีวิทยุ (SITL/USB/UDP ตรง)

```go
func (d *Drone) linkQuality() uint32 {
    if !d.connected { return 0 }
    switch {
    case d.rssiFresh():        q = (rssi + 120) / 70 * 100    // มีวิทยุ
    case d.hbIntervalMs > 0:   q = (3000 - hbMs) / 2000 * 100 // ไม่มีวิทยุ
    default:                   return 0                        // ไม่รู้ = บอกว่าไม่รู้
    }
    q -= dropRate / 10
    ...
}
```

**หลักการสำคัญ**: ถ้าไม่มีข้อมูลพอ → คืน `0` / โชว์ `N/A` **ไม่เดาว่าเต็ม**
· `rssiFresh()` หมดอายุใน 5 วิ (วิทยุถูกถอด → กลับเป็นไม่มีข้อมูล ไม่ค้างค่าเดิม)

**UI** — `RSSI: N/A` และ `LINK: --` เมื่อไม่มีข้อมูลจริง

---

## 2. Servo A/B — RPC ที่ประกาศไว้แต่ไม่เคย implement

### สิ่งที่พบ
`Servo` RPC มีอยู่ใน `service.proto` แล้ว **แต่ไม่มี handler ทั้งฝั่ง Go และ Python**
→ `UnimplementedSwarmGodServiceServer` กลืนคำสั่งเงียบ ๆ (บั๊กแบบเดียวกับ `SetLeader` รอบก่อน)
และ `ServoRequest` **ไม่มี field `channel`** จึงแยก ch7/ch8 ไม่ได้

### สิ่งที่แก้

**proto** — เพิ่ม `channel` (0 = default 8 เพื่อเข้ากันได้ย้อนหลัง)

### 2.1 กลไกที่ใช้: RC_CHANNELS_OVERRIDE (ไม่ใช่ DO_SET_SERVO)

**ทำไมเปลี่ยน**: ตอนแรกใช้ `DO_SET_SERVO` (cmd 183) ตาม servo.md แต่มันใช้ร่วมกับ
RC passthrough ไม่ได้ —

- `SERVOn_FUNCTION = RCINn` → FC เขียนค่าจาก RC ลงขาเซอร์โว **ทุก loop (50–400 Hz)**
- `DO_SET_SERVO` เป็นคำสั่ง **ครั้งเดียว** → ถูกทับภายในเสี้ยววินาที

ไม่ใช่ "ถูกบล็อก" แต่ "ถูกเขียนทับตลอดเวลา" · ทางออกคือให้ cockpit ส่ง
**`RC_CHANNELS_OVERRIDE`** แทน → FC มองว่าค่านั้น "มาจากรีโมท" จึงผ่าน passthrough ได้

```
รีโมทโยกสวิตช์   ──┐
                   ├─→ RC7/RC8 ─→ SERVO7/8 (passthrough) ─→ เซอร์โว
cockpit override ──┘
```

**API ฝั่ง Go** (`internal/fleet/drone.go`):

| ฟังก์ชัน | ทำอะไร |
|----------|--------|
| `SetRCOverride(ch, pwm)` | override ช่องนั้น + เริ่ม loop ส่งซ้ำ |
| `ClearRCOverride(ch)` | เลิก override เฉพาะช่องนั้น → คืนให้รีโมท |
| `ClearAllRCOverride()` | ปล่อยทุกช่อง |
| `RCOverrideActive()` | ดูว่าช่องไหน override อยู่ |

### 2.2 ⚠️ สองจุดที่เป็นเรื่องความปลอดภัย

**1. ห้าม override คันบังคับหลัก (RC1-4)**

`RC_CHANNELS_OVERRIDE` ครอบคลุมทุกช่องในข้อความเดียว ถ้าเผลอใส่ค่า roll/pitch/
throttle/yaw = **แย่งคันบังคับจากนักบินกลางอากาศ**

กันไว้ 2 ชั้น:
- `SetRCOverride()` **reject ทันที**ถ้า channel ≤ 4 (มีเทสต์คุม)
- ช่องที่ไม่ได้สั่งจะเป็น `0` = "ปล่อยให้รีโมทจริงคุม" ตามสเปก MAVLink
  ซึ่งบังเอิญตรงกับ zero-value ของ Go พอดี → **ปลอดภัยโดยดีฟอลต์**

**2. override หมดอายุ ~3 วิ → ต้องส่งซ้ำ**

ArduPilot ปล่อย override ทิ้งเองถ้าไม่มีข้อความใหม่ (`RC_OVERRIDE_TIME`)
core จึงมี loop ส่งซ้ำทุก **500 ms** ขณะที่ยังมีช่อง override อยู่ · หยุดอัตโนมัติเมื่อ
ปล่อยหมด (ไม่มี goroutine รั่ว — มีเทสต์คุม)

> **นี่เป็น failsafe ในตัว**: ถ้า core ตาย → ไม่มีใครส่ง override → หมดอายุใน 3 วิ →
> **รีโมทได้คุมคืนอัตโนมัติ**

### 2.3 กติกา "1 ห้อง" — เจ้าของสิทธิ์ต่อช่อง (สำคัญที่สุด)

**ปัญหาที่เจอจริงตอนทดสอบกับรีโมท**:

| ลำดับ | อาการ |
|-------|-------|
| UI กดก่อน | รีโมทสั่งไม่ได้ (override ทับ) — ถูกต้องตามกลไก |
| **รีโมทกดก่อน** | **UI กดไม่ได้อีกเลย** จนกว่าจะเชื่อมต่อใหม่ ← บั๊ก |

**ต้นตอ**: UI อ่าน RC7=1950 เห็นว่า "เปิดอยู่" → toggle ตัดสินว่าครั้งต่อไปต้องเป็น
"ยกเลิก" → ส่งคำสั่งปล่อย override **ที่ไม่เคยมีอยู่** = ไม่เกิดอะไรขึ้น แล้ววนแบบนั้น
(ไม่ได้ถูกล็อกจริง แต่ตรรกะพาไปติดกับ)

**แก้เป็นระบบเจ้าของสิทธิ์** — ทำ "1 ห้อง" ให้เป็นการออกแบบจริง:

| เจ้าของ | เงื่อนไข | UI กด | รีโมทโยก |
|---------|----------|-------|----------|
| ว่าง | ไม่ override + ค่าปิด | เปิด → UI ถือห้อง | เปิด → รีโมทถือห้อง |
| **UI** | core override อยู่ | ปิด → คืนห้อง | ถูกเมิน (override ทับ) |
| **RC** | ไม่ override แต่ค่าเปิด | **ปฏิเสธ + บอกเหตุผล** | ปิดได้ → คืนห้อง |

A และ B **แยกห้องกัน** — รีโมทถือ A อยู่ ไม่กระทบการใช้ B

**ปุ่มบอกสถานะ 3 แบบ**: `A` (ว่าง) · `A ●` (UI ถือ) · `A 🔒` (รีโมทถือ กดไม่ได้)

#### ⚠️ ข้อจำกัดทางเทคนิคที่ต้องรู้

**ขณะ override ค้างอยู่ เรามองไม่เห็นตำแหน่งสวิตช์จริง** — ArduPilot เขียนค่า override
ทับ `radio_in` แล้ว `RC_CHANNELS` ก็รายงานค่านั้น จึงแยกไม่ออกว่า "1950 ที่เห็น"
มาจาก cockpit หรือจากคนโยกสวิตช์

จึงต้องมีธง `ovr_ch7`/`ovr_ch8` ใน telemetry บอกตรง ๆ ว่า **core กำลัง override
ช่องไหนอยู่** (ground truth จาก `RCOverrideActive()`) — เป็นตัวชี้ขาดว่าใครถือห้อง
· ข้อดีคือรอด cockpit restart ด้วย (ไม่ได้จำไว้ในหน่วยความจำ UI)

**ผลตามมา**: ตอน UI กดปิด เราไม่รู้ล่วงหน้าว่าสวิตช์รีโมทค้างอยู่ไหม จึงใช้วิธี
**"ปล่อยก่อนแล้วเช็ค"** (`_verify_servo_released`, หน่วง 1.2 วิ):
- ปล่อยแล้วปิดจริง → ห้องว่าง ✓
- ปล่อยแล้วยังเปิด → สวิตช์รีโมทค้างที่ตำแหน่งเปิด → **banner แดงค้าง**บอกให้ปิดสวิตช์
  ที่รีโมท และห้องตกเป็นของรีโมท

> วิธีนี้ปลอดภัยเพราะ override ที่ UI ตั้งมีแค่ค่า "เปิด" อย่างเดียว — จังหวะที่ปล่อย
> จึงไม่มีทางทำให้กลไกเปิดขึ้นมาเอง มีแต่จะปิด (ตามสวิตช์) ซึ่งเป็นผลที่ต้องการอยู่แล้ว

### 2.4 ความหมายของ "ปิด" (กดปุ่มซ้ำ)

**ปิด = ปล่อย override คืนช่องให้รีโมท** ไม่ใช่ override ค้างไว้ที่ค่าปิด

เพราะถ้า override ค้าง สวิตช์จริงบนรีโมทจะ**ถูกเมินตลอดไป** ซึ่งขัดกับที่ตั้งใจให้
สั่งได้ทั้งสองทาง — และจะไม่มีวัน "จบการทำงานฝั่ง UI" ตามที่ต้องการ

> **เคสที่ต้องเข้าใจ**: ถ้าสวิตช์บนรีโมทค้างที่ตำแหน่ง "เปิด" ตอน UI กดปิด
> กลไกจะ**ยังเปิดอยู่** (เพราะรีโมทสั่งอย่างนั้นจริง ๆ) — ระบบจะขึ้น banner แดงค้าง
> บอกให้ปิดสวิตช์ที่รีโมท และห้องตกเป็นของรีโมททันที · ป้ายบนจอยังบอกความจริง ไม่โกหก

**Python** — `servo_set()` (override) / `servo_release()` (ปล่อยช่อง) /
`servo_reset()` (ปล่อยทุกช่อง) ใน `grpc_client.py`
(Servo RPC เป็น per-drone ไม่ใช่ target list → app วนส่งทีละลำ)

### ⚠️ ต้องตั้งค่าที่ FC ด้วย — ตั้งตรงไหนใน Mission Planner

> **สรุปสั้น**: เปิด Mission Planner → `CONFIG` → `Full Parameter List` →
> ค้นหาชื่อพารามิเตอร์ → แก้ค่า → กด **Write Params** → **รีบูต FC**

#### ก) ตั้งช่องเป็น RC passthrough ← **ค่าที่ถูกต้องสำหรับระบบนี้**

| พารามิเตอร์ | ตั้งเป็น | ผล |
|-------------|---------|-----|
| `SERVO7_FUNCTION` | **57** (RCIN7) | ปุ่ม A — RC7 ส่งผ่านไปขาเซอร์โว 7 |
| `SERVO8_FUNCTION` | **58** (RCIN8) | ปุ่ม B — RC8 → ขาเซอร์โว 8 |

ตั้งแบบนี้แล้ว **สั่งได้ทั้งจากรีโมทและจาก cockpit** เพราะ cockpit ใช้
`RC_CHANNELS_OVERRIDE` ไม่ใช่ `DO_SET_SERVO` (ดู §2.1)

> ❌ **อย่าตั้งเป็น 0 (Disabled)** — จะได้เฉพาะ cockpit สั่งได้ รีโมทใช้ไม่ได้
> (นั่นคือ config ของเวอร์ชันแรกที่ใช้ `DO_SET_SERVO` ซึ่งเลิกใช้แล้ว)

#### ค) ระยะ PWM ของแต่ละช่อง (ให้ตรงกับกลไกจริง)

| พารามิเตอร์ | ค่าที่ใช้กับรีโมทเครื่องนี้ |
|-------------|---------------------------|
| `SERVO7_MIN` / `SERVO7_MAX` | 1050 / 1950 |
| `SERVO8_MIN` / `SERVO8_MAX` | 900 / 2100 |

#### ง-1) พารามิเตอร์ที่เกี่ยวกับ RC override

| พารามิเตอร์ | ความหมาย | หมายเหตุ |
|-------------|----------|----------|
| `RC_OVERRIDE_TIME` | override หมดอายุกี่วินาทีถ้าไม่มีข้อความใหม่ | ดีฟอลต์ ~3.0 · core ส่งซ้ำทุก 0.5 วิ จึงไม่ขาด · **ตั้งเป็น 0 = ไม่หมดอายุ ไม่แนะนำ** (เสีย failsafe) |

> ⚠️ **ชื่อ/ดีฟอลต์ของพารามิเตอร์นี้ต่างกันตามเวอร์ชันเฟิร์มแวร์** — ตรวจในเครื่องจริงก่อน

#### ง) ให้ cockpit เห็นสถานะสวิตช์จากรีโมท

ต้องมี stream ของ `RC_CHANNELS` ส่งมาด้วย (ปกติ ArduPilot ส่งอยู่แล้ว) — ถ้าไม่มา ตรวจ:

| พารามิเตอร์ | ตั้งเป็น | ความหมาย |
|-------------|---------|----------|
| `SR0_RC_CHAN` (หรือ `SR1_RC_CHAN` ตามพอร์ตที่ต่อ) | **≥ 2** (Hz) | อัตราส่ง RC_CHANNELS |
| `SR0_EXT_STAT` | ≥ 2 | รวม SYS_STATUS (ใช้กับ drop rate/link quality) |

> `SR0_*` = พอร์ต USB · `SR1_*` = TELEM1 · `SR2_*` = TELEM2 — ตั้งของพอร์ตที่ใช้ต่อจริง

#### จ) ตรวจว่าตั้งถูกไหม (ก่อนบิน)

ใน Mission Planner → `SETUP` → `Optional Hardware` → `Servo Output`
หรือหน้า `DATA` → แท็บ `Status` ดูค่า `ch7in` / `ch8in` ตอนโยกสวิตช์
— ต้องเห็นตัวเลขเปลี่ยน 1050↔1950 (A) และ 900↔2100 (B)

---

## 3. ปุ่ม A/B — toggle + การยืนยัน

- ปุ่ม **A** (แดง) / **B** (เหลือง) อยู่ในหมวด FLIGHT · **ขนาดใหญ่** (สูง 60px,
  ฟอนต์ 26px) กดง่ายตอนใส่ถุงมือ แต่ยัง**อยู่แถวเดียวกัน** แบ่งความกว้างเท่ากัน
- **กดครั้งแรก = เปิด (PWM 2100) · กดซ้ำ = ยกเลิก (PWM 900)**
  ข้อความยืนยันเปลี่ยนตามด้วย ("ยืนยันสั่ง" ↔ "ยืนยันยกเลิก")
- **ถามยืนยัน 1 ครั้งทุกครั้ง** ทั้งตอนเปิดและตอนยกเลิก (เป็นกลไกปล่อยของจริง)
- ปุ่ม**ติดไฟ** (พื้นทึบ + จุด ●) เมื่อช่องนั้นเปิดอยู่ → รู้ทันทีว่ากดครั้งหน้าคือยกเลิก
- **DISARM** ก็เพิ่ม confirm 1 ครั้งด้วย (กดพลาดกลางอากาศ = ร่วงทันที)
- ทั้งหมดผ่าน `_guard()` → REMOTE เปิดอยู่กดไม่ติด

> A และ B เป็นคนละช่อง (7/8) จึง **toggle แยกกันอิสระ** — เปิดพร้อมกันทั้งคู่ได้

## 4. ป้ายสถานะ A/B — อ่านจากของจริง เห็นได้แม้กดจากรีโมท

สี่เหลี่ยมเล็กข้างชื่อโดรน มีตัวอักษรอยู่ข้างใน — **A = แดง, B = เหลือง**
โชว์ทั้งแถว FLEET และการ์ด SELECTED DRONE (เปิดทั้งคู่ = โชว์ `A B`)

### ⚠️ RC input ≠ Servo output — คนละชั้น อย่าสับสน

เดิม `_servo_state` จำแค่ "cockpit เคยกดอะไรไป" — ถ้าผู้ใช้โยกสวิตช์ **บนรีโมท**
cockpit จะไม่รู้เลย ป้ายบนจอไม่ตรงกับของจริง

**รอบแรกผมแก้ผิด**: ไปอ่าน `SERVO_OUTPUT_RAW` (เอาต์พุตที่ขาเซอร์โว) ทั้งที่คำถามคือ
"สวิตช์บนรีโมทถูกกดหรือยัง" ซึ่งต้องอ่าน `RC_CHANNELS` (อินพุตจากรีโมท)

| MAVLink message | คืออะไร | ใช้ตอบคำถาม |
|-----------------|---------|-------------|
| `RC_CHANNELS` (ch7/ch8) | **สวิตช์บนรีโมทอยู่ตำแหน่งไหน** | "กด A/B มาหรือยัง" ← ตรงที่สุด |
| `SERVO_OUTPUT_RAW` (servo7/8) | FC ขยับขาเซอร์โวจริงเท่าไร | "กลไกทำงานจริงไหม" |

**ลำดับความน่าเชื่อถือที่ใช้** (`_servo_active`):
1. RC input (`rc_valid` = true) ← หลัก
2. servo output (`servo_valid` = true) ← เผื่อรีโมทไม่ได้ต่อ
3. สิ่งที่ UI สั่งล่าสุด ← ไม่มีข้อมูลจาก FC เลย

**ค่าจริงจากรีโมทเครื่องนี้** (ผู้ใช้วัดมา):

| ปุ่ม | ช่อง | ปล่อย | กด |
|------|------|-------|-----|
| A | RC7 | 1050 | 1950 |
| B | RC8 | 900 | 2100 |

- **เกณฑ์ "เปิด" = ≥ 1500** (`SERVO_ON_MIN`) — อยู่กึ่งกลางของทั้งสองช่วง
- cockpit ส่งค่าเดียวกับรีโมท: เปิด = **1950**, ยกเลิก = **1050**
- `rcFresh()` / `servoFresh()` หมดอายุใน 5 วิ — สัญญาณขาดแล้วต้องไม่ค้างสถานะเก่า
  หลอกว่ายังกดสวิตช์ค้างอยู่

**ผลลัพธ์**: โยกสวิตช์ A ที่รีโมท → ป้ายบนการ์ดขึ้น A และปุ่ม A ติดไฟทันที
โดย cockpit ไม่ได้ส่งคำสั่งอะไรเลย

## 5. ลงจอดเสร็จ → GUIDED อัตโนมัติ

`_auto_guided_after_land()` — เงื่อนไข:
1. เคยเห็นสถานะ `LANDING` มาก่อน
2. ตอนนี้ **disarm แล้ว + สูงต่ำกว่า 1 m** (แตะพื้นจริง ไม่ใช่แค่ลดระดับ)
3. ยังไม่ได้อยู่ GUIDED
4. **ไม่ได้อยู่โหมด REMOTE** (REMOTE ถือคันบังคับ ห้าม UI แทรก)

สั่งครั้งเดียวต่อรอบ (`_land_guided_done`) — telemetry มา 10 Hz ถ้าไม่กันจะถล่ม FC
ด้วย DO_SET_MODE

## 6. ป้ายโหมดปัจจุบันในหมวด FLIGHT

เดิมมีแต่ปุ่มสั่ง (UI GUIDED / RC LOITER) ไม่มีอะไรบอกว่า "ตอนนี้อยู่โหมดอะไร"
→ เพิ่ม `lbl_cur_mode` ตัวใหญ่ · **GUIDED = เขียวเด่น**, LOITER/POSHOLD = ฟ้า, อื่น ๆ = เหลือง

## 7. ป้ายโหมดกลาง top bar (อนิเมชัน)

- **ถอด** pill โหมดเดิมที่ซ้ายบนออก
- ป้ายใหม่ลอยกลาง topbar **เลื่อนลงมาแล้วค้างไว้** (`QPropertyAnimation` 320 ms, OutCubic)
- สีตามโหมดเดิม: FLIGHT=ฟ้า · SWARM=เหลือง · RTL=ส้ม · WAYPOINT=เขียว
- **แถบสี `_mode_strip` คงเดิม** (alpha 0.35 จางกว่า banner)

> **จุดที่พลาดแล้วแก้**: ตอนแรกจัดกึ่งกลางใน `resizeEvent` ของหน้าต่าง แต่ตอนนั้น
> topbar ยัง layout ไม่เสร็จ `width()` ยังเป็นค่าเก่า ป้ายเลยไปกองซ้ายสุด
> → ย้ายไปใช้ `eventFilter` ดักที่ topbar เองแทน

## 8. ตั้งค่ารูปไอคอน top bar (สูงสุด 5 รูป)

- `core/logo_store.py` — **ลอจิกล้วน ไม่มี Qt** (เทสต์ headless ได้)
- เก็บที่ `~/.swarmgod/logos/` (ข้อมูลผู้ใช้ ไม่ใช่ asset ของโค้ด)
- ตั้งชื่อไฟล์ `NN_<timestamp>_<ชื่อเดิม>` → **เรียงตามลำดับเพิ่ม = รูปใหม่ไปทางขวา**
- จำกัด 5 รูป · ปฏิเสธไฟล์ที่ไม่ใช่รูป
- เข้าถึงที่เมนู ⚙ → "รูปไอคอน Top bar…"

## 9. SCAN — ติ๊กเลือก + เชื่อมต่อพร้อมกัน

- checkbox ทุกแถว + ปุ่ม **SELECT ALL / CLEAR ALL**
- ปุ่ม CONNECT โชว์จำนวนที่เลือก `CONNECT (3)` → ต่อทุก IP ที่ติ๊กพร้อมกัน
- ไม่ได้ติ๊กเลย → ใช้แถวที่ไฮไลต์อยู่ (พฤติกรรมเดิม)

### ⚠️ บั๊กที่เจอตอนทำฟีเจอร์นี้ — ID โดรนชนกัน
`_next_drone_id()` เดิมดูแค่ `self.fleet_items` ซึ่ง**มีสมาชิกก็ต่อเมื่อ telemetry มาแล้ว**
→ กด CONNECT หลายลำรวดเดียว **ได้ ID เดียวกันหมด** (พิสูจน์แล้ว: `[1, 1, 1]`)

**แก้**: เพิ่ม `_reserved_ids` จอง ID ตอนกด CONNECT แล้วปล่อยเมื่อ telemetry มาถึง/ลบโดรน
→ ตอนนี้ได้ `[1, 2, 3]`

---

## 10. ผลการทดสอบ

### Python — **438 tests OK**

| ไฟล์ | จำนวน |
|------|-------|
| test_swarm_logic / test_waypoint | 35 / 15 |
| test_ui_selection / test_map_collision_ui / test_rtl_and_mapui | 32 / 36 / 36 |
| test_head_rtlcfg_mode / test_tactical_map / test_ui_polish | 37 / 36 / 35 |
| test_waypoint_ui / test_waypoint_separate | 47 / 42 |
| test_help_and_alerts | 27 |
| **test_servo_and_ui** (ใหม่รอบนี้) | **40** |

| **test_servo_and_ui** (RC override + 1 ห้อง + _safe + perf) | **60** |

### Go — ผ่านทุก package

| ไฟล์ | เคส | ครอบคลุม |
|------|-----|----------|
| `internal/fleet/servo_link_test.go` | 8 | clamp PWM, ช่อง A/B, link quality (ไม่มีข้อมูล/หลุด/heartbeat/drop rate/clamp 100), rssi ไม่ valid เมื่อไม่มีวิทยุ |
| `internal/fleet/rcoverride_test.go` | 10 | **ปฏิเสธ override RC1-4**, ปฏิเสธช่องนอกช่วง, คันบังคับเป็น 0 เสมอ, override ทีละช่อง/สองช่อง, ปล่อยเฉพาะช่อง/ปล่อยหมด, **loop ส่งซ้ำทำงาน**, **หยุดเมื่อปล่อย**, ไม่มี loop ซ้อน |

> **ยังไม่ได้รัน race detector** — เครื่องนี้ไม่มี gcc (`CGO_ENABLED=1` ต้องใช้)
> การเข้าถึง `ovrCh`/`ovrStop` ทั้งหมดอยู่ใต้ `ovrMu` แล้ว แต่ควรรัน
> `CGO_ENABLED=1 go test -race ./internal/fleet/` บนเครื่องที่มี gcc เพื่อยืนยัน

### ปัญหาที่เจอระหว่างเทส (แก้แล้ว)

1. **เทสเก่าอ้าง `pill_fmode` ที่ถอดออกไปตามข้อ 7** — 6 เคสใน `test_head_rtlcfg_mode`
   พัง → อัปเดตให้ชี้ `mode_drop` แทน
2. **`test_help_and_alerts` crash เงียบ ๆ กลางคัน** — ไม่ใช่ flake แต่เป็น
   **resource leak ในเทสเอง**: `tearDown` เรียกแค่ `close()` ไม่ได้ปล่อย object
   พอสะสม GroundStation 8+ ตัวในโปรเซสเดียวก็พัง
   → เพิ่ม `deleteLater()` + `processEvents()` ตาม pattern ของ `test_ui_selection.py`
3. **`FakeClient` ไม่มี `set_mode`/`servo_*`** — `_safe()` กลืน AttributeError ทำให้
   เทสผ่านแบบหลอก ๆ (ไม่ได้ยิงคำสั่งจริงแต่ไม่ error) → เพิ่มเมธอดที่ขาด

---

## 10.1 🔴 ต้องทดสอบบนโต๊ะก่อนบิน (ถอดใบพัดออกก่อน)

เทสต์ทั้งหมดข้างบนเป็น **mock ล้วน** ยังไม่เคยเจอ FC จริง · RC override แตะระบบ
ควบคุมโดยตรง จึงต้องไล่เช็คตามนี้ก่อนเอาขึ้นบิน:

| # | ทดสอบ | ผลที่ต้องได้ |
|---|-------|--------------|
| 1 | โยกสวิตช์ A/B บนรีโมท (cockpit ไม่แตะ) | เซอร์โวขยับ · ป้าย A/B บนจอขึ้นตาม |
| 2 | กดปุ่ม A ใน cockpit | เซอร์โวขยับ · ปุ่มติดไฟ · ป้ายขึ้น A |
| 3 | **ขณะ A override อยู่ ขยับคันบังคับทุกแกน** | **โดรนตอบสนองปกติทุกแกน** ← สำคัญที่สุด |
| 4 | กดปุ่ม A ซ้ำ (ปิด) | override หลุด · สวิตช์รีโมทกลับมาคุมช่องนั้น |
| 4b | **โยกสวิตช์ A ที่รีโมทค้างไว้ แล้วกด A ใน UI** | ปุ่มขึ้น `A 🔒` · UI ปฏิเสธ + บอกให้ปิดสวิตช์ที่รีโมทก่อน |
| 4c | **UI เปิด A → กดปิด ขณะสวิตช์รีโมทค้างเปิด** | banner แดงค้าง "สวิตช์รีโมทค้าง" · ห้องตกเป็นของรีโมท |
| 5 | กด A ค้างไว้ แล้ว **ปิด core** | ภายใน ~3 วิ เซอร์โวกลับไปตามสวิตช์รีโมท |
| 6 | กด A ค้างไว้ แล้ว **ถอดสายรีโมท** | RC failsafe ของ FC ยังทำงานตามที่ตั้งไว้ |
| 7 | ดูนานกว่า 30 วิ ขณะ override ค้าง | เซอร์โวไม่กระตุก (loop ส่งซ้ำ 0.5 วิ ไม่ขาด) |

> **ข้อ 3 กับ 6 เป็นข้อที่พลาดไม่ได้** — ถ้าข้อ 3 ไม่ผ่าน แปลว่ามีการ override
> คันบังคับหลุดไป **ห้ามบินเด็ดขาด** ให้แจ้งมาทันที

## 11. ไฟล์ที่แตะรอบนี้

**Backend (Go)**
- `proto/swarmgod/v1/telemetry.proto` — +datalink (rssi/link_quality/drop_rate)
  +servo output (ch7/ch8) +**RC input (rc_ch7/rc_ch8/rc_valid)**
- `proto/swarmgod/v1/command.proto` — +`channel` ใน ServoRequest
- `internal/fleet/drone.go` — RADIO_STATUS/heartbeat/drop rate, RC_CHANNELS,
  SERVO_OUTPUT_RAW, linkQuality, **RC override + loop ส่งซ้ำ**
- `internal/command/service.go` — `Servo()` / `ServoOff()` / `ServoRelease()`
- `internal/api/server.go` — handler `Servo` (เดิมไม่มี)
- `internal/fleet/servo_link_test.go` · `internal/fleet/rcoverride_test.go` — ใหม่

**Frontend (Python)**
- `core/grpc_client.py` — servo_set/release/reset
- `core/logo_store.py` — ใหม่ (ลอจิกล้วน)
- `widgets/logo_dialog.py` — ใหม่
- `widgets/fleet_item.py` — ป้าย A/B, datalink N/A
- `widgets/scan_dialog.py` — checkbox + select all + multi-connect
- `widgets/takeoff_panel.py` — เอาไอคอนออกจาก TAKE OFF
- `app.py` — ปุ่ม A/B, auto-GUIDED, mode drop animation, logo row, reserved IDs
- `tests/test_servo_and_ui.py` — ใหม่ (29 เคส)

**ยังไม่ได้ยืนยันกับ SITL/โดรนจริง** — เทสทั้งหมดเป็น headless mock
โดยเฉพาะ **servo A/B ต้องลองกับ FC จริงที่ตั้ง `SERVO7/8_FUNCTION` แล้ว**
