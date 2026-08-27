"""
fleet_item.py — แถวรายชื่อโดรน (ซ้าย) + แผงรายละเอียดลำที่เลือก
โทนเครื่องมือ / โปร: hairline, mono metrics, สถานะเงียบ ไม่ฉูดฉาด
"""
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QSizePolicy, QPushButton, QLineEdit, QDoubleSpinBox, QApplication, QInputDialog,
    QMenu,
)

from ..core.theme import (
    T, rgba, hairline, badge_style, FONT_MONO, ghost_btn, tinted_btn,
    COLOR_CHOICES, drone_color, set_drone_color,
)
from ..core import rpc
from ..core.ip_scan import split_host_port

_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_DRONE_IMG = os.path.join(_ASSETS, "drone_hero.png")


def _drone_pixmap():
    if os.path.exists(_DRONE_IMG):
        return QPixmap(_DRONE_IMG)
    return QPixmap()


# ── ป้ายสถานะ servo A/B (spec: สี่เหลี่ยมเล็ก แดง=A เหลือง=B มีตัวอักษรอยู่ข้างใน) ──
SERVO_BADGE_COLOR = {"A": "red", "B": "yellow"}


def set_qss(widget, qss: str):
    """setStyleSheet เฉพาะเมื่อค่าเปลี่ยนจริง

    PERF: Qt แปลง stylesheet ใหม่ทั้งก้อน + คำนวณ style + repaint ทุกครั้งที่เรียก
    ซึ่งแพงมาก · โค้ดอัปเดต telemetry เรียก setStyleSheet ~8 ครั้งต่อแพ็กเก็ต
    (10 Hz × หลายลำ) ทั้งที่ค่าซ้ำเดิมเกือบทั้งหมด → main thread ตัน คลิกปุ่มแล้วหน่วง
    """
    if widget.property("_qss_cache") != qss:
        widget.setProperty("_qss_cache", qss)
        widget.setStyleSheet(qss)


def servo_badge_qss(label: str) -> str:
    """สี่เหลี่ยมเล็กสีตามช่อง servo ที่สั่งล่าสุด — ใช้ร่วมกันทั้งแถว FLEET และการ์ดล่าง"""
    color = T(SERVO_BADGE_COLOR.get(label, "faint"))
    return (f"background:{color}; color:#101418; border-radius:3px;"
            f" font-size:9px; font-weight:800; letter-spacing:0.5px;"
            f" padding:1px 5px; min-width:12px;")


class FleetItem(QFrame):
    """แถวโดรน 1 ลำ — คลิกเพื่อเลือก (Ctrl+Click = เลือกหลายลำ) · กดดาวเพื่อตั้งเป็น Head"""
    clicked = pyqtSignal(int, bool)   # drone_id, ctrl_held (True = toggle เพิ่ม/เอาออก)
    head_req = pyqtSignal(int)   # ขอเซ็ตลำนี้เป็น Head (app จะถามยืนยันก่อน)
    group_req = pyqtSignal(int, int)  # drone_id, group_no (0 = ไม่มีกลุ่ม)

    def __init__(self, drone_id: int, name: str = "", pixmap: QPixmap = None, parent=None):
        super().__init__(parent)
        self.drone_id = drone_id
        self.name = name or f"UAV-{drone_id:02d}"
        self._accent = drone_color(drone_id)
        self._selected = False
        self._is_head = False
        self._rtl_phase = None
        self._status = "OFFLINE"
        self.setObjectName("FleetItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # ต้องตรงกับ _fleet_item_h ใน app.py (ใช้คำนวณความสูงพื้นที่เลื่อน)
        self.setFixedHeight(64)
        self._apply_style()

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(10)

        # Stage-style ID pill
        self.badge_idx = QLabel(f"D{drone_id}")
        self.badge_idx.setAlignment(Qt.AlignCenter)
        self.badge_idx.setFixedHeight(20)
        self.badge_idx.setMinimumWidth(28)
        self.badge_idx.setStyleSheet(
            f"background:{self._accent}; color:#ffffff; border-radius:10px;"
            f" font-size:9px; font-weight:700; padding:1px 7px;")
        lead = QVBoxLayout()
        lead.setSpacing(3)
        lead.setAlignment(Qt.AlignVCenter)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.dot.setAlignment(Qt.AlignCenter)
        self.dot.setStyleSheet(f"color:{self._accent}; font-size:9px;")
        lead.addWidget(self.dot, 0, Qt.AlignCenter)
        lead.addWidget(self.badge_idx)
        h.addLayout(lead)

        # ไม่มีรูปย่อโดรนในแถว FLEET แล้ว — แถวแคบ พื้นที่ควรให้ชื่อ/สถานะแทน
        # (รูปโดรนยังอยู่ที่การ์ด SELECTED DRONE ด้านล่าง)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        namerow = QHBoxLayout()
        namerow.setSpacing(5)
        self.lbl_name = QLabel(self.name)
        self.lbl_name.setStyleSheet(
            f"color:{self._accent}; font-weight:700; font-size:12px; letter-spacing:0.2px;")
        namerow.addWidget(self.lbl_name)
        # กันพื้นที่ 18px ไว้ตลอด — การเพิ่ม/เอากลุ่มออกจึงไม่ดันชื่อหรือสถานะ
        self.lbl_group = QLabel("")
        self.lbl_group.setAlignment(Qt.AlignCenter)
        self.lbl_group.setFixedSize(18, 18)
        self.lbl_group.setToolTip("No group assigned")
        self.lbl_group.setStyleSheet("background:transparent; border:1px solid transparent;")
        namerow.addWidget(self.lbl_group)
        # ป้ายสถานะ servo A/B — สี่เหลี่ยมเล็กข้างชื่อ (ซ่อนจนกว่าจะมีการสั่ง)
        self.lbl_servo = QLabel("")
        self.lbl_servo.setAlignment(Qt.AlignCenter)
        self.lbl_servo.setVisible(False)
        namerow.addWidget(self.lbl_servo)
        # สถานะ Head แสดงผ่านปุ่มดาว (self.btn_head เปลี่ยนเป็น ★ สีเหลืองตอนเป็นหัว) เท่านั้น
        # — เดิมมีป้าย "★ HEAD" ซ้อนอยู่ในแถวนี้ด้วย แต่แถวแคบ (การ์ดเล็ก) พอชื่อโดรนยาว/
        # ฟอนต์ขยาย ป้ายจะไปทับ/บังชื่อ จึงตัดออก เหลือแค่ไอคอนดาวสีที่ปุ่มพอ
        namerow.addStretch(1)
        mid.addLayout(namerow)
        self.lbl_meta = QLabel("ALT --  ·  -- m/s")
        self.lbl_meta.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        mid.addWidget(self.lbl_meta)
        h.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(3)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toprow = QHBoxLayout()
        toprow.setSpacing(4)
        toprow.addStretch(1)
        # ปุ่มดาว = ตั้งเป็น Head แบบเร็ว (ไม่ทับการคลิกเลือกลำ)
        self.btn_head = QPushButton("☆")
        self.btn_head.setFixedSize(20, 20)
        self.btn_head.setCursor(Qt.PointingHandCursor)
        self.btn_head.setToolTip("ตั้งเป็น Head")
        self.btn_head.clicked.connect(lambda: self.head_req.emit(self.drone_id))
        self._style_head_btn()
        toprow.addWidget(self.btn_head)
        self.badge = QLabel("OFFLINE")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(badge_style("OFFLINE"))
        toprow.addWidget(self.badge)
        right.addLayout(toprow)
        self.lbl_bat = QLabel("--%")
        self.lbl_bat.setStyleSheet(
            f"color:{T('dim')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};")
        right.addWidget(self.lbl_bat, 0, Qt.AlignRight)
        h.addLayout(right)

    def _apply_style(self):
        if self._selected:
            # พื้นสีจางตามสีโดรน — แยกลำที่เลือกได้ชัด
            self.setStyleSheet(
                f"#FleetItem {{ background:{rgba(self._accent, 0.16)};"
                f" border:1px solid {rgba(self._accent, 0.45)}; border-radius:8px; }}")
        else:
            self.setStyleSheet(
                f"#FleetItem {{ background:transparent; border:1px solid transparent;"
                f" border-radius:8px; }}"
                f"#FleetItem:hover {{ background:{rgba(self._accent, 0.08)};"
                f" border:1px solid {rgba(self._accent, 0.22)}; }}")

    def set_selected(self, on: bool):
        self._selected = on
        self._apply_style()

    def set_display_name(self, name: str):
        self.name = (name or "").strip() or f"Drone {self.drone_id}"
        self.lbl_name.setText(self.name)
        self.lbl_name.setToolTip(self.name)

    def set_group(self, group_no: int):
        group = int(group_no or 0)
        if 1 <= group <= 6:
            color = drone_color(group)
            self.lbl_group.setText(str(group))
            self.lbl_group.setToolTip(f"Group {group}")
            self.lbl_group.setStyleSheet(
                f"background:{color}; color:#ffffff; border:none; border-radius:9px;"
                f" font-size:9px; font-weight:800;")
        else:
            self.lbl_group.setText("")
            self.lbl_group.setToolTip("No group assigned")
            self.lbl_group.setStyleSheet(
                "background:transparent; border:1px solid transparent; border-radius:9px;")

    def _style_head_btn(self):
        if self._is_head:
            self.btn_head.setText("★")
            self.btn_head.setStyleSheet(
                f"QPushButton {{ background:{rgba(T('yellow'), 0.2)};"
                f" border:1px solid {rgba(T('yellow'), 0.6)}; border-radius:10px;"
                f" color:{T('yellow')}; font-size:12px; font-weight:800; padding:0; }}"
                f"QPushButton:hover {{ background:{rgba(T('yellow'), 0.32)}; }}")
        else:
            self.btn_head.setText("☆")
            self.btn_head.setStyleSheet(
                f"QPushButton {{ background:transparent;"
                f" border:1px solid {rgba('#ffffff', 0.14)}; border-radius:10px;"
                f" color:{T('faint')}; font-size:12px; font-weight:700; padding:0; }}"
                f"QPushButton:hover {{ background:{rgba(T('yellow'), 0.16)};"
                f" color:{T('yellow')}; border:1px solid {rgba(T('yellow'), 0.4)}; }}")

    def set_head(self, on: bool):
        self._is_head = bool(on)
        self._style_head_btn()

    def set_servo_state(self, labels):
        """แสดงป้าย A/B ที่ "เปิดอยู่จริง" (ว่าง/None = ไม่มีช่องไหนเปิด → ซ่อน)

        รับได้ทั้ง set เช่น {"A","B"} (เปิดพร้อมกันได้) หรือสตริงเดี่ยว "A"
        ค่าที่ส่งมาสะท้อน servo output จริงของ FC จึงเห็นได้แม้กดมาจากรีโมท
        """
        if isinstance(labels, str):
            labels = {labels}
        labels = {l for l in (labels or ()) if l in ("A", "B")}
        if not labels:
            self.lbl_servo.setVisible(False)
            return
        shown = sorted(labels)
        self.lbl_servo.setText(" ".join(shown))
        # เปิดสองช่องพร้อมกัน = ใช้สีของ A (แดง) เป็นตัวเตือนที่เด่นกว่า
        self.lbl_servo.setStyleSheet(servo_badge_qss(shown[0]))
        self.lbl_servo.setToolTip(", ".join(
            f"SERVO {l} (CH{7 if l == 'A' else 8}) เปิดอยู่" for l in shown))
        self.lbl_servo.setVisible(True)

    def set_accent(self, color: str):
        self._accent = color or self._accent
        self.badge_idx.setStyleSheet(
            f"background:{self._accent}; color:#ffffff; border-radius:10px;"
            f" font-size:9px; font-weight:700; padding:1px 7px;")
        self.lbl_name.setStyleSheet(
            f"color:{self._accent}; font-weight:700; font-size:12px; letter-spacing:0.2px;")
        self._apply_style()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
            self.clicked.emit(self.drone_id, ctrl)
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        groups = menu.addMenu("Group")
        for group in range(1, 7):
            action = groups.addAction(str(group))
            action.triggered.connect(
                lambda _=False, g=group: self.group_req.emit(self.drone_id, g))
        groups.addSeparator()
        none = groups.addAction("No group")
        none.triggered.connect(lambda _=False: self.group_req.emit(self.drone_id, 0))
        menu.exec_(e.globalPos())

    def set_rtl_phase(self, phase):
        """โชว์สถานะ RTL ทับ badge ปกติ (CLIMB/RETURN/LAND); None = เลิกโชว์"""
        self._rtl_phase = phase or None
        self._apply_badge()

    def _apply_badge(self):
        """RTL ที่เราคุมเองไม่สะท้อนใน status ของ FC → ต้องโชว์เองบนการ์ด"""
        if getattr(self, "_rtl_phase", None):
            self.badge.setText(f"RTL·{self._rtl_phase}")
            set_qss(self.badge,
                    f"color:#ffffff; background:{T('orange')}; border:none;"
                    f" border-radius:3px; font-size:9px; font-weight:800;"
                    f" letter-spacing:0.6px; padding:1px 6px;")
            set_qss(self.dot, f"color:{T('orange')}; font-size:9px;")
            return
        status = getattr(self, "_status", "OFFLINE")
        self.badge.setText(status)
        set_qss(self.badge, badge_style(status))
        sc = T("green") if status in ("READY", "FLYING", "HOLD", "MOVING", "TAKEOFF") else \
             T("amber") if status in ("ARMED", "CONNECTING", "RECONNECTING", "LANDING") else \
             T("red") if status == "RTL" else T("faint")
        set_qss(self.dot, f"color:{sc}; font-size:9px;")

    def mark_offline(self):
        """telemetry ขาด → ตีเป็น OFFLINE (ไม่งั้น badge ค้างสถานะเก่าเช่น READY ตลอดไป)"""
        self._status = "OFFLINE"
        self._rtl_phase = None
        self._apply_badge()
        self.lbl_meta.setText("-- m  ·  -- m/s  ·  ไม่มีสัญญาณ")
        self.lbl_bat.setText("--%")
        self.lbl_bat.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};")

    def update_from_telemetry(self, t):
        self._status = rpc.status_name(t.status)
        self._apply_badge()
        self.lbl_meta.setText(
            f"{t.position.alt_rel:.0f} m  ·  {t.ground_speed:.1f} m/s  ·  {rpc.mode_name(t.mode)}")
        pct = int(t.battery_pct)
        clr = T("green") if pct > 60 else T("amber") if pct > 30 else T("red")
        self.lbl_bat.setText(f"{pct}%  {getattr(t, 'voltage', 0):.1f}V")
        set_qss(self.lbl_bat,
                f"color:{clr}; font-size:10px; font-weight:600; font-family:{FONT_MONO};")


class SelectedDroneCard(QFrame):
    """แผงรายละเอียดลำที่เลือก — ข้อมูลหนาแน่น โทนเครื่องมือ"""
    rtl_req = pyqtSignal(int)
    land_req = pyqtSignal(int)
    hold_req = pyqtSignal(int)
    color_changed = pyqtSignal(int, str)   # drone_id, hex
    ping_req = pyqtSignal(int)             # drone_id
    connect_req = pyqtSignal(int, str, int)   # id, host, port
    disconnect_req = pyqtSignal(int)
    delete_req = pyqtSignal(int)
    apply_ip_req = pyqtSignal(int, str, int)  # reconnect ด้วย IP ใหม่
    head_req = pyqtSignal(int)             # ขอตั้งลำนี้เป็น Head (app ถามยืนยัน)
    estop_req = pyqtSignal(int)            # ขอ Emergency Stop ลำนี้ (app double-confirm)
    arm_req = pyqtSignal(int)              # Quick action: ARM ลำนี้
    disarm_req = pyqtSignal(int)           # Quick action: DISARM ลำนี้
    alt_changed = pyqtSignal(int, float)      # drone_id, ความสูง takeoff ของลำนี้
    spacing_changed = pyqtSignal(int, float)  # drone_id, ระยะห่างจากลำหน้า
    rename_req = pyqtSignal(int, str)          # เปลี่ยนชื่อที่แสดง/บันทึกใน cockpit

    def __init__(self, pixmap: QPixmap = None, parent=None):
        super().__init__(parent)
        self.drone_id = 0
        self._host = ""
        self._port = 0
        self._is_head = False
        self._rtl_phase = None
        self._status = "OFFLINE"
        self._accent = T("green")
        self._pixmap = pixmap if (pixmap and not pixmap.isNull()) else _drone_pixmap()
        self.setObjectName("SelCard")
        self._apply_card_accent()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.setMinimumWidth(0)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # header — โมเดลโดรนขนาดอ่านรูปทรงได้ชัด + ชื่อโดรนเป็นหัวเรื่อง (รูปอยู่เฉพาะการ์ดล่างนี้
        # ส่วนแถว FLEET ด้านบนไม่มีรูปแล้ว)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setFixedSize(72, 60)
        self.img.setToolTip("โมเดลโดรนลำที่เลือก")
        self.img.setStyleSheet(
            f"background:{rgba('#000000', 0.36)}; border:1px solid {rgba(T('cyan'), 0.30)};"
            f" border-radius:9px; padding:3px;")
        if not self._pixmap.isNull():
            self.img.setPixmap(self._pixmap.scaled(52, 52, Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))
        top.addWidget(self.img)
        self.lbl_name = QLabel("No vehicle")
        self.lbl_name.setWordWrap(False)
        self.lbl_name.setStyleSheet(
            f"color:{T('text')}; font-weight:700; font-size:14px;")
        top.addWidget(self.lbl_name, 1)
        self.btn_rename = QPushButton("✎")
        self.btn_rename.setFixedSize(22, 22)
        self.btn_rename.setCursor(Qt.PointingHandCursor)
        self.btn_rename.setToolTip("แก้ไขชื่อโดรน")
        self.btn_rename.setStyleSheet(ghost_btn(radius=6, font=10))
        self.btn_rename.clicked.connect(self._edit_name)
        top.addWidget(self.btn_rename)
        # ป้ายสถานะ servo A/B — สี่เหลี่ยมเล็กเหมือนแถว FLEET
        self.lbl_servo = QLabel("")
        self.lbl_servo.setAlignment(Qt.AlignCenter)
        self.lbl_servo.setVisible(False)
        top.addWidget(self.lbl_servo)
        # HEAD และสถานะสูงเท่ากัน เพื่อไม่ให้ป้ายใดป้ายหนึ่งเด่นเกินชื่อ/โมเดล
        self.lbl_head = QLabel("★ HEAD")
        self.lbl_head.setAlignment(Qt.AlignCenter)
        self.lbl_head.setFixedHeight(18)
        self.lbl_head.setMinimumWidth(48)
        self.lbl_head.setStyleSheet(
            f"color:{T('yellow')}; background:{rgba(T('yellow'), 0.12)};"
            f" border:1px solid {rgba(T('yellow'), 0.45)}; border-radius:4px;"
            f" font-size:8px; font-weight:800; letter-spacing:0.4px; padding:0 5px;")
        self.lbl_head.setVisible(False)
        top.addWidget(self.lbl_head, 0, Qt.AlignRight)
        self.badge = QLabel("OFFLINE")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedHeight(18)
        self.badge.setMinimumWidth(52)
        self.badge.setStyleSheet(badge_style("OFFLINE"))
        top.addWidget(self.badge, 0, Qt.AlignRight)
        v.addLayout(top)

        # ping/link แถวบางใต้ชื่อ — ไม่มีรูปโดรนคั่นซ้ายอีกต่อไป
        prow = QHBoxLayout()
        prow.setSpacing(4)
        self.lbl_ping = QLabel("PING --")
        self.lbl_ping.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; font-family:{FONT_MONO};")
        prow.addWidget(self.lbl_ping, 1)
        self.lbl_link = QLabel("LINK --")
        self.lbl_link.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        prow.addWidget(self.lbl_link, 1)
        self.btn_ping = QPushButton("PING")
        self.btn_ping.setFixedHeight(22)
        self.btn_ping.setFixedWidth(48)
        self.btn_ping.setCursor(Qt.PointingHandCursor)
        self.btn_ping.setToolTip("Ping this drone to check link latency")
        self.btn_ping.setStyleSheet(ghost_btn(radius=5, font=9))
        self.btn_ping.clicked.connect(self._emit_ping)
        prow.addWidget(self.btn_ping)
        v.addLayout(prow)

        # ── STATUS (ย้ายขึ้นมาบนสุดของก้อนตามที่สั่ง) — การ์ดย่อยแยกกรอบให้เด่น ──
        status_wrap = QFrame()
        status_wrap.setObjectName("StatusWrap")
        status_wrap.setStyleSheet(
            f"#StatusWrap {{ background:{T('panel3')};"
            f" border:1px solid {rgba(T('accent'), 0.22)}; border-radius:8px; }}")
        sv = QVBoxLayout(status_wrap)
        sv.setContentsMargins(9, 8, 9, 9)
        sv.setSpacing(7)

        self._vals = {}
        self._metric_cells = {}
        headline = QHBoxLayout()
        headline.setSpacing(14)
        headline.addLayout(self._status_headline_col("📶", "LINK QUALITY", "LINKQ"), 1)
        headline.addLayout(self._status_headline_col("🔋", "BATTERY", "BATTERY"), 1)
        sv.addLayout(headline)

        grid = QGridLayout()
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(0)
        grid.setContentsMargins(0, 0, 0, 0)
        cells = [("MODE", 0, 0), ("SPEED", 0, 1), ("HEADING", 0, 2),
                 ("GPS", 1, 0), ("ALTITUDE", 1, 1), ("RSSI", 1, 2)]
        for key, r, col in cells:
            grid.addWidget(self._metric_cell(key, r, col), r, col)
        sv.addLayout(grid)
        v.addWidget(status_wrap)

        # IP: host เต็มแถว → port + OK แถวถัดไป (เห็น IP ครบ) — ตามด้วยสถานะตามที่สั่ง
        ip_lab = QLabel("IP ADDRESS")
        ip_lab.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; letter-spacing:0.8px;")
        v.addWidget(ip_lab)
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.100")
        self.ed_host.setMinimumHeight(28)
        self.ed_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.ed_host.setStyleSheet(self._ip_edit_qss())
        v.addWidget(self.ed_host)

        port_row = QHBoxLayout()
        port_row.setSpacing(4)
        port_lab = QLabel("PORT")
        port_lab.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600;")
        port_row.addWidget(port_lab)
        self.ed_port = QLineEdit()
        self.ed_port.setPlaceholderText("5760")
        self.ed_port.setMinimumHeight(28)
        self.ed_port.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.ed_port.setStyleSheet(self._ip_edit_qss())
        port_row.addWidget(self.ed_port, 1)
        self.btn_apply_ip = QPushButton("OK")
        self.btn_apply_ip.setFixedSize(40, 28)
        self.btn_apply_ip.setCursor(Qt.PointingHandCursor)
        self.btn_apply_ip.setToolTip("Apply IP / reconnect")
        self.btn_apply_ip.setStyleSheet(ghost_btn(radius=5, font=9))
        self.btn_apply_ip.clicked.connect(self._emit_apply_ip)
        port_row.addWidget(self.btn_apply_ip)
        v.addLayout(port_row)

        # ปุ่มลิงก์ — กว้างเท่ากัน ไม่ล้นขอบ
        link_row = QHBoxLayout()
        link_row.setSpacing(4)
        self.btn_connect = self._qbtn("CONN", T("green"))
        self.btn_disconnect = self._qbtn("DISC", T("amber"))
        self.btn_delete = self._qbtn("DEL", T("red"))
        self.btn_connect.setToolTip("Connect to this drone")
        self.btn_disconnect.setToolTip("Disconnect from this drone")
        self.btn_delete.setToolTip("Remove this drone from the fleet")
        for b in (self.btn_connect, self.btn_disconnect, self.btn_delete):
            b.setFixedHeight(28)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumWidth(0)
        self.btn_connect.clicked.connect(self._emit_connect)
        self.btn_disconnect.clicked.connect(self._emit_disconnect)
        self.btn_delete.clicked.connect(self._emit_delete)
        link_row.addWidget(self.btn_connect, 1)
        link_row.addWidget(self.btn_disconnect, 1)
        link_row.addWidget(self.btn_delete, 1)
        v.addLayout(link_row)

        # color
        crow = QHBoxLayout()
        crow.setSpacing(3)
        cl = QLabel("COLOR")
        cl.setStyleSheet(f"color:{T('faint')}; font-size:9px; font-weight:600; letter-spacing:0.6px;")
        crow.addWidget(cl)
        self._color_btns = []
        for hexc in COLOR_CHOICES:
            b = QPushButton()
            b.setFixedSize(10, 10)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("swatch", hexc)
            b.setToolTip(hexc)
            b.clicked.connect(lambda _=False, c=hexc: self._pick_color(c))
            crow.addWidget(b)
            self._color_btns.append(b)
        crow.addStretch(1)
        v.addLayout(crow)
        self._refresh_swatches()

        # ── พารามิเตอร์รายลำ: ความสูง takeoff + ระยะห่าง (ย้ายมาจาก Right Panel) ──
        # ป้ายกำกับเป็นอังกฤษล้วน — คำอธิบายเต็มอยู่ใน tooltip แทน (ไม่เอาไทยขึ้นบนตัวควบคุม)
        pl = QLabel("PARAMETERS")
        pl.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; letter-spacing:1.4px;")
        v.addWidget(pl)
        prow2 = QHBoxLayout()
        prow2.setSpacing(4)
        self.spin_alt = self._param_spin("ALT", 1, 120, 20, " m", T("accent"))
        self.spin_alt.setToolTip("ความสูงที่จะขึ้นบิน (Takeoff altitude)")
        self.spin_alt.valueChanged.connect(
            lambda val: self.drone_id and self.alt_changed.emit(self.drone_id, float(val)))
        self.spin_spacing = self._param_spin("SPACING", 1, 50, 12, " m", T("amber"))
        self.spin_spacing.setToolTip("ระยะห่างจากลำหน้าตอนบินเป็นขบวน (Formation spacing)")
        self.spin_spacing.valueChanged.connect(
            lambda val: self.drone_id and self.spacing_changed.emit(self.drone_id, float(val)))
        prow2.addLayout(self._param_cell("TAKEOFF ALT", self.spin_alt), 1)
        prow2.addLayout(self._param_cell("SPACING", self.spin_spacing), 1)
        v.addLayout(prow2)

        param_help = QLabel(
            "TAKEOFF ALT = ความสูงเป้าหมายตอนขึ้นบิน  ·  "
            "SPACING = ระยะห่างจากลำหน้าขณะบินเป็นขบวน")
        param_help.setWordWrap(True)
        param_help.setStyleSheet(
            f"color:{T('faint')}; font-size:9px; line-height:135%;"
            f" background:{rgba('#ffffff', 0.025)}; border-radius:5px; padding:4px 6px;")
        v.addWidget(param_help)

        qa = QLabel("QUICK ACTIONS")
        qa.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-weight:600; letter-spacing:1.4px;")
        v.addWidget(qa)

        # แถว 1: ARM / DISARM (คำสั่งเปลี่ยนสถานะเครื่อง)
        arow = QHBoxLayout()
        arow.setSpacing(4)
        self.btn_arm = self._pro_btn("ARM", T("green"))
        self.btn_arm.setToolTip("Arm motors")
        self.btn_disarm = self._pro_btn("DISARM", T("red"))
        self.btn_disarm.setToolTip("Disarm motors")
        self.btn_arm.clicked.connect(lambda: self.drone_id and self.arm_req.emit(self.drone_id))
        self.btn_disarm.clicked.connect(
            lambda: self.drone_id and self.disarm_req.emit(self.drone_id))
        arow.addWidget(self.btn_arm, 1)
        arow.addWidget(self.btn_disarm, 1)
        v.addLayout(arow)

        # แถว 2: RTL / LAND / HOLD (คำสั่งการบิน)
        brow = QHBoxLayout()
        brow.setSpacing(4)
        self.btn_rtl = self._pro_btn("RTL", T("cyan"))
        self.btn_rtl.setToolTip("Return to launch")
        self.btn_land = self._pro_btn("LAND", T("amber"))
        self.btn_land.setToolTip("Land in place")
        self.btn_hold = self._pro_btn("HOLD", T("dim"))
        self.btn_hold.setToolTip("Hold current position")
        for b in (self.btn_arm, self.btn_disarm,
                  self.btn_rtl, self.btn_land, self.btn_hold):
            b.setFixedHeight(28)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumWidth(0)
        self.btn_rtl.clicked.connect(lambda: self.drone_id and self.rtl_req.emit(self.drone_id))
        self.btn_land.clicked.connect(lambda: self.drone_id and self.land_req.emit(self.drone_id))
        self.btn_hold.clicked.connect(lambda: self.drone_id and self.hold_req.emit(self.drone_id))
        brow.addWidget(self.btn_rtl, 1)
        brow.addWidget(self.btn_land, 1)
        brow.addWidget(self.btn_hold, 1)
        v.addLayout(brow)

        # ── Head + Emergency Stop (spec 1 + spec 4) — ตำแหน่ง/สไตล์เดิมทุกอย่าง ──
        hs = QHBoxLayout()
        hs.setSpacing(4)
        self.btn_sethead = QPushButton("★ SET HEAD")
        self.btn_sethead.setFixedHeight(28)
        self.btn_sethead.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_sethead.setCursor(Qt.PointingHandCursor)
        self.btn_sethead.setToolTip("Set this drone as the swarm head/leader")
        self.btn_sethead.setStyleSheet(tinted_btn(T("yellow"), radius=6, font=10))
        self.btn_sethead.clicked.connect(
            lambda: self.drone_id and self.head_req.emit(self.drone_id))
        self.btn_estop = QPushButton("■ E-STOP")
        self.btn_estop.setFixedHeight(28)
        self.btn_estop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_estop.setCursor(Qt.PointingHandCursor)
        self.btn_estop.setToolTip("หยุดฉุกเฉินเฉพาะลำนี้ (ถามยืนยัน 2 ครั้ง)")
        self.btn_estop.setStyleSheet(tinted_btn(T("red"), radius=6, font=10))
        self.btn_estop.clicked.connect(
            lambda: self.drone_id and self.estop_req.emit(self.drone_id))
        hs.addWidget(self.btn_sethead, 1)
        hs.addWidget(self.btn_estop, 1)
        v.addLayout(hs)
        v.addStretch(1)

    def _status_headline_col(self, icon, label, key):
        """คอลัมน์ไอคอน+ป้าย+ตัวเลขใหญ่ สำหรับแถวหัวสุดของ STATUS block (LINK QUALITY/BATTERY)"""
        col = QVBoxLayout()
        col.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(4)
        icon_lab = QLabel(icon)
        icon_lab.setStyleSheet("font-size:14px;")
        head.addWidget(icon_lab)
        text_lab = QLabel(label)
        text_lab.setStyleSheet(
            f"color:{T('faint')}; font-size:8px; font-weight:700; letter-spacing:0.6px;")
        head.addWidget(text_lab)
        head.addStretch(1)
        col.addLayout(head)
        val = QLabel("--%")
        val.setStyleSheet(
            f"color:{T('text')}; font-size:14px; font-weight:700; font-family:{FONT_MONO};")
        col.addWidget(val)
        self._vals[key] = val
        return col

    @staticmethod
    def _ip_edit_qss():
        return (
            f"QLineEdit {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {hairline()}; border-radius:5px;"
            f" padding:5px 8px; font-size:12px; font-family:{FONT_MONO}; }}"
            f"QLineEdit:focus {{ border:1px solid {rgba(T('accent'), 0.55)}; }}"
        )
    def _metric_cell(self, key, row, col):
        cell = QFrame()
        cell.setObjectName("ModeMetric" if key == "MODE" else "MetricCell")
        cell.setMinimumWidth(0)
        borders = []
        if row == 0:
            borders.append(f"border-bottom:1px solid {hairline()};")
        if col < 2:
            borders.append(f"border-right:1px solid {hairline()};")
        if key == "MODE":
            cell.setStyleSheet(
                f"#ModeMetric {{ background:{rgba('#ffffff', 0.045)};"
                f" border:1px solid {rgba('#ffffff', 0.24)}; border-radius:6px; }}")
        else:
            cell.setStyleSheet(
                f"#MetricCell {{ background:transparent; {''.join(borders)} }}")
        cv = QVBoxLayout(cell)
        cv.setContentsMargins(4, 5, 4, 5)
        cv.setSpacing(1)
        k = QLabel(key)
        k.setStyleSheet(
            f"color:{T('faint')}; font-size:8px; font-weight:600; letter-spacing:0.5px;")
        val = QLabel("--")
        val.setStyleSheet(
            f"color:{T('text')}; font-size:{13 if key == 'MODE' else 12}px;"
            f" font-weight:{800 if key == 'MODE' else 600}; font-family:{FONT_MONO};")
        cv.addWidget(k)
        cv.addWidget(val)
        self._vals[key] = val
        self._metric_cells[key] = cell
        return cell

    def _apply_card_accent(self):
        """กรอบนอกยืนยันลำที่กำลังเลือก แม้ผู้ใช้เลื่อนมาดูการ์ดด้านล่าง."""
        if self.drone_id:
            border = f"2px solid {rgba(self._accent, 0.78)}"
        else:
            border = f"1px solid {hairline()}"
        set_qss(self,
                f"#SelCard {{ background:{T('panel2')}; border:{border};"
                f" border-radius:8px; }}")

    def _style_mode_metric(self, color=None):
        """ทำให้ MODE เป็นการ์ดย่อยมีกรอบชัด และใช้สีเดียวกับสถานะบิน."""
        cell = self._metric_cells.get("MODE")
        if cell is None:
            return
        if color is None:
            # สภาวะเริ่มต้นยังคงกรอบขาว เพื่อให้อ่านได้ก่อนมี telemetry
            color = "#ffffff"
        set_qss(cell,
                f"#ModeMetric {{ background:{rgba(color, 0.08)};"
                f" border:2px solid {rgba(color, 0.72)}; border-radius:6px; }}")

    def set_selected_drone(self, drone_id: int):
        """อัปเดตสีกรอบทันที แม้ลำที่เลือกยังไม่มี telemetry."""
        self.drone_id = int(drone_id or 0)
        self._accent = drone_color(self.drone_id) if self.drone_id else T("green")
        self._apply_card_accent()

    def _qbtn(self, text, color):
        b = QPushButton(text)
        b.setFixedHeight(28)
        b.setMinimumWidth(0)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if color in (T("dim"),):
            b.setStyleSheet(ghost_btn(radius=6, font=10))
        else:
            b.setStyleSheet(tinted_btn(color, radius=6, font=10))
        return b

    def _pro_btn(self, text, color):
        """ปุ่ม Quick Action โทนใหม่ — พื้นเข้มเรียบ + แถบสีซ้ายบอกความหมาย แทนพื้น
        ไล่สีเต็มแบบเดิม ให้ความรู้สึกโปร/enterprise มากกว่า"""
        b = QPushButton(text)
        b.setFixedHeight(28)
        b.setMinimumWidth(0)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton {{ background:{T('panel3')}; border:none;"
            f" border-left:3px solid {color}; border-radius:5px;"
            f" color:{T('text')}; font-size:10px; font-weight:700;"
            f" letter-spacing:0.8px; padding-left:6px; text-align:left; }}"
            f"QPushButton:hover {{ background:{rgba(color, 0.14)}; }}"
            f"QPushButton:pressed {{ background:{rgba(color, 0.24)}; }}"
            f"QPushButton:disabled {{ color:{T('faint')};"
            f" border-left:3px solid {rgba('#ffffff', 0.1)}; }}")
        return b

    def _param_spin(self, _name, lo, hi, val, suffix, color):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(0)
        sp.setSingleStep(1)
        sp.setValue(float(val))
        sp.setSuffix(suffix)
        sp.setAlignment(Qt.AlignCenter)
        sp.setFixedHeight(26)
        sp.setStyleSheet(
            f"QDoubleSpinBox {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {rgba(color, 0.45)}; border-radius:8px;"
            f" padding:2px 6px; font-family:{FONT_MONO}; font-size:11px; font-weight:700; }}"
            f"QDoubleSpinBox:focus {{ border:1px solid {rgba(color, 0.85)}; }}")
        return sp

    @staticmethod
    def _param_cell(label, spin):
        col = QVBoxLayout()
        col.setSpacing(2)
        lab = QLabel(label)
        lab.setStyleSheet(f"color:{T('faint')}; font-size:8px; font-weight:600;")
        col.addWidget(lab)
        col.addWidget(spin)
        return col

    def set_quick_enabled(self, on: bool):
        for b in (self.btn_arm, self.btn_disarm,
                  self.btn_rtl, self.btn_land, self.btn_hold,
                  self.btn_sethead, self.btn_estop):
            b.setEnabled(on)

    def set_params(self, alt: float, spacing: float):
        """โหลดค่า ALT/SPACING ของลำที่เลือก โดยไม่ยิง signal กลับ"""
        for sp, val in ((self.spin_alt, alt), (self.spin_spacing, spacing)):
            sp.blockSignals(True)
            sp.setValue(float(val))
            sp.blockSignals(False)

    def set_display_name(self, name: str):
        shown = (name or "").strip() or (f"Drone {self.drone_id}" if self.drone_id else "No vehicle")
        self.lbl_name.setText(shown)
        self.lbl_name.setToolTip(shown)

    def _edit_name(self, _checked=False):
        if not self.drone_id:
            return
        current = self.lbl_name.text().strip() or f"Drone {self.drone_id}"
        name, ok = QInputDialog.getText(self, "แก้ไขชื่อโดรน", "ชื่อที่แสดง:", text=current)
        name = " ".join((name or "").split())[:32]
        if ok and name and name != current:
            self.rename_req.emit(int(self.drone_id), name)

    def set_head(self, on: bool):
        self._is_head = bool(on)
        self.lbl_head.setVisible(self._is_head)
        self.btn_sethead.setText("★ HEAD" if self._is_head else "★ SET HEAD")

    def set_servo_state(self, labels):
        """แสดงป้าย A/B ที่ "เปิดอยู่จริง" (ว่าง/None = ไม่มีช่องไหนเปิด → ซ่อน)

        รับได้ทั้ง set เช่น {"A","B"} (เปิดพร้อมกันได้) หรือสตริงเดี่ยว "A"
        ค่าที่ส่งมาสะท้อน servo output จริงของ FC จึงเห็นได้แม้กดมาจากรีโมท
        """
        if isinstance(labels, str):
            labels = {labels}
        labels = {l for l in (labels or ()) if l in ("A", "B")}
        if not labels:
            self.lbl_servo.setVisible(False)
            return
        shown = sorted(labels)
        self.lbl_servo.setText(" ".join(shown))
        # เปิดสองช่องพร้อมกัน = ใช้สีของ A (แดง) เป็นตัวเตือนที่เด่นกว่า
        self.lbl_servo.setStyleSheet(servo_badge_qss(shown[0]))
        self.lbl_servo.setToolTip(", ".join(
            f"SERVO {l} (CH{7 if l == 'A' else 8}) เปิดอยู่" for l in shown))
        self.lbl_servo.setVisible(True)

    def set_rtl_phase(self, phase):
        """โชว์สถานะ RTL บนการ์ดล่าง (CLIMB/RETURN/LAND); None = เลิกโชว์"""
        self._rtl_phase = phase or None
        self._apply_badge()

    def _apply_badge(self):
        if getattr(self, "_rtl_phase", None):
            self.badge.setText(f"RTL·{self._rtl_phase}")
            self.badge.setStyleSheet(
                f"color:#ffffff; background:{T('orange')}; border:none;"
                f" border-radius:3px; font-size:9px; font-weight:800;"
                f" letter-spacing:0.6px; padding:1px 6px;")
            return
        status = getattr(self, "_status", "OFFLINE")
        self.badge.setText(status)
        self.badge.setStyleSheet(badge_style(status))

    def _read_endpoint(self):
        raw_host = (self.ed_host.text() or "").strip() or (self._host or "").strip()
        raw_port = (self.ed_port.text() or "").strip()
        try:
            port = int(raw_port) if raw_port else int(self._port or 0)
        except ValueError:
            port = int(self._port or 0)
        if not port:
            port = 5760
        # BUGFIX: ช่อง IP รับ "host:port" ได้ด้วย (แอปเองก็เติมรูปแบบนี้ลงไปหลัง SCAN)
        # เดิมเอาข้อความทั้งก้อนไปเป็น host แล้วต่อพอร์ตเข้าไปอีก
        # → dial "10.0.0.1:5760:5760" ไม่ติด · reader ตายใน ~8 วิโดยไม่บอกสาเหตุ
        return split_host_port(raw_host, port)

    def _emit_connect(self, _checked=False):
        host, port = self._read_endpoint()
        # drone_id=0 → ให้แอปจอง id ใหม่ (ยังไม่เลือกโดรนก็เชื่อมได้)
        self.connect_req.emit(int(self.drone_id or 0), host, int(port))

    def _emit_disconnect(self, _checked=False):
        self.disconnect_req.emit(int(self.drone_id or 0))

    def _emit_delete(self, _checked=False):
        self.delete_req.emit(int(self.drone_id or 0))

    def _emit_apply_ip(self, _checked=False):
        host, port = self._read_endpoint()
        self.apply_ip_req.emit(int(self.drone_id or 0), host, int(port))

    def _emit_ping(self, _checked=False):
        if self.drone_id:
            self.set_ping_pending()
            self.ping_req.emit(self.drone_id)
        else:
            # ปิง IP ในช่องได้แม้ยังไม่เลือกโดรน
            host, port = self._read_endpoint()
            if host:
                self.set_ping_pending()
                self.ping_req.emit(0)

    def _pick_color(self, color: str):
        if not self.drone_id:
            return
        c = set_drone_color(self.drone_id, color)
        self._accent = c
        self._apply_card_accent()
        self._refresh_swatches()
        self.lbl_name.setStyleSheet(
            f"color:{c}; font-weight:700; font-size:13px;")
        self.color_changed.emit(self.drone_id, c)

    def _refresh_swatches(self):
        # ถูกเรียกจาก update_from_telemetry ทุกแพ็กเก็ต แต่สีแทบไม่เคยเปลี่ยน
        # → set_qss ข้ามให้เองถ้าค่าเดิม (สวอทช์มี ~10 ปุ่ม = ประหยัดได้เยอะ)
        for b in self._color_btns:
            hexc = b.property("swatch")
            on = (hexc == self._accent)
            ring = "#ffffff" if on else "transparent"
            set_qss(b,
                    f"QPushButton {{ background:{hexc}; border:1px solid {ring};"
                    f" border-radius:5px; padding:0; min-width:10px; max-width:10px;"
                    f" min-height:10px; max-height:10px; }}"
                    f"QPushButton:hover {{ border:1px solid #ffffff; }}")

    def set_ping_pending(self):
        self.lbl_ping.setText("PING …")
        self.lbl_ping.setStyleSheet(
            f"color:{T('amber')}; font-size:10px; font-weight:700; font-family:{FONT_MONO};")
        self.btn_ping.setEnabled(False)

    def set_ping_result(self, ok: bool, text: str):
        clr = T("green") if ok else T("red")
        self.lbl_ping.setText(text)
        self.lbl_ping.setStyleSheet(
            f"color:{clr}; font-size:10px; font-weight:700; font-family:{FONT_MONO};")
        self.btn_ping.setEnabled(True)

    def mark_offline(self):
        """telemetry ขาด → การ์ดล่างต้องขึ้น OFFLINE ด้วย

        เดิมการ์ดนี้ค้างสถานะสุดท้าย (เช่น READY) แม้แถว FLEET ด้านบนจะรู้ว่าหลุดแล้ว
        ทำให้สองที่โชว์ไม่ตรงกัน
        """
        self._status = "OFFLINE"
        self._rtl_phase = None
        self._apply_badge()
        self._style_mode_metric(T("faint"))
        for key in ("MODE", "SPEED", "HEADING", "GPS", "ALTITUDE", "RSSI"):
            if key in self._vals:
                self._vals[key].setText("--")
        for key in ("BATTERY", "LINKQ"):
            if key in self._vals:
                self._vals[key].setText("--")
                self._vals[key].setStyleSheet(
                    f"color:{T('faint')}; font-size:14px; font-weight:700;"
                    f" font-family:{FONT_MONO};")
        self.lbl_link.setText("LINK --")

    def update_from_telemetry(self, t):
        self.drone_id = t.drone_id
        accent = drone_color(t.drone_id)
        self._accent = accent
        self._apply_card_accent()
        self.set_display_name(t.name or f"Drone {t.drone_id}")
        set_qss(self.lbl_name, f"color:{accent}; font-weight:700; font-size:13px;")
        self._status = rpc.status_name(t.status)
        self._apply_badge()
        self._refresh_swatches()

        host = (getattr(t, "host", "") or "").strip()
        port = int(getattr(t, "port", 0) or 0)
        self._host = host
        self._port = port
        # ไม่ทับตอนกำลังพิมพ์
        if not self.ed_host.hasFocus() and not self.ed_port.hasFocus():
            if host:
                self.ed_host.setText(host)
            if port:
                self.ed_port.setText(str(port))

        pct = int(t.battery_pct)
        voltage = getattr(t, "voltage", 0.0)
        bclr = T("green") if pct > 60 else T("amber") if pct > 30 else T("red")
        self._vals["BATTERY"].setText(f"{pct}%  {voltage:.1f}V")
        set_qss(self._vals["BATTERY"],
                f"color:{bclr}; font-size:14px; font-weight:700; font-family:{FONT_MONO};")
        mode_name = rpc.mode_name(t.mode)
        self._vals["MODE"].setText(mode_name)
        mode_color = (T("cyan") if mode_name == "GUIDED" else
                      T("green") if mode_name in ("LOITER", "POSHOLD", "ALT_HOLD") else
                      T("red") if mode_name == "RTL" else
                      T("amber") if mode_name == "LAND" else T("text"))
        self._style_mode_metric(mode_color)
        set_qss(self._vals["MODE"],
                f"color:{mode_color}; font-size:13px; font-weight:800; font-family:{FONT_MONO};")
        self._vals["ALTITUDE"].setText(f"{t.position.alt_rel:.0f} m")
        self._vals["SPEED"].setText(f"{t.ground_speed:.1f} m/s")
        self._vals["HEADING"].setText(f"{t.heading:03.0f}°")
        fix = int(t.gps_fix)
        self._vals["GPS"].setText(f"{t.sat_count} · {'3D' if fix >= 3 else 'NO'}")

        # ── Datalink: ใช้ค่าจริงจาก core เท่านั้น ──
        # เดิม proto ไม่มี field rssi เลย getattr เลย fallback 0 ทุกครั้ง แล้วสูตรเก่า
        # ((0+90)/60*100) ให้ 100% ตลอด = โชว์ "ลิงก์เต็ม" ปลอม ๆ บนจอที่ใช้ตัดสินใจบิน
        rssi = int(getattr(t, "rssi", 0))
        rssi_ok = bool(getattr(t, "rssi_valid", False))
        linkq = int(getattr(t, "link_quality", 0))

        if rssi_ok:
            self._vals["RSSI"].setText(f"{rssi} dBm")
        else:
            # ไม่มีวิทยุ SiK รายงาน (SITL/USB/UDP ตรง) — บอกตรง ๆ ว่าไม่มีข้อมูล
            self._vals["RSSI"].setText("N/A")

        if linkq > 0:
            lclr = T("green") if linkq > 60 else T("amber") if linkq > 30 else T("red")
            self._vals["LINKQ"].setText(f"{linkq}%")
        else:
            lclr = T("faint")
            self._vals["LINKQ"].setText("--")
        set_qss(self._vals["LINKQ"],
                f"color:{lclr}; font-size:14px; font-weight:700; font-family:{FONT_MONO};")
        self.lbl_link.setText(
            (f"RSSI {rssi} dBm" if rssi_ok else "RSSI N/A") + f"  ·  {voltage:.2f} V")
