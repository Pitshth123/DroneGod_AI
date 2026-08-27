"""
logo_store.py — จัดการรูปโลโก้/สัญลักษณ์หน่วยที่โชว์บน top bar

ลอจิกล้วน ไม่มี Qt → เทสต์แบบ headless ได้ตรง ๆ (แนวเดียวกับ swarm_logic/waypoint_logic)

เก็บไฟล์จริงไว้ที่ ~/.swarmgod/logos/ (นอกโปรเจค) เพราะเป็นข้อมูลของผู้ใช้
ไม่ใช่ asset ของโค้ด — clone ใหม่แล้วไม่ต้องพกรูปไปด้วย
"""
import os
import shutil
import time

MAX_LOGOS = 5
_VALID_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}


def logo_dir() -> str:
    """โฟลเดอร์เก็บโลโก้ของผู้ใช้ (สร้างให้ถ้ายังไม่มี)"""
    d = os.path.join(os.path.expanduser("~"), ".swarmgod", "logos")
    os.makedirs(d, exist_ok=True)
    return d


def is_image(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in _VALID_EXT


def list_logos(directory: str = None) -> list:
    """คืน path รูปทั้งหมดเรียงตามลำดับที่เพิ่ม (ชื่อไฟล์ขึ้นต้นด้วย seq)

    เรียงด้วยชื่อไฟล์ = เรียงตามลำดับเพิ่ม เพราะตั้งชื่อเป็น NN_<ชื่อเดิม>
    รูปใหม่จึงไปต่อท้าย (ขยับไปทางขวาบนแถบ) ตามสเปก
    """
    d = directory or logo_dir()
    if not os.path.isdir(d):
        return []
    names = [n for n in os.listdir(d) if is_image(n)]
    names.sort()
    return [os.path.join(d, n) for n in names]


def can_add(directory: str = None) -> bool:
    return len(list_logos(directory)) < MAX_LOGOS


def add_logo(src: str, directory: str = None) -> str:
    """ก๊อปรูปเข้าโฟลเดอร์โลโก้ แล้วคืน path ปลายทาง

    raise ValueError ถ้าไฟล์ไม่ใช่รูป / ไม่มีจริง / ครบ 5 รูปแล้ว
    """
    if not src or not os.path.isfile(src):
        raise ValueError("ไม่พบไฟล์รูปที่เลือก")
    if not is_image(src):
        raise ValueError("รองรับเฉพาะไฟล์รูป (png/jpg/bmp/gif/webp)")
    d = directory or logo_dir()
    existing = list_logos(d)
    if len(existing) >= MAX_LOGOS:
        raise ValueError(f"ใส่รูปได้สูงสุด {MAX_LOGOS} รูป — ลบรูปเดิมออกก่อน")
    # seq จากจำนวนที่มี + timestamp กันชื่อชนเมื่อลบ/เพิ่มสลับกัน
    seq = len(existing) + 1
    base = os.path.basename(src)
    dst = os.path.join(d, f"{seq:02d}_{int(time.time())}_{base}")
    shutil.copy2(src, dst)
    return dst


def remove_logo(path: str) -> bool:
    """ลบรูปออก — คืน True ถ้าลบสำเร็จ"""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


def clear_logos(directory: str = None) -> int:
    n = 0
    for p in list_logos(directory):
        if remove_logo(p):
            n += 1
    return n
