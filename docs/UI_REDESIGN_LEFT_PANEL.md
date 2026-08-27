# UI Redesign — การ์ดโดรน (แผงซ้าย) ตามภาพอ้างอิง

> **สถานะเอกสารนี้**: แบบร่าง (draft spec) เท่านั้น — **ยังไม่ได้แก้โค้ดจริง**
> รอ user รีวิว/คอนเฟิร์มก่อนเริ่ม implement
>
> **กติกาที่ต้องยึดตลอดการ implement (ผู้ใช้ระบุไว้ชัดเจน)**:
> 1. ฟังก์ชัน/ปุ่ม/ช่องกรอกทั้งหมดที่มีอยู่ตอนนี้ **ห้ามตัดออกแม้แต่อันเดียว** — ต่อให้ภาพ
>    อ้างอิงไม่โชว์ ก็แปลว่า "ยังไม่ได้ออกแบบส่วนนั้นในภาพ" ไม่ใช่ "ให้ตัดทิ้ง"
> 2. **PRE-FLIGHT SUMMARY** (แผงขวา) ต้องอยู่เหมือนเดิม — ภาพอ้างอิงไม่ได้ตัดออก แค่ไม่ได้
>    เอามาโชว์ในครอปนี้
> 3. **Banner แจ้งเตือน** (แถบใต้ topbar, persistent ตอน REMOTE / บอกเหตุผล Head บล็อก)
>    อยู่เหมือนเดิมทุกจุด ไม่แตะ
> 4. งานนี้คือ **redesign การ์ดโดรนซ้ายล่าง (SelectedDroneCard) เป็นหลัก** — ส่วนอื่น
>    (topbar, FLEET list, แผนที่, แผงขวา) ปรับแค่ระดับ "ให้เข้าธีมเดียวกับภาพ" เท่านั้น
>    ไม่ใช่ออกแบบใหม่ทั้งหมด

---

## 1. เป้าหมาย

ภาพอ้างอิงที่ผู้ใช้ส่งมาเป็นการ์ด "SELECTED DRONE" แบบกระชับ:

```
┌─────────────────────────────┐
│ D1  Drone 1          ✓READY │  ← header: id badge + ชื่อ + status pill
├─────────────────────────────┤
│ [icon] LINK QUALITY  BATTERY│
│        📶 100%       🔋100% │  ← สถานะลิงก์/แบต แบบไอคอน+เลข ขึ้นก่อนเลย
├─────────────────────────────┤
│ MODE      SPEED    HEADING  │
│ STABILIZE 0.0 m/s  000°     │  ← metrics แถวบน
│ GPS       ALTITUDE RSSI     │
│ 10 • 3D   -0.0 m   -12.6dBm │  ← metrics แถวล่าง
├─────────────────────────────┤
│      VIEW FULL TELEMETRY  › │  ← ทางลัดดูข้อมูลเต็ม (ถ้ามีอะไรเกินนี้)
└─────────────────────────────┘
```

จุดเด่นที่อยากได้จากภาพนี้:
- **สถานะ (status/telemetry) ขึ้นก่อนบนสุดของก้อน** ไม่ใช่จมอยู่กลาง/ท้ายการ์ดแบบตอนนี้
- Layout เป็นแถบ/กริดที่อ่านง่าย มีไอคอนประกอบ ไม่ใช่ตัวหนังสือแน่นๆ
- Header กระชับ: badge สี + ชื่อ + status pill บรรทัดเดียว

**แต่** การ์ดจริงของเรามีฟังก์ชันเยอะกว่าภาพ (IP config, ปุ่ม Quick Action, สี, พารามิเตอร์
ต่อลำ) — โจทย์คือเอา "โทน/ลำดับความสำคัญ" จากภาพมาปรับผังการ์ดเดิม ไม่ใช่ลอกภาพตรงๆ

---

## 2. โครงสร้างปัจจุบัน (ก่อนแก้) — `SelectedDroneCard`

ไฟล์: `frontend/swarmgod_gui/widgets/fleet_item.py` (class `SelectedDroneCard`, บรรทัด ~221-560)

ลำดับบนลงล่างตอนนี้:

1. **Header row** (`top` HBoxLayout): `lbl_title` ("VEHICLE") + `lbl_head` (★ HEAD, ซ่อนจนเป็นหัว) + `badge` (สถานะ OFFLINE/READY/ARMED/RTL·CLIMB ฯลฯ)
2. **Hero row** (`hero` HBoxLayout): รูปโดรน (`img`, 72×56) ซ้าย + คอลัมน์ขวา (`idcol`): ชื่อ (`lbl_name`) → แถว PING (`lbl_ping` + `btn_ping`) → LINK (`lbl_link`)
3. **IP section**: label "IP" → `ed_host` (เต็มแถว) → แถว PORT (`port_lab` + `ed_port` + `btn_apply_ip` "OK")
4. **Link action row**: `btn_connect` (CONN) / `btn_disconnect` (DISC) / `btn_delete` (DEL) — 3 ปุ่มเท่ากัน
5. **Color row**: label "color" + สวอทช์สีวงกลมเล็ก (`_color_btns`, จาก `COLOR_CHOICES`)
6. **Metrics grid** (`QGridLayout` 2×3): BATTERY, MODE, ALTITUDE / SPEED, HEADING, GPS (ผ่าน `_metric_cell()`, เก็บใน `self._vals[key]`)
7. **PARAMETERS**: label → แถว `spin_alt` (ALT ขึ้นบิน) + `spin_spacing` (SPACING ห่างลำหน้า)
8. **QUICK ACTIONS**: label → แถว1 `btn_arm`/`btn_disarm` → แถว2 `btn_rtl`/`btn_land`/`btn_hold` → แถว3 `btn_sethead`/`btn_estop`

**ปัญหาที่เจอ**: สถานะ/telemetry (ข้อ 6) จมอยู่เกือบท้ายการ์ด ต้องเลื่อนผ่าน IP +
สี ก่อนถึงจะเห็นแบต/โหมด/ความเร็ว ทั้งที่เป็นข้อมูลที่ต้องการเห็นไวสุด

---

## 3. โครงสร้างใหม่ (เสนอ) — จัดลำดับใหม่ตามที่สั่ง

> "ย้ายสถานะขึ้นมาบนก้อน แล้วตามด้วยไอพีการกำหนดค่า แล้วก็ปุ่ม Quick action ตาม"

ลำดับบนลงล่างใหม่:

```
1. HEADER          — id/ชื่อ/head badge/status pill (เหมือนเดิม แค่โทนเข้ม/กระชับขึ้น)
2. HERO ย่อ         — รูปโดรนเล็ก + ชื่อ + PING/LINK (คงไว้ แต่ลดพื้นที่)
3. ★ STATUS BLOCK   — (ย้ายขึ้นมาจากข้อ 6 เดิม) เมทริกซ์ BATTERY/MODE/ALTITUDE/
                       SPEED/HEADING/GPS แบบไอคอน+ตัวเลข ให้เด่นสุดในการ์ด
4. IP CONFIG        — (เดิม) IP host / PORT / OK / CONN-DISC-DEL — ย้ายมาอยู่รอง
                       จาก status ตามที่สั่ง
5. COLOR            — สวอทช์สีเลือกโดรน (คงไว้ พับเป็นแถบบางๆ ใต้ IP)
6. PARAMETERS       — ALT ขึ้นบิน / SPACING (คงไว้ตำแหน่งใกล้ Quick Action เพราะ
                       เป็นค่าที่ผูกกับคำสั่งบิน)
7. QUICK ACTIONS    — ARM/DISARM/RTL/LAND/HOLD/SET HEAD/E-STOP (คงไว้ท้ายสุด
                       ตามที่สั่ง "ตามด้วยปุ่ม Quick action")
```

### เหตุผลที่ตำแหน่งข้อ 5–6 ไม่ได้ขยับตามภาพ 100%
ภาพอ้างอิงไม่มี IP config / สี / พารามิเตอร์ต่อลำเลย (ภาพออกแบบมาแบบ "ดูอย่างเดียว"
ไม่มีการตั้งค่า) เราต้อง**เก็บของเดิมไว้ครบ** เลยแทรกมันกลับเข้าไปในตำแหน่งที่สมเหตุผลที่สุด
ตามคำสั่ง: สถานะ → IP → (สี พ่วงกับ IP เพราะเป็น "ตั้งค่าการเชื่อมต่อ/ระบุตัวตนโดรน" กลุ่มเดียวกัน)
→ พารามิเตอร์ (พ่วงกับ Quick Action เพราะเป็น "ตั้งค่าก่อนสั่งบิน" กลุ่มเดียวกัน) → Quick Action

---

## 4. รายละเอียดการปรับแต่ละส่วน

### 4.1 Header (แก้เล็กน้อย)
- คงโครงสร้างเดิม (`top` layout: title/head badge/status badge)
- เปลี่ยน `lbl_title` จาก "VEHICLE" (label คงที่) → โชว์ **ชื่อโดรนแทน** (เช่น "Drone 1")
  ให้เหมือนภาพ (ภาพไม่มีคำว่า VEHICLE คงที่ ใช้ชื่อจริงเป็นหัวเรื่องเลย)
  - ชื่อเดิม (`lbl_name`) ที่อยู่ใน hero row ข้อ 4.2 จะไม่ซ้ำซ้อน — ให้ย่อ/เอาออกจาก
    hero แล้วคงไว้แค่ header เท่านั้น (ลดความซ้ำซ้อน "ชื่อโดรน" ที่โผล่ 2 จุด)
    **หมายเหตุ**: นี่เป็นการจัดเรียงใหม่ ไม่ใช่การตัดฟังก์ชัน — `lbl_name` (attribute)
    ยังต้องมีอยู่และอัปเดตข้อความเหมือนเดิมทุกจุดที่เรียกใช้ (เช่น `_apply_badge`,
    `_select_drone`) แค่ย้ายตำแหน่งการวาดเท่านั้น
- status badge (`self.badge`) คงไว้ตำแหน่งขวาสุดเหมือนเดิม (READY/OFFLINE/ARMED/
  RTL·CLIMB ฯลฯ) — สไตล์ pill โค้งมน ตามภาพ (badge_style() เดิมใกล้เคียงอยู่แล้ว)
- ★ HEAD badge (`lbl_head`) คงตำแหน่งเดิม (ก่อน status badge, ชิดขวา, ซ่อนจนเป็นหัว)
  — **ยืนยันจากรอบก่อน**: ไม่ต้องย้าย/ไม่ใช่จุดที่มีปัญหา

### 4.2 Hero row → ย่อเหลือแค่รูป + PING/LINK
- รูปโดรน (`img`) คงไว้ แต่ลดขนาดลงเล็กน้อย (จาก 72×56 → ประมาณ 56×44) เพราะชื่อ
  ย้ายขึ้น header แล้ว พื้นที่แถวนี้จะโล่งขึ้น
- `lbl_ping` + `btn_ping` (ปุ่ม PING) คงไว้ทั้งคู่ ในคอลัมน์ขวาของรูป
- `lbl_link` (LINK --) คงไว้ใต้ ping row เหมือนเดิม

### 4.3 ★ Status Block (ของใหม่ที่ย้ายขึ้นมา — ใจกลางของงานนี้)
เอา metrics grid เดิม (ข้อ 6 ในผังเก่า) **ย้ายขึ้นมาเป็นบล็อกที่ 3** ทันทีหลัง hero row
เพิ่มเติมจากภาพอ้างอิง (ที่มีแค่ mode/speed/heading + gps/altitude/rssi):

- **แถวบนสุดของบล็อก (ใหม่ เพิ่มจากภาพ เพราะภาพมีแต่เราต้องคงของเดิมด้วย)**:
  LINK QUALITY (ไอคอนสัญญาณ + %) และ BATTERY (ไอคอนแบต + %) แบบ 2 คอลัมน์ใหญ่
  มีไอคอนประกอบ (คนละสไตล์กับ metric cell เดิมที่เป็นแค่ตัวเลข) — ใช้ข้อมูลเดิมที่มีอยู่
  แล้ว (`_vals["BATTERY"]`, เชื่อมกับ battery_pct/rssi ที่มีอยู่ใน telemetry handler)
- **กริด 2×3 เดิม** (`_metric_cell` เดิม 6 ช่อง: BATTERY, MODE, ALTITUDE, SPEED,
  HEADING, GPS) — คงไว้ทั้งหมด แต่จัดใหม่ให้ตรงภาพมากขึ้น: แถว1 = MODE/SPEED/HEADING,
  แถว2 = GPS/ALTITUDE/RSSI (RSSI เป็นของใหม่ที่ยังไม่มีช่องในกริดเดิม — ต้องเพิ่ม
  key "RSSI" เข้า `cells` list และมี field เก็บค่าเหมือน key อื่นๆ, ข้อมูล rssi
  มีอยู่แล้วใน telemetry object (`t.rssi`) แค่ยังไม่ได้ผูกเข้ากริดนี้)
  - **BATTERY จะซ้ำ 2 จุด** (แถวบนใหญ่ + ในกริด 2×3 เดิม) — พิจารณา 2 ทาง:
    - (แนะนำ) เอา BATTERY ออกจากกริด 2×3 เดิม เหลือแค่ 5 ช่อง (MODE/SPEED/HEADING/
      GPS/ALTITUDE) + เพิ่ม RSSI = 6 ช่องพอดี เพราะ BATTERY ย้ายไปโชว์ใหญ่ด้านบนแล้ว
      ไม่ต้องซ้ำ (ไม่ถือเป็นการตัดฟังก์ชัน — ข้อมูลเดียวกัน แค่ย้ายที่แสดงผล)
    - หรือคงไว้ทั้ง 2 จุดถ้าผู้ใช้อยากเห็นซ้ำเพื่อเน้นย้ำ — ไว้ถามตอน implement จริง
- ไอคอนที่ต้องเพิ่ม: สัญลักษณ์ signal-bars, battery, gps-pin (ใช้ระบบ `geo_icon()`
  เดิมใน `widgets/icons.py` ที่มีอยู่แล้ว หรือวาด QPainter เพิ่มถ้ายังไม่มีชุดที่ตรง)

### 4.4 IP Config (ย้ายลงมา — เนื้อหาเดิมทั้งหมด ไม่ตัดอะไร)
คงทุก widget เดิม 1:1 แค่ย้ายตำแหน่งมาอยู่หลัง Status Block:
- label "IP" + `ed_host`
- แถว PORT: `port_lab` + `ed_port` + `btn_apply_ip`
- แถว CONN/DISC/DEL: `btn_connect` / `btn_disconnect` / `btn_delete`

### 4.5 Color (ย้ายมาติดท้าย IP Config)
คงเดิมทั้งหมด (`_color_btns` จาก `COLOR_CHOICES`) — แค่ย้ายลงมาอยู่ใต้ IP แทนที่จะ
อยู่ระหว่าง IP กับ metrics เหมือนเดิม (เพราะ metrics ย้ายขึ้นไปแล้ว)

### 4.6 Parameters (คงตำแหน่งใกล้ Quick Actions เหมือนเดิม)
`spin_alt` + `spin_spacing` คงเดิมทุกอย่าง (range, default, signal `alt_changed`/
`spacing_changed`) — อยู่เหนือ Quick Actions ทันที เหมือนโครงสร้างเดิม

### 4.7 Quick Actions (ท้ายสุด — ตามคำสั่ง)
คงทุกปุ่มเดิม 1:1 ไม่ตัด/ไม่เพิ่ม:
- แถว1: `btn_arm` / `btn_disarm`
- แถว2: `btn_rtl` / `btn_land` / `btn_hold`
- แถว3: `btn_sethead` / `btn_estop`

### 4.8 "VIEW FULL TELEMETRY ›" (ของใหม่จากภาพ — เป็นตัวเลือก ไม่บังคับ)
ภาพมีลิงก์/ปุ่มท้ายการ์ด "VIEW FULL TELEMETRY" ซึ่งนัยว่ามีข้อมูลเพิ่มที่ซ่อนอยู่
เบื้องหลัง — เนื่องจากการ์ดของเราแสดงทุกอย่าง "ครบ" อยู่แล้วตามกติกาห้ามตัด จึงมี
2 ทางเลือกสำหรับ element นี้ (**รอผู้ใช้ตัดสินใจตอน implement**):

- **(ก) ไม่ต้องมี** — เพราะเราไม่ได้ซ่อนอะไรไว้ (การ์ดโชว์ครบทุกฟังก์ชันอยู่แล้ว
  ต่างจากภาพต้นแบบที่ตัดฟีเจอร์ออกไปเยอะ)
- **(ข) มีไว้เป็นปุ่ม "ขยาย/ย่อ" ส่วน IP+Color+Parameters** (accordion แบบเดียวกับ
  `AccordionSection` ที่ใช้ในแผงขวาอยู่แล้ว) — พับส่วนตั้งค่าที่ไม่ได้ใช้บ่อยไว้เริ่มต้น
  แบบพับ ให้การ์ดเริ่มต้นดูกระชับใกล้เคียงภาพ แต่กดขยายแล้วเห็นครบเหมือนเดิม —
  ไม่ตัดฟังก์ชัน แค่ซ่อน/โชว์ตามต้องการ (คล้ายที่ทำกับ SAFETY & SYSTEM /
  GEOFENCE·MAP / TACTICAL PLANNING / CV TRACK ในแผงขวาอยู่แล้ว — ใช้แพทเทิร์นเดียวกัน
  จะคุ้นเคยกับผู้ใช้)

**คำแนะนำ**: เลือก (ข) เพราะให้ผลลัพธ์ตรงกับความรู้สึกของภาพอ้างอิง (การ์ดเริ่มต้น
กระชับ) โดยไม่เสียฟังก์ชันไปเลย — สอดคล้องกับกติกาข้อ 1

---

## 5. ส่วนอื่นที่ "ไม่ใช่" เป้าหมายหลักของงานนี้ (คงไว้ ปรับแค่โทนสี/ระยะห่างเล็กน้อยถ้าจำเป็น)

### 5.1 Topbar
ปัจจุบันมี: hamburger, โลโก้, "SwarmGod Cockpit", pill CORE (สถานะ gRPC), pill LINK,
pill FLIGHT/SWARM/RTL/WAYPOINT (พร้อมแถบสีสถานะโหมดใต้ topbar), CONTROL switch
(UI/REMOTE), ปุ่ม "?" (help, สีฟ้าเด่น), ปุ่ม ⚙ (gear menu), นาฬิกา — **ใกล้เคียงภาพ
อ้างอิงอยู่แล้วเกือบทั้งหมด** ไม่ต้องแก้โครงสร้าง มีแค่จุดสังเกต:
- ภาพอ้างอิงมีไอคอน wifi/signal-bars ขวาสุดก่อนเวลา (ตอนนี้เรายังไม่มี) — เป็น
  ของเสริม ไม่บังคับตามกติกาข้อ 4 (ปรับแค่ระดับเข้าธีม ไม่ต้องมีครบทุกไอคอนในภาพ)

### 5.2 Banner แจ้งเตือน
ไม่แตะ — อยู่ตำแหน่งเดิม (ใต้ topbar, เว้นพื้นที่ 26px เสมอ, persistent ตอน REMOTE,
แดงตอน Head ถูกบล็อก) ตามที่ยืนยันไว้แล้วในรอบก่อน

### 5.3 FLEET list (`FleetItem`)
โครงสร้างปัจจุบันใกล้เคียงภาพอยู่แล้ว (D# badge สี, รูปย่อ, ชื่อ, สัญญาณ%, battery%,
status pill READY/ALERT) — คงไว้ทั้งหมด รวมถึงการแก้ล่าสุดที่เอาข้อความ "★ HEAD"
ออกจากแถวนี้แล้ว เหลือแค่สีปุ่มดาว (ไม่ต้องย้อนกลับ)

### 5.4 แผนที่ + พิกัดเมาส์ + Mission Log
ไม่อยู่ในสโคปงานนี้ คงเดิมทั้งหมด

### 5.5 แผงขวา (COMMAND & MISSION)
คงเดิมทั้งหมด **รวม PRE-FLIGHT SUMMARY ด้วย** — FORMATION picker (WEDGE/LINE/COLUMN/
DIAMOND/ECHELON), SPACING/ALTITUDE OFFSET/SPEED sliders, LEADER/HEAD dropdown,
PRE-FLIGHT SUMMARY box, ปุ่ม FORM UP/TAKE OFF/STOP/RETURN+LAND, accordion sections
(SAFETY & SYSTEM, GEOFENCE/MAP, TACTICAL PLANNING, CV TRACK) — ภาพอ้างอิงมีของกลุ่มนี้
เกือบครบอยู่แล้ว ไม่ต้องปรับโครงสร้าง

---

## 6. รายการฟังก์ชัน/widget ที่ต้อง "ยังอยู่ครบ" หลัง redesign (checklist กันตกหล่น)

จาก `SelectedDroneCard` (`fleet_item.py`):

- [ ] `lbl_title` / ชื่อโดรนที่ header (ย้ายตำแหน่งเนื้อหา ไม่ตัด)
- [ ] `lbl_head` (★ HEAD badge)
- [ ] `badge` (status pill)
- [ ] `img` (รูปโดรน)
- [ ] `lbl_name` (ชื่อ — ย้ายตำแหน่งการแสดงผลไป header ตามข้อ 4.1)
- [ ] `lbl_ping`, `btn_ping`
- [ ] `lbl_link`
- [ ] `ed_host`, `ed_port`, `btn_apply_ip`
- [ ] `btn_connect`, `btn_disconnect`, `btn_delete`
- [ ] `_color_btns` (สวอทช์สี, `COLOR_CHOICES`)
- [ ] metrics ทั้ง 6+1 ช่อง: BATTERY, MODE, ALTITUDE, SPEED, HEADING, GPS, **RSSI (ใหม่)**
- [ ] `spin_alt`, `spin_spacing` (+ label กำกับ)
- [ ] `btn_arm`, `btn_disarm`, `btn_rtl`, `btn_land`, `btn_hold`, `btn_sethead`, `btn_estop`
- [ ] `set_head()`, `set_rtl_phase()`, `_apply_badge()`, `set_quick_enabled()`, `set_params()`
  (เมธอดเดิมทั้งหมด — ปรับ implementation ตามตำแหน่ง widget ใหม่เท่านั้น ชื่อ/signature เดิม)
- [ ] signal ทั้งหมด (`head_req`, `estop_req`, `arm_req`, `disarm_req`, `alt_changed`,
  `spacing_changed`, `ping_req`, `connect_req`, `disconnect_req`, `delete_req`,
  `apply_ip_req`, `color_changed`, ฯลฯ)

จากส่วนอื่นของแอป (ไม่แตะ แต่ต้องยังทำงานได้หลังแก้):

- [ ] Banner (persistent REMOTE / red Head-block)
- [ ] Help modal (ปุ่ม "?")
- [ ] แถบสีสถานะโหมด topbar
- [ ] PRE-FLIGHT SUMMARY (แผงขวา)
- [ ] FLEET list ทั้งหมด (คงเดิม)
- [ ] Waypoint / Tactical / Geofence / CV Track ทั้งหมด (คงเดิม)

---

## 7. ขั้นตอนถัดไป (เมื่อจะเริ่ม implement จริง)

1. คอนเฟิร์มกับผู้ใช้: เอา BATTERY ออกจากกริด 2×3 หรือคงซ้ำ (ข้อ 4.3),
   เอา "VIEW FULL TELEMETRY" แบบ (ก) หรือ (ข) (ข้อ 4.8)
2. แก้ `SelectedDroneCard.__init__` ใน `fleet_item.py` — จัดลำดับ `v.addLayout(...)`/
   `v.addWidget(...)` ใหม่ตามข้อ 3 (ย้ายโค้ดเดิมเป็นหลัก ไม่ต้องเขียนใหม่ทั้งหมด)
3. เพิ่ม field RSSI เข้ากริด metrics + เดินสายค่าใน `app.py` (จุดที่อัปเดต
  `sel_card._vals[...]` จาก telemetry — หา method ที่อัปเดตการ์ดตอนนี้แล้วเพิ่มบรรทัดเดียว)
4. เพิ่มไอคอน signal/battery ใหญ่ (แถวบนสุดของ status block) — เช็คว่า `widgets/icons.py`
   มี geo_icon ที่ใช้ได้เลยหรือต้องวาดเพิ่ม
5. ถ้าเลือก (ข) ในข้อ 4.8 — ห่อ IP+Color+Parameters ด้วย `AccordionSection` เดิม
   (import จาก widgets/controls.py) แบบเดียวกับแผงขวา
6. รัน headless smoke test สร้าง `SelectedDroneCard` เปล่าๆ เช็คว่าไม่ throw +
   widget ทุกตัวยัง reference ได้ (`hasattr`) ครบตาม checklist ข้อ 6
7. รัน regression suite เดิมทั้งหมด (โดยเฉพาะ `test_ui_selection.py`,
   `test_head_rtlcfg_mode.py` ที่พึ่งพา `sel_card.*` เยอะ) ก่อนถือว่าเสร็จ
8. อัปเดต `docs/SWARM_CONTROL.md` + `docs/PROGRESS.md` ตามแพทเทิร์นเดิม
