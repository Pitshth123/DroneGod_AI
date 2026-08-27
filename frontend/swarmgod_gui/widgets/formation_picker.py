"""
formation_picker.py — ปุ่มเลือกรูปขบวน (พระเอกโหมด Swarm)
วาด schematic จุดโดรนตามสูตรเดียวกับ backend/internal/swarm/formation.go
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PyQt5.QtWidgets import QPushButton, QWidget, QGridLayout, QButtonGroup, QSizePolicy

from ..core.theme import T, rgba, hairline


FORMATIONS = [
    (0, "WEDGE", "ลิ่ม / V"),
    (1, "LINE", "หน้ากระดาน"),
    (2, "COLUMN", "แถวตอน"),
    (3, "DIAMOND", "เพชร"),
    (4, "ECHELON", "ทแยง"),
]


def _side_of(i: int) -> float:
    return 1.0 if i % 2 == 1 else -1.0


def slot_offset(formation: int, i: int, d: float = 1.0):
    if formation == 1:  # LINE
        rank = float(i // 2 + 1)
        return 0.0, _side_of(i) * rank * d
    if formation == 2:  # COLUMN
        return -float(i + 1) * d, 0.0
    if formation == 3:  # DIAMOND
        if i == 0:
            return -d, -d
        if i == 1:
            return -d, d
        if i == 2:
            return -2 * d, 0.0
        rank = float((i - 3) // 2 + 2)
        return -rank * d, _side_of(i - 3) * rank * d
    if formation == 4:  # ECHELON
        s = float(i + 1) * d * 0.75
        return -s, s
    rank = float(i // 2 + 1)
    return -rank * d, _side_of(i) * rank * d * 0.8


def formation_points(formation: int, followers: int = 4):
    pts = [(0.0, 0.0)]
    for i in range(followers):
        n, e = slot_offset(formation, i, 1.0)
        pts.append((e, -n))
    return pts


_BTN_QSS = (
    f"QPushButton {{ background:{rgba('#ffffff', 0.03)};"
    f" border:1px solid {hairline()}; border-radius:10px; }}"
    f"QPushButton:hover {{ background:{rgba('#ffffff', 0.06)};"
    f" border:1px solid {rgba('#ffffff', 0.18)}; }}"
    f"QPushButton:checked {{ background:{rgba(T('amber'), 0.18)};"
    f" border:1px solid {rgba(T('amber'), 0.7)}; }}"
    f"QPushButton:checked:hover {{ background:{rgba(T('amber'), 0.22)};"
    f" border:1px solid {rgba(T('amber'), 0.8)}; }}"
)


class FormationButton(QPushButton):
    """ปุ่มการ์ดขบวน — schematic + ชื่อ · เหลืองเฉพาะตอน checked"""

    def __init__(self, formation_id: int, name: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.formation_id = formation_id
        self._name = name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(88, 86)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(92)
        self.setToolTip(f"{name} — {subtitle}" if subtitle else name)
        self.setStyleSheet(_BTN_QSS)
        self.toggled.connect(lambda _on: self.update())

    def sizeHint(self):
        return QSize(96, 92)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        on = self.isChecked()

        # เลือกแล้ว = amber · ไม่เลือก = เทาเย็น ไม่เหลือง
        leader_c = QColor(T("amber") if on else T("dim"))
        follow_c = QColor(T("text") if on else T("faint"))
        line_c = QColor(T("amber") if on else T("faint"))
        line_c.setAlpha(200 if on else 70)
        label_c = QColor(T("amber") if on else T("dim"))

        diagram_h = h - 28
        cx, cy = w / 2.0, diagram_h / 2.0 + 4
        pts = formation_points(self.formation_id, followers=4)

        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 0.01)
        span_y = max(max_y - min_y, 0.01)
        pad = 10.0
        scale = min((w - 2 * pad) / span_x, (diagram_h - 2 * pad) / span_y) * 0.78

        def to_screen(pt):
            x = cx + (pt[0] - (min_x + max_x) / 2.0) * scale
            y = cy + (pt[1] - (min_y + max_y) / 2.0) * scale
            return x, y

        screen = [to_screen(pt) for pt in pts]
        lx, ly = screen[0]

        p.setPen(QPen(line_c, 1.4 if on else 1.0))
        for sx, sy in screen[1:]:
            p.drawLine(int(lx), int(ly), int(sx), int(sy))

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(follow_c))
        for sx, sy in screen[1:]:
            p.drawEllipse(int(sx - 3.2), int(sy - 3.2), 7, 7)

        p.setBrush(QBrush(leader_c))
        p.drawEllipse(int(lx - 5), int(ly - 5), 10, 10)
        if on:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(leader_c, 1.5))
            p.drawEllipse(int(lx - 8), int(ly - 8), 16, 16)

        p.setPen(label_c)
        font = QFont()
        font.setFamilies(["Segoe UI", "Bahnschrift", "Arial"])
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        p.drawText(0, h - 20, w, 16, Qt.AlignHCenter | Qt.AlignVCenter, self._name)
        p.end()


class FormationPicker(QWidget):
    """กริด 5 รูปขบวน — exclusive เหลืองเฉพาะอันที่เลือก"""
    changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)

        self._btns = {}
        for i, (fid, name, sub) in enumerate(FORMATIONS):
            b = FormationButton(fid, name, sub)
            self._group.addButton(b, fid)
            self._btns[fid] = b
            grid.addWidget(b, i // 3, i % 3)

        self._btns[0].setChecked(True)
        self._group.idClicked.connect(self._on_click)
        self._group.buttonToggled.connect(self._on_toggled)

    def _on_click(self, fid: int):
        self.changed.emit(fid)

    def _on_toggled(self, btn, _checked: bool):
        # วาดใหม่ทั้งกลุ่มให้สีตาม isChecked() จริง
        for b in self._btns.values():
            b.update()

    def current(self) -> int:
        cid = self._group.checkedId()
        return cid if cid >= 0 else 0

    def set_current(self, formation_id: int):
        fid = int(formation_id)
        b = self._btns.get(fid)
        if b is None:
            return
        if not b.isChecked():
            b.setChecked(True)
        for x in self._btns.values():
            x.update()
