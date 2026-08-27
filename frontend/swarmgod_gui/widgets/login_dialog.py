"""
login_dialog.py — หน้าใส่รหัสก่อนเข้าโปรแกรม

ดีไซน์เข้าชุดกับ cockpit (ดาร์คหรู graphite) — โลโก้หน่วย + ช่องรหัสตรงกลาง
กด Enter ได้เลย · รหัสผิด = กรอบแดง + สั่น + ข้อความบอก (ไม่ทำ layout ขยับ)
"""
import os

from PyQt5.QtCore import (
    Qt, QEasingCurve, QPropertyAnimation, QRect, pyqtSignal,
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..core.theme import T, FONT_FAMILY, FONT_MONO, rgba, hairline
from ..core import auth

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")


class LoginDialog(QDialog):
    """คืน Accepted เมื่อใส่รหัสถูก · ปิดหน้าต่าง = Rejected (ไม่เข้าโปรแกรม)"""

    unlocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SwarmGod")
        self.setModal(True)
        self.setFixedSize(440, 520)
        # ไม่มีปุ่ม ? บนแถบหัวต่าง — ให้เหลือแค่ปิด
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        icon = os.path.join(_ASSETS, "logo.ico")
        if os.path.exists(icon):
            from PyQt5.QtGui import QIcon
            self.setWindowIcon(QIcon(icon))

        self.setStyleSheet(f"QDialog {{ background:{T('bg')}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)

        # ── การ์ดกลาง (ยกระดับจากพื้น + เงานุ่ม) ──
        self.card = QFrame(self)
        self.card.setObjectName("LoginCard")
        self.card.setStyleSheet(
            f"#LoginCard {{ background:{T('panel')};"
            f" border:1px solid {rgba('#ffffff', 0.11)}; border-radius:18px; }}")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(Qt.black)
        self.card.setGraphicsEffect(shadow)
        root.addWidget(self.card)

        v = QVBoxLayout(self.card)
        v.setContentsMargins(32, 34, 32, 30)
        v.setSpacing(0)

        # ── โลโก้ ──
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo_path = os.path.join(_ASSETS, "logo.png")
        if os.path.exists(logo_path):
            pm = QPixmap(logo_path)
            if not pm.isNull():
                logo.setPixmap(pm.scaled(84, 84, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        else:
            logo.setText("🛡")
            logo.setStyleSheet(f"font-size:52px; color:{T('accent')};")
        v.addWidget(logo)
        v.addSpacing(16)

        title = QLabel("SWARMGOD")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{T('text')}; font-family:{FONT_FAMILY}; font-size:26px;"
            f" font-weight:800; letter-spacing:5px;")
        v.addWidget(title)

        sub = QLabel("ระบบควบคุมฝูงโดรน")
        sub.setAlignment(Qt.AlignCenter)
        # ไม่ใส่ letter-spacing กับข้อความไทย — จะดันสระ/วรรณยุกต์หลุดจากพยัญชนะ
        sub.setStyleSheet(
            f"color:{T('faint')}; font-family:{FONT_FAMILY}; font-size:12.5px;")
        v.addWidget(sub)
        v.addSpacing(30)

        # เส้นคั่นบาง ๆ
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{hairline()};")
        v.addWidget(line)
        v.addSpacing(26)

        lbl = QLabel("รหัสผ่าน")
        lbl.setStyleSheet(
            f"color:{T('dim')}; font-family:{FONT_FAMILY}; font-size:12px;"
            f" font-weight:700;")
        v.addWidget(lbl)
        v.addSpacing(8)

        # ── ช่องรหัส ──
        self.ed = QLineEdit()
        self.ed.setEchoMode(QLineEdit.Password)
        self.ed.setAlignment(Qt.AlignCenter)
        self.ed.setMaxLength(32)
        self.ed.setPlaceholderText("• • • • •")
        self.ed.setFixedHeight(52)
        self._style_input(ok=True)
        self.ed.returnPressed.connect(self._try_unlock)
        self.ed.textEdited.connect(self._on_typing)
        v.addWidget(self.ed)

        # ── ข้อความ error — เว้นที่ไว้เสมอ ไม่ให้ layout ขยับตอนโผล่/หาย ──
        self.err = QLabel(" ")
        self.err.setAlignment(Qt.AlignCenter)
        self.err.setFixedHeight(22)
        self.err.setStyleSheet(
            f"color:{T('red')}; font-family:{FONT_FAMILY}; font-size:12px;"
            f" font-weight:600;")
        v.addWidget(self.err)
        v.addSpacing(6)

        # ── ปุ่มเข้าสู่ระบบ ──
        self.btn = QPushButton("เข้าสู่ระบบ")
        self.btn.setFixedHeight(48)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setStyleSheet(
            f"QPushButton {{ background:{T('accent')}; border:none;"
            f" border-radius:12px; color:#ffffff; font-family:{FONT_FAMILY};"
            f" font-size:16px; font-weight:800; }}"
            f"QPushButton:hover {{ background:{rgba(T('accent'), 0.86)}; }}"
            f"QPushButton:pressed {{ background:{rgba(T('accent'), 0.72)}; }}")
        self.btn.clicked.connect(self._try_unlock)
        v.addWidget(self.btn)

        v.addStretch(1)

        hint = QLabel("กด Enter เพื่อเข้าสู่ระบบ")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            f"color:{T('faint')}; font-family:{FONT_FAMILY}; font-size:11px;")
        v.addWidget(hint)

        self.ed.setFocus()

    # ── สไตล์ช่องรหัส: ปกติ / ผิด ──
    def _style_input(self, ok: bool):
        border = hairline() if ok else rgba(T("red"), 0.85)
        glow = T("accent") if ok else T("red")
        self.ed.setStyleSheet(
            f"QLineEdit {{ background:{T('panel2')}; border:1.5px solid {border};"
            f" border-radius:12px; color:{T('text')}; font-family:{FONT_MONO};"
            f" font-size:22px; letter-spacing:8px; padding:0 12px; }}"
            f"QLineEdit:focus {{ border:1.5px solid {glow};"
            f" background:{T('panel3')}; }}")

    def _on_typing(self, _text):
        """เริ่มพิมพ์ใหม่ = ล้างสถานะผิด ไม่ให้ค้างแดงทั้งที่กำลังแก้อยู่"""
        if self.err.text().strip():
            self.err.setText(" ")
            self._style_input(ok=True)

    def _try_unlock(self):
        if auth.verify(self.ed.text()):
            self.unlocked.emit()
            self.accept()
            return
        self.err.setText("รหัสผ่านไม่ถูกต้อง")
        self._style_input(ok=False)
        self.ed.selectAll()
        self._shake()

    def _shake(self):
        """สั่นการ์ดซ้าย-ขวาสั้น ๆ — feedback ที่รู้สึกได้ทันทีโดยไม่ต้องอ่าน"""
        start = self.card.geometry()
        anim = QPropertyAnimation(self.card, b"geometry", self)
        anim.setDuration(260)
        anim.setEasingCurve(QEasingCurve.OutQuad)
        for i, dx in enumerate((0, -11, 9, -6, 3, 0)):
            anim.setKeyValueAt(
                i / 5.0,
                QRect(start.x() + dx, start.y(), start.width(), start.height()))
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self._anim = anim  # กันโดน GC ระหว่างเล่น


def require_passcode(parent=None) -> bool:
    """แสดงหน้ารหัสผ่าน — True = ผ่าน, False = ผู้ใช้ปิดหน้าต่าง

    ข้ามให้เลยถ้า process นี้ถูกเปิดโดย launcher ที่ถามไปแล้ว
    """
    if auth.already_authed():
        return True
    dlg = LoginDialog(parent)
    return dlg.exec_() == QDialog.Accepted
