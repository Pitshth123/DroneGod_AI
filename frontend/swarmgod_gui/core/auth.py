"""
auth.py — รหัสผ่านหน้าเข้าโปรแกรม

⚠️ ขอบเขตของสิ่งนี้: เป็น **ประตูกันคนกดเล่น** ไม่ใช่ระบบความปลอดภัยจริง
ใครที่เข้าถึงไฟล์โปรแกรมได้ ก็แก้/ข้ามได้ทั้งนั้น (โปรแกรมรันบนเครื่องผู้ใช้เอง)
ของจริงที่กันการสั่งโดรนคือ mTLS + token ที่ core (ดู docs/SECURITY.md)

เก็บเป็น SHA-256 ไม่ใช่ตัวเปล่า — ไม่ได้ทำให้ปลอดภัยขึ้นมาก แต่อย่างน้อย
เปิดไฟล์ดูแล้วไม่เห็นรหัสตรง ๆ

เปลี่ยนรหัสได้ 2 ทางโดยไม่ต้องแก้โค้ด (เรียงตามลำดับความสำคัญ):
  1. env  SWARMGOD_PASSCODE=<รหัสใหม่>
  2. ไฟล์ ~/.swarmgod/passcode  (บรรทัดเดียว, ใส่รหัสตรง ๆ)
"""
from __future__ import annotations

import hashlib
import os

# SHA-256 ของรหัสเริ่มต้นที่ผู้ใช้กำหนด
_DEFAULT_HASH = "b3c50838fe758e187c428a6490d6de846c35793552b778c10b431c9ade3a9f33"


def _sha(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _passcode_file() -> str:
    return os.path.join(os.path.expanduser("~"), ".swarmgod", "passcode")


def _expected_hash() -> str:
    """รหัสที่ระบบยอมรับ — env > ไฟล์ > ค่าเริ่มต้นในโค้ด"""
    env = os.getenv("SWARMGOD_PASSCODE", "").strip()
    if env:
        return _sha(env)
    try:
        with open(_passcode_file(), "r", encoding="utf-8") as f:
            code = f.read().strip()
        if code:
            return _sha(code)
    except OSError:
        pass
    return _DEFAULT_HASH


def verify(code: str) -> bool:
    """True = รหัสถูก"""
    return _sha((code or "").strip()) == _expected_hash()


def already_authed() -> bool:
    """cockpit ที่ถูกเปิดโดย launcher ไม่ต้องถามซ้ำ (launcher ถามไปแล้ว)"""
    return os.getenv("SWARMGOD_AUTHED") == "1"


def mark_authed(env: dict) -> dict:
    """ใส่ธงลง env ที่จะส่งต่อให้ process ลูก"""
    env["SWARMGOD_AUTHED"] = "1"
    return env
