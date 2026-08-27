"""
confirm.py — กล่องยืนยันสไตล์ดาร์ค (ใช้กับ Set Head / Emergency Stop)
คืน True เมื่อผู้ใช้กดยืนยัน
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox

from ..core.theme import T, rgba, FONT_FAMILY


def _style(box: QMessageBox, accent: str):
    box.setStyleSheet(
        f"QMessageBox {{ background:{T('panel')}; }}"
        f"QMessageBox QLabel {{ color:{T('text')}; font-family:{FONT_FAMILY};"
        f" font-size:13px; }}"
        f"QPushButton {{ background:{rgba('#ffffff', 0.06)};"
        f" border:1px solid {rgba('#ffffff', 0.14)}; border-radius:7px;"
        f" color:{T('text')}; padding:6px 16px; font-weight:600; min-width:78px; }}"
        f"QPushButton:hover {{ background:{rgba('#ffffff', 0.12)}; }}"
        f"QPushButton:default {{ background:{rgba(accent, 0.22)};"
        f" border:1px solid {rgba(accent, 0.5)}; color:{T('text')}; }}"
        f"QPushButton:default:hover {{ background:{rgba(accent, 0.34)}; }}")


_prewarmed = False


def prewarm(parent=None) -> None:
    """สร้าง native dialog window ทิ้ง 1 ใบตอนเปิดโปรแกรม (ไม่โชว์บนจอ)

    วัดบนเครื่องจริง (platform=windows): กล่องยืนยัน**ใบแรก**ของ process ใช้เวลา
    ~825 ms ใบถัดไปเหลือ 3-4 ms — ต้นทุนก้อนนั้นคือ Windows สร้าง HWND ของ dialog
    ครั้งแรก (theme/DWM/dialog machinery) ไม่ใช่ CSS ของเรา (วัดแยกได้ 1.3 ms)

    ถ้าไม่ทำล่วงหน้า ก้อนนี้จะไปตกที่ "การกดปุ่มคำสั่งครั้งแรกหลังเปิดโปรแกรม"
    ผู้ใช้เห็นเป็นอาการค้างเกือบ 1 วิ แล้วครั้งต่อ ๆ ไปเร็วปกติ

    `winId()` บังคับสร้าง native window โดยไม่ map ขึ้นจอ จึงไม่มีหน้าต่างวาบ
    """
    global _prewarmed
    if _prewarmed:
        return
    _prewarmed = True
    box = QMessageBox(parent)
    box.setWindowTitle(" ")
    box.setText(" ")
    box.setIcon(QMessageBox.Question)
    box.addButton("", QMessageBox.RejectRole)
    _style(box, T("accent"))
    box.winId()
    box.ensurePolished()
    box.deleteLater()


def confirm(parent, title: str, text: str, *,
            ok_text: str = "ตกลง", cancel_text: str = "ยกเลิก",
            accent: str = None, danger: bool = False) -> bool:
    """กล่องยืนยัน 1 ชั้น — คืน True เมื่อกดตกลง"""
    accent = accent or (T("red") if danger else T("accent"))
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    ok = box.addButton(ok_text, QMessageBox.AcceptRole)
    cancel = box.addButton(cancel_text, QMessageBox.RejectRole)
    box.setDefaultButton(cancel)   # ดีฟอลต์ = ยกเลิก กัน Enter เผลอยืนยัน
    box.setEscapeButton(cancel)
    _style(box, accent)
    box.exec_()
    return box.clickedButton() is ok


def confirm_twice(parent, title: str, text1: str, text2: str, *,
                  accent: str = None) -> bool:
    """Double-confirmation สำหรับคำสั่งอันตราย (เช่น STOP ALL) — ต้องผ่าน 2 ชั้น"""
    accent = accent or T("red")
    if not confirm(parent, title, text1, ok_text="ดำเนินการต่อ",
                   accent=accent, danger=True):
        return False
    return confirm(parent, title + " — ยืนยันอีกครั้ง", text2,
                   ok_text="ยืนยันหยุด", accent=accent, danger=True)
