"""
web_bridge.py — ส่งคำสั่งจาก HTTP thread เข้า Qt main thread อย่างปลอดภัย

`ThreadingHTTPServer` เรียก handler บน thread ของตัวเอง **Qt object ไม่ thread-safe**
แตะ widget/ส่ง signal ตรง ๆ จาก thread นั้นคือ race บนตัวคุมโดรน
(docs/FIELD_TABLET.md §6)

ทางเดินคำสั่ง:

    HTTP thread                 Qt main thread
    ───────────                 ──────────────
    ตรวจ token/allowlist/rate
    dispatch()  ──signal──►     _on_web_command()
    return "รับคำสั่งแล้ว"        เรียกเมธอดเดิมใน app.py
                                → _selected_or_all() → core

HTTP ตอบกลับทันทีว่า "รับไว้แล้ว" ไม่รอผลบินจริง ผลลัพธ์จริงไหลกลับไปทาง
telemetry/SSE ตามปกติ — ไม่งั้น HTTP thread จะค้างรอ gRPC และกิน slot
ของ SSE ไปด้วย
"""
from PyQt5.QtCore import QObject, pyqtSignal


class WebBridge(QObject):
    """ตัวกลาง thread เดียวที่เชื่อม field_server กับ GroundStation

    ตัวมันเอง**ไม่รู้จักคำสั่งใด ๆ** — แค่ส่งต่อ ผู้รับ signal (app.py) เป็นคน
    ตัดสินว่าจะ map ไปเมธอดไหน
    """

    command = pyqtSignal(str, str, dict)   # (session, action, params)
    control_changed = pyqtSignal()         # สิทธิ์ควบคุมย้ายมือ

    def control_moved(self):
        """เรียกจาก HTTP thread เช่นกัน — ต้องผ่าน signal ไม่ใช่แตะ widget ตรง"""
        self.control_changed.emit()

    def dispatch(self, session, action, params):
        """เรียกจาก HTTP thread เท่านั้น — emit แล้วจบหน้าที่ ไม่รอผล

        `pyqtSignal.emit()` ข้าม thread เป็น queued connection ให้อัตโนมัติ
        เมื่อผู้รับอยู่คนละ thread จึงปลอดภัย ต่างจากการเรียกเมธอดตรง ๆ
        """
        self.command.emit(str(session or ""), str(action or ""),
                          dict(params or {}))
        return True, "รับคำสั่งแล้ว"
