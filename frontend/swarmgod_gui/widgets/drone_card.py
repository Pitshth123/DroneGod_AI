"""
drone_card.py — การ์ดสถานะโดรน 1 ลำ (iOS-style dark card)
อัปเดตจาก telemetry_pb2.Telemetry ที่ streaming มาจาก core
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QProgressBar, QSizePolicy, QPushButton, QDoubleSpinBox,
)

from ..core.theme import (
    T, STATUS_COLOR, badge_style, DRONE_COLORS, FONT_MONO,
    tinted_btn, rgba,
)
from ..core import rpc


def _c(status: str) -> str:
    return T(STATUS_COLOR.get(status, "dim"))


class DroneCard(QWidget):
    """การ์ด 1 ใบ — เรียก update_from_telemetry(t) เพื่ออัปเดตค่าสด
    มีปุ่มสั่งงานรายลำ (takeoff/rtl/land) + ความสูงแยกแต่ละลำ"""

    takeoff_req = pyqtSignal(int, float)  # drone_id, alt
    rtl_req = pyqtSignal(int)
    land_req = pyqtSignal(int)

    def __init__(self, drone_id: int, name: str = "", parent=None):
        super().__init__(parent)
        self.drone_id = drone_id
        self.name = name or f"Drone {drone_id}"
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        accent = DRONE_COLORS.get(drone_id, T("green"))
        self._accent = accent
        self.setObjectName("DroneCard")
        self.setStyleSheet(
            f"#DroneCard {{ background:{T('panel2')}; border:none; border-radius:14px; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(9)

        # header: dot + name + leader pill + status badge
        top = QHBoxLayout()
        top.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{accent}; font-size:12px;")
        top.addWidget(dot)
        self.lbl_name = QLabel(self.name)
        self.lbl_name.setStyleSheet(
            f"color:{T('text')}; font-weight:700; font-size:14px;")
        top.addWidget(self.lbl_name)
        # LEADER badge (ตัวแม่) — ซ่อนไว้จนกว่าจะเป็นแม่
        self.lbl_leader = QLabel("★ LEADER")
        self.lbl_leader.setStyleSheet(
            f"color:{T('amber')}; background:{rgba(T('amber'), 0.16)}; border:none;"
            f" border-radius:8px; font-size:9px; font-weight:700;"
            f" letter-spacing:0.5px; padding:2px 7px;")
        self.lbl_leader.setVisible(False)
        top.addWidget(self.lbl_leader)
        top.addStretch()
        self.badge = QLabel("OFFLINE")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(badge_style("OFFLINE"))
        top.addWidget(self.badge)
        root.addLayout(top)

        # info grid — เซลล์ยกระดับโค้งมน
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self._vals = {}
        cells = [("IP", 0, 0), ("MODE", 0, 1), ("ALT", 0, 2),
                 ("SAT", 1, 0), ("SPD", 1, 1), ("HDG", 1, 2)]
        for key, r, col in cells:
            cell = QWidget()
            cell.setAttribute(Qt.WA_StyledBackground, True)
            cell.setStyleSheet(
                f"background:{rgba('#ffffff', 0.05)}; border-radius:9px;")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(8, 5, 8, 5)
            cv.setSpacing(1)
            k = QLabel(key)
            k.setStyleSheet(f"color:{T('faint')}; font-size:9px; letter-spacing:1px;")
            v = QLabel("--")
            v.setStyleSheet(
                f"color:{T('text')}; font-size:12px; font-weight:600;"
                f" font-family:{FONT_MONO};")
            cv.addWidget(k)
            cv.addWidget(v)
            grid.addWidget(cell, r, col)
            self._vals[key] = v
        root.addLayout(grid)

        # battery bar + voltage/percent
        bat = QHBoxLayout()
        bat.setSpacing(8)
        lb = QLabel("BAT")
        lb.setStyleSheet(f"color:{T('faint')}; font-size:9px; letter-spacing:1px;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(7)
        self.bar.setTextVisible(False)
        self.lbl_bat = QLabel("--")
        self.lbl_bat.setStyleSheet(
            f"color:{T('dim')}; font-size:11px; font-weight:600;"
            f" font-family:{FONT_MONO}; min-width:70px;")
        self.lbl_bat.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bat.addWidget(lb)
        bat.addWidget(self.bar, 1)
        bat.addWidget(self.lbl_bat)
        root.addLayout(bat)

        # ── ปุ่มสั่งงานรายลำ + ความสูงแยกแต่ละลำ ──
        ctl = QHBoxLayout(); ctl.setSpacing(6)
        mini = QLabel("ALT")
        mini.setStyleSheet(f"color:{T('faint')}; font-size:9px; letter-spacing:1px;")
        ctl.addWidget(mini)
        self.alt_spin = QDoubleSpinBox()
        self.alt_spin.setRange(1, 120); self.alt_spin.setValue(20)
        self.alt_spin.setFixedWidth(66); self.alt_spin.setSuffix(" m")
        self.alt_spin.setStyleSheet("font-size:11px; padding:4px 6px;")
        ctl.addWidget(self.alt_spin)
        b_to = self._btn("Take off", T("green"))
        b_to.clicked.connect(lambda: self.takeoff_req.emit(self.drone_id, self.alt_spin.value()))
        ctl.addWidget(b_to, 1)
        root.addLayout(ctl)

        ctl2 = QHBoxLayout(); ctl2.setSpacing(6)
        b_rtl = self._btn("RTL", T("cyan"))
        b_rtl.clicked.connect(lambda: self.rtl_req.emit(self.drone_id))
        b_land = self._btn("Land", T("amber"))
        b_land.clicked.connect(lambda: self.land_req.emit(self.drone_id))
        ctl2.addWidget(b_rtl); ctl2.addWidget(b_land)
        root.addLayout(ctl2)

    def _btn(self, text, color):
        b = QPushButton(text)
        b.setFixedHeight(30)
        b.setStyleSheet(tinted_btn(color, radius=9, font=12, weight=600))
        return b

    # ──────────────────────────────────────────────
    def set_leader(self, is_leader: bool):
        self.lbl_leader.setVisible(is_leader)

    def update_from_telemetry(self, t):
        status = rpc.status_name(t.status)
        self.badge.setText(status)
        self.badge.setStyleSheet(badge_style(status))

        self._vals["IP"].setText(t.host or "--")
        self._vals["MODE"].setText(rpc.mode_name(t.mode))
        self._vals["ALT"].setText(f"{t.position.alt_rel:.1f}m")
        fix = int(t.gps_fix)
        self._vals["SAT"].setText(f"{t.sat_count} · {'3D' if fix >= 3 else 'no'}")
        self._vals["SPD"].setText(f"{t.ground_speed:.1f} m/s")
        self._vals["HDG"].setText(f"{t.heading:03.0f}°")

        pct = int(t.battery_pct)
        self.bar.setValue(pct)
        clr = T("green") if pct > 60 else T("amber") if pct > 30 else T("red")
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:{rgba('#ffffff', 0.1)}; border:none;"
            f" border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{clr}; border-radius:4px; }}")
        if t.voltage > 0:
            self.lbl_bat.setText(f"{t.voltage:.2f}V · {pct}%")
        else:
            self.lbl_bat.setText(f"{pct}%")
        self.lbl_bat.setStyleSheet(
            f"color:{clr}; font-size:11px; font-weight:700;"
            f" font-family:{FONT_MONO}; min-width:70px;")
