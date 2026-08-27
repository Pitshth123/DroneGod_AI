# SwarmGod — Security Design (ความปลอดภัยของระบบ)

> โดรนติดอาวุธ/บินเหนือคน = ความผิดพลาดมีต้นทุนสูง เอกสารนี้กำหนดว่าเราป้องกันอะไรบ้าง
> หลักคิด: **defense in depth** (หลายชั้น) + **fail-safe** (พังแล้วต้องปลอดภัย)

---

## 1. Threat Model (เรากลัวอะไร)

| ภัยคุกคาม | ตัวอย่าง | ชั้นป้องกัน |
|-----------|---------|-------------|
| **คำสั่งอันตรายโดยพลาด** | กด TAKEOFF ผิดลำ, alt 500m | Safety envelope + confirm dialog |
| **UI ถูกดัดแปลง / bug** | frontend ส่ง alt ติดลบ | Core validate ซ้ำ (ไม่เชื่อ UI) |
| **แอบดัก/สั่งการ MAVLink** | attacker บน LAN spoof คำสั่ง | MAVLink signing (HMAC) |
| **แอบต่อ gRPC core** | process อื่นสั่งโดรน | mTLS + token + bind localhost |
| **Link ขาดกลางอากาศ** | WiFi หลุด | Failsafe: RTL/LAND อัตโนมัติ |
| **โดรนชนกัน** | 2 ลำ waypoint ทับ | Inter-drone distance check |
| **บินออกนอกเขต** | หลุด geofence | Geofence enforce ที่ core |
| **ไม่รู้ว่าใครสั่งอะไร** | สอบสวนอุบัติเหตุ | Append-only audit log |

---

## 2. ชั้นที่ 1 — ช่องทาง Frontend ↔ Core (gRPC)

- **Bind localhost เท่านั้น** (`127.0.0.1:50051`) โดย default — ไม่เปิดออก network
- **mTLS**: ทั้ง server และ client ต้องมี cert ที่เซ็นโดย CA เดียวกัน (`certs/`)
  → process แปลกปลอมต่อไม่ได้แม้อยู่เครื่องเดียวกัน
- **Production fail closed**: `SWARMGOD_PROFILE=production` จะไม่ยอม start ถ้า cert/CA โหลดไม่ได้,
  ไม่ได้ตั้ง `SWARMGOD_HOME_LOC=lat,lon,alt,heading`, ไม่ได้เปิด strict signing
  หรือ signing key อ่านไม่ได้/สั้นกว่า 32 ไบต์
- **Session token**: ทุก RPC ต้องแนบ bearer token (metadata) ที่ core ออกให้ตอน handshake
- **Idempotency**: คำสั่งที่มี `request_id` เดิมจะไม่ถูกส่งซ้ำจาก retry/double-click

## 3. ชั้นที่ 2 — Safety Envelope (บังคับที่ Core, UI ข้ามไม่ได้)

ทุก command วิ่งผ่าน `safety.Envelope.Check(cmd, droneState)` ก่อนส่งออก:

```
✓ Altitude limit      : 0 < alt ≤ MAX_ALT (default 120m, ตั้งได้)
✓ Distance from GCS    : ≤ MAX_RADIUS (default 500m)
✓ Geofence            : จุดเป้าต้องอยู่ในรั้ว (polygon)
✓ Battery gate        : ห้าม takeoff ถ้า batt < MIN_ARM_BATT (default 25%)
✓ GPS gate            : ห้าม arm/takeoff ถ้า gps_fix < 3D หรือ sat < 6
✓ Inter-drone spacing : goto/waypoint ต้องห่างลำอื่น ≥ MIN_SEPARATION (default 5m)
✓ Speed limit         : ≤ MAX_SPEED
✓ Armed precondition   : goto/rc ต้อง armed + mode ถูกต้องก่อน
```
ถ้าไม่ผ่าน → reject + reason กลับไป UI + เขียน audit (ไม่ส่งออกไปโดรน)

## 4. ชั้นที่ 3 — Dangerous Command Confirmation

คำสั่งกลุ่มเสี่ยงต้องมี **explicit confirm** (double-action) ก่อน core ยอมส่ง:
`KILL · DISARM-in-air · เปลี่ยน geofence · takeoff ทั้งฝูงพร้อมกัน`
UI แสดง dialog + core ต้องได้ flag `confirmed=true` ใน request (ไม่ใช่แค่ UI เด้ง)

## 5. ชั้นที่ 4 — MAVLink Signing (Core ↔ Drone)

- ใช้ **MAVLink 2.0 message signing** (HMAC-SHA256 + shared secret key) — กัน spoofing บน LAN/RF
- Key เก็บใน `certs/mavlink_key` (0600), ไม่ commit ลง git
- Reject packet ที่ signature ไม่ผ่าน (ป้องกัน replay/injection)
- อ้างอิงแนวทางจาก `GCS_1/docs/MAVLINK_SIGNING_PROPOSAL.md`

## 6. ชั้นที่ 5 — Failsafe (พังแล้วต้องปลอดภัย)

| เหตุการณ์ | การกระทำอัตโนมัติ |
|-----------|---------------------|
| ไม่ได้รับ telemetry > 3s | mark RECONNECTING + alarm |
| ไม่ได้รับ > 10s | ถือว่า link lost → trigger failsafe policy (RTL/LAND) |
| Core crash | โดรนใช้ FC-side failsafe (ตั้งค่า FS_* params ไว้) |
| Battery critical | บังคับ RTL/LAND + alarm |
| Command ไม่มี ACK ใน timeout | ถือว่าล้มเหลว, retry มีขอบเขต, แจ้ง user |

## 7. ชั้นที่ 6 — Audit Log (append-only)

- ทุก command + ผลลัพธ์ + เวลา → เขียน JSONL แบบ append-only ที่ core
- ไฟล์หมุนตามวัน, ไม่ลบย้อนหลัง, เก็บ `logs/audit/`
- Telemetry บันทึกแยก (CSV/parquet) สำหรับ post-flight analysis

## 8. Secrets & Config

- **ไม่ commit**: `certs/*.key`, `certs/mavlink_key`, session tokens → อยู่ใน `.gitignore`
- Config ผ่าน env / ไฟล์ `config.yaml` (ไม่ hardcode IP/port/limit ในโค้ด)
- Home/GCS สำหรับ radius และ swarm return อ่านจาก `SWARMGOD_HOME_LOC`; HIL/production ห้ามใช้ค่าตั้งต้น SITL
- `gen-certs.sh` สร้าง dev cert; production ต้องออก cert เอง

## 9. สิ่งที่ "จะไม่ทำ" (ขอบเขตความปลอดภัย)

- ไม่มีการเก็บ credential ของผู้ใช้ในระบบ
- ไม่เปิด core ออก public internet โดยไม่มี VPN/mTLS
- ไม่ยอมให้ UI ส่ง raw MAVLink ตรงข้าม safety envelope

---

**Checklist ก่อนบินจริง (pre-flight security):**
- [ ] certs ครบ + permission 0600
- [ ] MAVLink signing เปิด + key ตรงกับ FC
- [ ] geofence + alt limit + battery gate ตั้งค่าแล้ว
- [ ] FC-side failsafe params ตั้งแล้ว (FS_THR, FS_GCS, BATT_LOW)
- [ ] audit log เขียนได้ (disk เหลือพอ)
- [ ] ทดสอบ props-off และ tethered hover ผ่านก่อนปล่อยบินอิสระ
