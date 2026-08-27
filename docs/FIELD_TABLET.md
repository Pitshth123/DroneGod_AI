# FIELD TABLET — เข้าคอกพิตผ่านบราวเซอร์บน LAN หน้างาน

> สถานะ: **เฟส 1–3 ทำแล้ว** (25 สิงหาคม 2026) · เหลือลองบนแท็บเล็ตจริงในวง LAN
> เฟส 3 (หน้าเว็บใหม่ + WAYPOINT/SWARM/MOVE/WAVE): [FIELD_TABLET_V2.md](FIELD_TABLET_V2.md)
> ขอบเขต: LAN หน้างานเท่านั้น (ไม่ออกอินเทอร์เน็ต) · Python Cockpit เท่านั้น · **ไม่แตะ Go core / .proto**
>
> ไฟล์: `frontend/swarmgod_gui/core/field_server.py` · `core/web_bridge.py`
> · `widgets/field_dialog.py` · `assets/tablet.html` · `app.py` (`_dispatch_web`)
> เทสต์: `tests/test_field_server.py` · `test_field_control.py`
> · `test_field_integration.py` · `test_field_phase3.py`

---

## 0. TL;DR

Desktop cockpit ที่รันอยู่แล้ว เปิด HTTP server บน LAN เพิ่ม เพื่อให้ tablet/มือถือในวง WiFi เดียวกัน
เปิดบราวเซอร์เข้ามาดูและสั่งงานได้ **ระหว่างเดินดูหน้างาน**

- Desktop ยังเป็นเครื่องหลักเสมอ — tablet คือส่วนเสริม ไม่ใช่ตัวแทน
- สั่งงานได้ **ทีละเครื่องเดียว** (PILOT token) ที่เหลือเป็น VIEWER
- **ทุกคนกด HOLD/STOP / E-STOP / ยกเลิก WAVE ได้เสมอ** ไม่ต้องถือ token
- คำสั่ง `RcMove` / `SetYaw` ดิบ **ถูกปฏิเสธที่เซิร์ฟเวอร์เสมอ**
- แท็บ MOVE บนเว็บเป็น **ข้อยกเว้นที่ตั้งใจ** (ผู้ใช้ขอ) — มี deadman + ปลดล็อก + เพดาน 3 m/s
  ดู [FIELD_TABLET_V2.md §0.1](FIELD_TABLET_V2.md)

---

## 1. ทำไมต้องฝังใน process เดิม ไม่แยก process

Cockpit ที่รันอยู่มี state สดครบแล้ว: selection, groups, waypoints, wave, การเชื่อมต่อ mTLS กับ core
ถ้าแยก process ต้อง sync state ข้าม process (ยาก + แหล่งบั๊ก) และต้องต่อ core ซ้ำอีกเส้น

ฝังใน process เดิม → **tablet เห็นสิ่งเดียวกับ desktop ฟรี** และคำสั่งวิ่งผ่านทางเดิมทั้งหมด

```
                    ┌─ Qt UI (desktop)  ─┐
Browser ──HTTP/SSE──┤                    ├── _selected_or_all() ──gRPC/mTLS──> Go core ──> โดรน
 (tablet, LAN)      └─ Web server (SSE) ─┘         ▲                              ▲
                                          chokepoint เดียวกัน            E-STOP local + RC TX
                                          safety envelope + audit         (authoritative เสมอ)
```

---

## 2. สิ่งที่มีอยู่แล้ว (ไม่ต้องเขียนใหม่)

| ของที่มี | ใช้ทำอะไร |
|---|---|
| `core/tile_cache.py` → `TileServer(ThreadingHTTPServer)` | เป็น HTTP server อยู่แล้ว เสิร์ฟ `map.html` + tiles → **ต่อยอดตัวนี้** |
| `assets/map.html`, `assets/map3d.html`, `assets/map3d/terrain.js` | หน้าแผนที่พร้อมใช้ ไม่ต้องแก้แกน |
| `core/waypoint_logic.py` `swarm_logic.py` `group_store.py` `ip_store.py` `settings_io.py` | ไม่มี Qt — reuse ได้ 100% |
| `app.py:_on_telemetry()` → `_field_push()` | ทางเข้า telemetry เดียว → จุด mirror ไป SSE |
| `app.py:_selected_or_all()` | chokepoint คำสั่ง → คำสั่งจากเว็บต้องผ่านตรงนี้ |

**ไม่เพิ่ม dependency**: ใช้ SSE (`text/event-stream`) บน `ThreadingHTTPServer` เดิม
telemetry เป็น push ทางเดียว (SSE) + คำสั่งเป็น POST — ไม่ต้องใช้ WebSocket/FastAPI/aiohttp

---

## 3. ความปลอดภัย — กฎที่ห้ามละเมิด

### 3.1 กฎเหล็ก 6 ข้อ

1. **Server bind `127.0.0.1` เป็นค่าเริ่มต้นเสมอ** — เปิด LAN ต้องกดเปิดเอง (opt-in) และมีไฟแสดงสถานะชัดใน desktop UI ว่าตอนนี้ LAN เปิดอยู่
2. **E-STOP บนเว็บไม่ใช่ E-STOP จริง** — E-STOP ที่ desktop + RC transmitter คือของจริงเสมอ ห้าม UI เว็บทำให้ผู้ใช้เข้าใจว่าแทนกันได้
3. **ห้ามเปิดชื่อ RPC ดิบ `RcMove` / `SetYaw` ผ่านเว็บ** — ยิงตรงยังถูกปฏิเสธที่ allowlist
   แท็บ MOVE (`move`/`move_stop`) เป็นข้อยกเว้นที่ตั้งใจ มีตัวกันครบใน V2 §0.1
   ถ้าจะปิด: ทำให้ `MOVE_COMMANDS` ว่าง แล้วหน้าเว็บซ่อนแท็บเอง
4. **คำสั่งจากเว็บต้องผ่าน `_selected_or_all()`** เหมือนกดจาก desktop — ห้ามยิง gRPC ตรงจาก HTTP handler (ไม่งั้นหลุด safety envelope + audit)
5. **HTTP handler thread ห้ามแตะ Qt object** — ต้อง marshal เข้า Qt main thread ผ่าน `pyqtSignal` เท่านั้น (ดู §6)
6. **การคืน/ย้าย PILOT token ต้องไม่สั่งอะไรโดรนทั้งสิ้น** — ย้ายแค่ "สิทธิ์สั่งคำสั่งใหม่" โดรนทำสิ่งที่ทำอยู่ต่อไป

### 3.2 สิทธิ์แบบไม่สมมาตร (สำคัญที่สุด)

| คำสั่ง | PILOT | VIEWER | เหตุผล |
|---|---|---|---|
| `HOLD` / `E-STOP` / `move_stop` / `wave_cancel` | ✅ | ✅ **ได้เสมอ** | หยุดคือทิศทางที่ปลอดภัยเสมอ กดพลาด = ภารกิจช้า |
| `ARM` `TAKEOFF` `GOTO` `WAYPOINT` `RTL` `LAND` `SERVO` `SWARM` `WAVE` `select` | ✅ | ❌ | กดพลาด = คนเจ็บ / เปลี่ยนเป้า |
| `move` (แท็บ MOVE) | ✅ + ปลดล็อก + deadman | ❌ | ข้อยกเว้น V2 — viewer ยิงตรงได้ 403 |
| ชื่อ RPC ดิบ `RcMove` `SetYaw` | ❌ | ❌ | ไม่ได้อยู่ใน allowlist |

> **ห้ามเอา token ไปกั้นทิศทางที่ปลอดภัย** — VIEWER ต้องกดหยุดได้เสมอ

### 3.3 Auth บน LAN

- **PIN pairing 6 หลัก** สุ่มใหม่ทุกครั้งที่เปิด LAN แสดงบน desktop → พิมพ์บน tablet ครั้งเดียว
- ผ่านแล้วออก session cookie ชื่อ `swarmgod_field` — **HttpOnly + SameSite=Strict**
  (สคริปต์หน้าเว็บอ่านไม่ได้ ไม่ใช่ `localStorage` ตามร่างเดิม)
- PIN หมดอายุใน 5 นาที หรือเมื่อปิด LAN · เดาผิดครบ 5 ครั้ง PIN ตายทันที
- **ปราการจริงคือ WiFi hotspot WPA2** — PIN คือ defense in depth ไม่ใช่ชั้นเดียว
- Desktop มีปุ่ม **ตัดทุกเครื่อง** (kick all) — ปิด LAN ก็เตะทุก session ด้วย

---

## 4. PILOT token — ใครสั่งได้

### 4.1 กติกา

- มีผู้ถือ token **ได้ทีละหนึ่งเท่านั้น** ตลอดเวลา
- ตอนเปิดแอป: **desktop ถือ**
- tablet เครื่องแรกที่กดขอ → **ได้เลย** (auto-grant) desktop ขึ้นแบนเนอร์ว่าตอนนี้ควบคุมอยู่ที่เครื่องไหน
- เครื่องถัดไปที่กดขอ → เป็น VIEWER, ขอ handoff ได้ ผู้ถืออยู่ต้องกดอนุมัติ
- **Desktop ยึดคืนได้ตลอดเวลา ปุ่มเดียว ไม่ต้องขออนุมัติ** — เครื่องที่มี E-STOP และคนคุมอยู่ต้องชนะเสมอ

### 4.2 Heartbeat (ข้อนี้ขาดไม่ได้)

tablet ที่ถือ token ต้องส่ง heartbeat ทุก **2 วินาที**
ขาด **3 ครั้ง (6 วินาที)** → token **เด้งกลับ desktop อัตโนมัติ** + ขึ้นแบนเนอร์

เหตุผล: เดินออกนอกระยะ WiFi แล้ว token ค้างอยู่ที่ tablet = สั่งอะไรไม่ได้จากที่ไหนเลย
คืน token ต้อง **ไม่กระทบการบิน** (กฎ §3.1 ข้อ 6)

### 4.3 UI ต้องบอกสถานะให้ชัดแบบไม่กำกวม

tablet ต้องแยก PILOT / VIEWER ด้วยสีและข้อความขนาดใหญ่ที่มองปราดเดียวรู้
**ห้ามให้ผู้ใช้เข้าใจผิดว่าตัวเองคุมอยู่ทั้งที่เป็น viewer** — นี่คือคุณสมบัติด้านความปลอดภัย ไม่ใช่แค่ UX

---

## 5. Telemetry ผ่าน SSE

- endpoint: `GET /api/events` → `text/event-stream`
- **ต้อง throttle**: 5 ลำ × 10Hz = 50 แพ็กเก็ต/วิ (ดูคอมเมนต์ `app.py:294` ที่เตือนเรื่องนี้ไว้แล้ว)
  → รวบส่งเป็น snapshot รวม **5–10Hz** ไม่ใช่ต่อแพ็กเก็ต
- ส่งเฉพาะที่ต้องใช้: `drone_id, lat, lon, alt_rel, hdg, spd, batt, mode, armed, link`
- ต่อไม่ติด/หลุด → tablet ต้องขึ้นสถานะ **ชัดเจนว่าข้อมูลเก่า** พร้อมอายุข้อมูลเป็นวินาที
  (ข้อมูลค้างที่ดูเหมือนสดคืออันตราย)

---

## 6. Thread safety — จุดที่พลาดแล้วพัง

`ThreadingHTTPServer` เรียก handler บน thread อื่น **Qt object ไม่ thread-safe**

```python
# core/web_bridge.py
class WebBridge(QObject):
    command = pyqtSignal(str, str, dict)   # (session, action, params)
    # HTTP thread: emit เท่านั้น
    # Qt main thread: _on_web_command → _dispatch_web → เมธอดเดิมใน app.py
```

- HTTP handler: ตรวจ token/สิทธิ์ → `emit` → **จบหน้าที่**
- Qt main thread: รับ signal → เรียกเมธอดเดิม (`_do_takeoff`, `_wp_execute`, ...) → ผ่าน `_selected_or_all()` ตามปกติ
- ผลลัพธ์ส่งกลับ tablet ผ่าน SSE (ไม่ block HTTP response รอ gRPC)

---

## 7. Audit

ทุกคำสั่งจากเว็บถูก `_log` ใน Mission Log หมวด COMMAND เป็น
`tablet[<session 8 ตัว>] <action> → ok|ปฏิเสธ · <ข้อความ>`
ต้องตอบได้ว่า **คำสั่งนี้มาจากแท็บเล็ต** เมื่อมาสอบย้อนหลัง

---

## 8. แบ่งเฟส

### เฟส 1 — VIEWER อย่างเดียว ✅ ทำแล้ว
- `TileServer` เพิ่ม `/tablet` (หน้าใหม่) + `/events` (SSE)
- ธง `--lan` / ปุ่มใน UI เปิด bind LAN (ค่าเริ่มต้นยัง `127.0.0.1`)
- PIN pairing + session token
- แผนที่ + ตำแหน่งโดรน + fleet + สถานะ **ไม่มีปุ่มสั่งการเลยแม้แต่ปุ่มเดียว**
- **ความเสี่ยงเพิ่มเกือบศูนย์** เพราะไม่มี command path ใหม่
- เทสต์: assert ว่า **ไม่มี** endpoint ไหนไปถึง command RPC ได้

### เฟส 2 — PILOT token + คำสั่งระดับ mission ✅ ทำแล้ว
- token + heartbeat + desktop reclaim + handoff
- `WebBridge` + marshal เข้า Qt main thread
- เปิดคำสั่งตามตาราง §3.2 เท่านั้น (whitelist ฝั่ง server — **ห้าม blacklist**)
- VIEWER กด HOLD/STOP ได้
- log แยก `tablet[<session>]` ใน Mission Log

### เฟส 3 — WAYPOINT / SWARM / MOVEMENT (+ WAVE, เลือกหลายลำ) ✅ ทำแล้ว
ดูรายละเอียดและการยืนยันบนแท็บเล็ตใน [FIELD_TABLET_V2.md](FIELD_TABLET_V2.md)
groups/wave จาก tablet, แก้ waypoint บน tablet — ทำแล้วใน V2
TLS ยังไม่ทำ (LAN + WPA2 + PIN พอสำหรับหน้างาน)

---

## 8.1 บั๊กที่เจอตอนทำ (อย่าให้กลับมาอีก — มีเทสต์คุมทุกข้อแล้ว)

1. **`socketserver.shutdown()` บล็อกถาวรถ้า `serve_forever()` ไม่เคยรัน**
   ปุ่มปิด LAN เรียกจาก Qt main thread → คอกพิตค้างทั้งตัว
2. **`allow_reuse_address` บน Windows แย่งพอร์ตที่มีคนใช้อยู่ได้**
   เปิดคอกพิตซ้อนแล้วตัวหลังดูเหมือนสำเร็จ แต่คำขอยังวิ่งไปหาตัวเก่า
   → แท็บเล็ตคุยกับคอกพิตคนละตัวที่คุมโดรนคนละชุด **โดยไม่มีอะไรฟ้อง**
   แก้: ปิด `allow_reuse_address` บน Windows ให้ bind ซ้ำล้มเหลวเสียงดัง
3. **`heartbeat` ถูกนับเป็น "สิทธิ์เปลี่ยน"** → คอกพิตวาดแบนเนอร์ใหม่ทุก 2 วินาที
   แก้: เทียบ `ControlToken.gen` ก่อน/หลัง แล้วแจ้งเฉพาะตอนขยับจริง
4. **สายขาดกลางคันถูก log เป็น error** — บราวเซอร์ยกเลิก tile ทุกครั้งที่เลื่อนแผนที่
   traceback ถล่ม console จนกลบข้อความสำคัญ แก้ด้วย `handle_error()`
5. **เทสต์ที่ไม่เรียก `deleteLater()`** ทำให้หน้าต่างค้างสะสมจนสร้าง
   `GroundStation` ตัวที่ 3 ไม่ได้ (แขวนทั้ง process ระดับที่ watchdog thread
   ไม่ได้รันด้วยซ้ำ) — ใช้แพตเทิร์นเดียวกับเทสต์อื่นในรีโป

---

## 9. ข้อห้าม (สรุป)

- ❌ แตะ Go core หรือ `.proto`
- ❌ bind LAN เป็นค่าเริ่มต้น
- ❌ เปิดชื่อ RPC ดิบ `RcMove` / `SetYaw` ผ่านเว็บ (แท็บ MOVE มีตัวกันแยก — ดู V2 §0.1)
- ❌ ยิง gRPC ตรงจาก HTTP handler (ต้องผ่าน `_selected_or_all()`)
- ❌ แตะ Qt object จาก HTTP thread
- ❌ ให้ VIEWER กด HOLD/STOP ไม่ได้
- ❌ ให้ token ค้างที่เครื่องที่หลุดการเชื่อมต่อ
- ❌ ทำให้ UI ดูเหมือนคุมอยู่ทั้งที่เป็น viewer
- ❌ ออกอินเทอร์เน็ต / port forward — สเปกนี้คือ LAN หน้างานเท่านั้น

---

## 10. เช็คลิสต์เทสต์

ทำใน `test_field_server.py` · `test_field_control.py` · `test_field_integration.py` · `test_field_phase3.py`

**เฟส 1**
- [x] ค่าเริ่มต้นไม่มี listener — เปิด LAN ต้องกดเอง (เทสต์ผูก `127.0.0.1` เพื่อไม่เปิดออก LAN)
- [x] เปิด LAN แล้วเข้าได้ และ desktop แสดงสถานะว่าเปิดอยู่
- [x] ไม่มี PIN → เข้าไม่ได้ / PIN ผิด → ปฏิเสธ / PIN หมดอายุ → ปฏิเสธ / เดาครบ 5 ครั้ง PIN ตาย
- [x] ไม่มี endpoint ใดไปถึง command RPC โดยไม่ผ่าน allowlist + `_dispatch_web`
- [x] path traversal ถูกบล็อก (ทดสอบด้วย raw socket ไม่ใช่ urllib)
- [x] telemetry throttle ไม่เกินเพดานแม้ยิงถี่
- [x] snapshot มี `age` ต่อลำ · หน้าเว็บขึ้นป้ายข้อมูลค้างเมื่ออายุ > 3 วิ

**เฟส 2**
- [x] มีผู้ถือ token ได้ทีละหนึ่งเท่านั้น
- [x] tablet เครื่องแรกขอ → ได้ / เครื่องที่สอง → เป็น viewer
- [x] desktop ยึดคืนได้ทันทีโดยไม่ต้องอนุมัติ
- [x] heartbeat ขาด 6 วิ → token กลับ desktop (คืนสิทธิ์อย่างเดียว ไม่สั่งโดรน)
- [x] VIEWER กด HOLD ได้ / กด TAKEOFF ไม่ได้ (server ปฏิเสธ ไม่ใช่แค่ซ่อนปุ่ม)
- [x] ชื่อ RPC ดิบ `RcMove` / `SetYaw` ถูกปฏิเสธที่ server แม้ยิงตรง
- [x] คำสั่งจากเว็บโผล่ใน Mission Log เป็น `tablet[<session>] …`
- [x] คำสั่งจากเว็บผ่าน `_selected_or_all()` จริง (mock แล้ว assert)
- [x] ข้อผิดพลาดในคำสั่งไม่ฆ่า Qt loop

**เฟส 3** — ดู [FIELD_TABLET_V2.md](FIELD_TABLET_V2.md) §3.5

---

## 11. ที่ยังไม่ทำในสเปกนี้

- ลองบนแท็บเล็ตจริงในวง LAN (ลื่นไหม · ปุ่มโดนนิ้วง่ายไหมกลางแดด)
- ออกอินเทอร์เน็ต / คุมทางไกล — คนละ threat model ต้องเขียนสเปกใหม่
- TLS: LAN + WPA2 + PIN พอสำหรับหน้างาน ถ้าจะใส่ TLS ต้องแก้เรื่อง self-signed cert บน tablet (บราวเซอร์เตือน) — ไว้เฟสหลัง
- กำหนดกลุ่มโดรนจากแท็บเล็ต (ตอนนี้เลือกกลุ่มที่มีอยู่แล้วได้ ยังย้ายลำข้ามกลุ่มไม่ได้)
