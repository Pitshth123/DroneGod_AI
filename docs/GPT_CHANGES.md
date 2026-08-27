# GPT Changes

## 25 สิงหาคม 2026 — Pre-flight Checklist simplification

GPT ปรับระบบ Checklist ก่อนบินจริงให้ใช้งานง่ายขึ้นและเห็นสถานะการติ๊กชัดเจนขึ้น

### ไฟล์ที่แก้
- `docs/REAL_FLIGHT_CHECKLIST.md`
- `frontend/swarmgod_gui/widgets/preflight_dialog.py`
- `frontend/tests/test_preflight.py`

### สิ่งที่เปลี่ยน
1. ลด Checklist จากหลายสิบข้อเหลือ 8 ข้อที่ผู้ควบคุมต้องตรวจด้วยตัวเองจริง
2. ตัดรายการที่ SYSTEM TEST ตรวจแทนได้ เช่น GPS, telemetry, battery, mTLS, token, geofence, separation, Head และ command path
3. เปลี่ยน checkbox ให้ใช้ native indicator ของ Qt/Windows เพื่อให้เห็นเครื่องหมายติ๊กจริง ไม่ใช่แค่พื้นหลังเปลี่ยนสี
4. Progress แสดง `0/8` ถึง `8/8` และครบทุกข้อจึงเป็น CHECKLIST PASS
5. Checklist ไม่โหลดสถานะเก่าจากไฟล์บนดิสก์อีก เพื่อไม่ให้การตรวจของเที่ยวบินก่อนหน้าถูกนำมาใช้กับเที่ยวบินใหม่
6. อัปเดต automated test ให้คาดหวัง Checklist ใหม่ 1 หมวด 8 รายการ

### เหตุผล
Checklist เดิมยาวเกินไป ซ้ำกับ SYSTEM TEST และ checkbox แบบ custom QSS ทำให้ผู้ใช้มองไม่เห็นว่าติ๊กสำเร็จ จึงปรับให้แยกหน้าที่ชัดเจน: เครื่องตรวจสิ่งที่ตรวจอัตโนมัติได้ ส่วนคนยืนยันเฉพาะสิ่งที่ต้องตรวจหน้างาน

## 2026-08-25 — Checklist crash fix (GPT)
- อาการ: กดปุ่ม Checklist แล้วโปรแกรมปิด/เด้งทันที
- สาเหตุ: `ChecklistDialog.__init__()` อ้าง `self._session_checked` แต่ยังไม่ได้ประกาศ `_session_checked` ที่ระดับ class ทำให้เกิด `AttributeError` ตอนสร้าง dialog
- แก้ `frontend/swarmgod_gui/widgets/preflight_dialog.py`: เพิ่ม `ChecklistDialog._session_checked = {}` เพื่อเก็บสถานะเฉพาะ session และเริ่มใหม่เมื่อเปิดโปรแกรมใหม่
- แก้ `frontend/swarmgod_gui/app.py`: เพิ่ม guard รอบการสร้าง ChecklistDialog เพื่อให้ error ใน dialog แสดง toast/log แทนการทำให้ทั้งโปรแกรมเด้ง
- เพิ่ม regression test ใน `frontend/tests/test_preflight.py`: เปิด Checklist ด้วย fresh session state ต้องได้ 0/8 และสร้าง checkbox ครบ 8 ตัว
- ผลทดสอบ: `python -m pytest tests/test_preflight.py -q` ผ่าน 40 tests
