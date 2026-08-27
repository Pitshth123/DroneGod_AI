"""
logo_dialog.py — ตั้งค่ารูปไอคอน/โลโก้บน top bar (สูงสุด 5 รูป)

เพิ่มรูปใหม่ = ต่อท้ายทางขวา · ลบได้รายรูป · เปลี่ยนแล้วแถบบนอัปเดตทันที
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog, QFrame,
)

from ..core.theme import T, rgba, hairline, ghost_btn, tinted_btn, filled_btn
from ..core import logo_store


class LogoSettingsDialog(QDialog):
    """จัดการรูปโลโก้ — emit changed() ทุกครั้งที่เพิ่ม/ลบ เพื่อให้ top bar วาดใหม่"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ตั้งค่ารูปไอคอน — Top bar")
        self.setModal(True)
        self.resize(560, 300)
        self.setStyleSheet(f"QDialog {{ background:{T('panel')}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        hint = QLabel(
            f"รูปจะเรียงจากซ้ายไปขวาบนแถบบน — เพิ่มรูปใหม่จะไปต่อทางขวา "
            f"(สูงสุด {logo_store.MAX_LOGOS} รูป)")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(hint)

        self.strip = QFrame()
        self.strip.setStyleSheet(
            f"background:{T('panel3')}; border:1px solid {hairline()}; border-radius:8px;")
        self.strip_row = QHBoxLayout(self.strip)
        self.strip_row.setContentsMargins(10, 10, 10, 10)
        self.strip_row.setSpacing(8)
        v.addWidget(self.strip, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        v.addWidget(self.lbl_count)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_add = QPushButton("+ เพิ่มรูป…")
        self.btn_add.setFixedHeight(32)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setStyleSheet(tinted_btn(T("green"), radius=7, font=11))
        self.btn_add.clicked.connect(self._add)
        row.addWidget(self.btn_add)

        self.btn_clear = QPushButton("ลบทั้งหมด")
        self.btn_clear.setFixedHeight(32)
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(tinted_btn(T("red"), radius=7, font=11))
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch(1)

        btn_close = QPushButton("ปิด")
        btn_close.setFixedHeight(32)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(filled_btn(T("accent"), radius=7, font=11))
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        v.addLayout(row)

        self._reload()

    # ── internal ──
    def _reload(self):
        while self.strip_row.count():
            it = self.strip_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        paths = logo_store.list_logos()
        if not paths:
            empty = QLabel("ยังไม่มีรูป — กด “เพิ่มรูป…”")
            empty.setStyleSheet(f"color:{T('faint')}; font-size:12px;")
            self.strip_row.addWidget(empty)
        else:
            for p in paths:
                self.strip_row.addWidget(self._tile(p))
        self.strip_row.addStretch(1)

        n = len(paths)
        self.lbl_count.setText(f"{n}/{logo_store.MAX_LOGOS} รูป")
        self.btn_add.setEnabled(n < logo_store.MAX_LOGOS)
        self.btn_clear.setEnabled(n > 0)

    def _tile(self, path):
        """ช่องรูป 1 ใบ + ปุ่มลบใต้รูป"""
        box = QFrame()
        box.setFixedWidth(84)
        box.setStyleSheet("background:transparent; border:none;")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(4)

        img = QLabel()
        img.setFixedSize(84, 56)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet(
            f"background:{T('panel2')}; border:1px solid {rgba('#ffffff', 0.14)};"
            f" border-radius:6px;")
        pm = QPixmap(path)
        if not pm.isNull():
            img.setPixmap(pm.scaled(78, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        img.setToolTip(path)
        bv.addWidget(img)

        rm = QPushButton("ลบ")
        rm.setFixedHeight(22)
        rm.setCursor(Qt.PointingHandCursor)
        rm.setStyleSheet(ghost_btn(radius=5, font=9))
        rm.clicked.connect(lambda _=False, p=path: self._remove(p))
        bv.addWidget(rm)
        return box

    def _add(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "เลือกรูปไอคอน", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        try:
            logo_store.add_logo(path)
        except ValueError as e:
            self.lbl_count.setText(f"เพิ่มไม่ได้ — {e}")
            return
        self._reload()
        self.changed.emit()

    def _remove(self, path):
        logo_store.remove_logo(path)
        self._reload()
        self.changed.emit()

    def _clear(self):
        logo_store.clear_logos()
        self._reload()
        self.changed.emit()
