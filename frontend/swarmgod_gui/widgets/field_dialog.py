"""
field_dialog.py — เปิด/ปิด Field Tablet และแสดง PIN + ที่อยู่ให้พิมพ์บนแท็บเล็ต

เฟส 1 = ดูอย่างเดียว (docs/FIELD_TABLET.md) — กล่องนี้จึงย้ำตลอดว่าแท็บเล็ต
สั่งงานไม่ได้ และ E-STOP ของจริงอยู่ที่คอมควบคุมกับรีโมท RC เท่านั้น

ตัวกล่องไม่ถือ state เอง ทุกอย่างอ่าน/สั่งผ่าน callback ที่ GroundStation ส่งมา
เพื่อให้ยังเป็นแหล่งความจริงเดียว
"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
)

from ..core.theme import T, rgba, hairline, ghost_btn, filled_btn, tinted_btn


class FieldTabletDialog(QDialog):
    def __init__(self, parent, *, is_on, start, stop, pin_info, kick, clients):
        super().__init__(parent)
        self._is_on = is_on          # () -> bool
        self._start = start          # () -> (ok: bool, msg: str)
        self._stop = stop            # () -> None
        self._pin_info = pin_info    # () -> (pin, seconds_left, urls)
        self._kick = kick            # () -> int
        self._clients = clients      # () -> int

        self.setWindowTitle("Field Tablet — ให้แท็บเล็ตในวง LAN เปิดดู")
        self.setModal(True)
        self.resize(520, 430)
        self.setStyleSheet(f"QDialog {{ background:{T('panel')}; }}")

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(11)

        # ── คำเตือนถาวร ──
        warn = QLabel(
            "โหมดนี้ <b>ดูอย่างเดียว — แท็บเล็ตสั่งงานโดรนไม่ได้</b><br/>"
            "E-STOP ของจริงอยู่ที่คอมควบคุมเครื่องนี้และรีโมท RC เท่านั้น")
        warn.setWordWrap(True)
        warn.setStyleSheet(
            f"color:{T('amber')}; background:{rgba(T('amber'), 0.10)};"
            f" border:1px solid {rgba(T('amber'), 0.35)}; border-radius:8px;"
            f" padding:10px 12px; font-size:12px;")
        v.addWidget(warn)

        self.lbl_state = QLabel("")
        self.lbl_state.setStyleSheet("font-size:15px; font-weight:800;")
        v.addWidget(self.lbl_state)

        # ── PIN ──
        self.box_pin = QFrame()
        self.box_pin.setStyleSheet(
            f"background:{T('panel3')}; border:1px solid {hairline()};"
            f" border-radius:10px;")
        pv = QVBoxLayout(self.box_pin)
        pv.setContentsMargins(14, 12, 14, 12)
        pv.setSpacing(6)
        cap = QLabel("PIN สำหรับจับคู่ — พิมพ์บนแท็บเล็ต")
        cap.setStyleSheet(f"color:{T('faint')}; font-size:11px; letter-spacing:0.6px;")
        pv.addWidget(cap)
        self.lbl_pin = QLabel("——————")
        self.lbl_pin.setStyleSheet(
            f"color:{T('accent')}; font-size:36px; font-weight:800;"
            f" letter-spacing:10px;")
        pv.addWidget(self.lbl_pin)
        self.lbl_pin_ttl = QLabel("")
        self.lbl_pin_ttl.setStyleSheet(f"color:{T('faint')}; font-size:11px;")
        pv.addWidget(self.lbl_pin_ttl)
        v.addWidget(self.box_pin)

        # ── ที่อยู่ ──
        self.lbl_url_cap = QLabel("เปิดบราวเซอร์บนแท็บเล็ตไปที่")
        self.lbl_url_cap.setStyleSheet(
            f"color:{T('faint')}; font-size:11px; letter-spacing:0.6px;")
        v.addWidget(self.lbl_url_cap)
        self.lbl_urls = QLabel("")
        self.lbl_urls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_urls.setStyleSheet(
            f"color:{T('text')}; font-size:16px; font-weight:700;"
            f" background:{T('panel3')}; border:1px solid {hairline()};"
            f" border-radius:8px; padding:10px 12px;")
        v.addWidget(self.lbl_urls)

        self.lbl_clients = QLabel("")
        self.lbl_clients.setStyleSheet(f"color:{T('faint')}; font-size:12px;")
        v.addWidget(self.lbl_clients)

        v.addStretch(1)

        # ── ปุ่ม ──
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_toggle = QPushButton("")
        self.btn_toggle.setFixedHeight(38)
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle)
        row.addWidget(self.btn_toggle, 2)

        self.btn_newpin = QPushButton("PIN ใหม่")
        self.btn_newpin.setFixedHeight(38)
        self.btn_newpin.setCursor(Qt.PointingHandCursor)
        self.btn_newpin.setStyleSheet(ghost_btn(radius=9, font=12))
        self.btn_newpin.clicked.connect(self._new_pin)
        row.addWidget(self.btn_newpin, 1)

        self.btn_kick = QPushButton("ตัดทุกเครื่อง")
        self.btn_kick.setFixedHeight(38)
        self.btn_kick.setCursor(Qt.PointingHandCursor)
        self.btn_kick.setStyleSheet(tinted_btn(T("red"), radius=9, font=12))
        self.btn_kick.setToolTip("ตัด session ทุกเครื่องทันที และทำให้ PIN เดิมใช้ไม่ได้")
        self.btn_kick.clicked.connect(self._kick_all)
        row.addWidget(self.btn_kick, 1)
        v.addLayout(row)

        close = QPushButton("ปิดหน้าต่าง")
        close.setFixedHeight(32)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(ghost_btn(radius=8, font=12))
        close.clicked.connect(self.accept)
        v.addWidget(close)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh)
        self._tick.start(1000)
        self.refresh()

    # ── actions ──
    def _toggle(self):
        if self._is_on():
            self._stop()
        else:
            ok, msg = self._start()
            if not ok:
                self.lbl_state.setText(msg)
                self.lbl_state.setStyleSheet(
                    f"color:{T('red')}; font-size:15px; font-weight:800;")
                return
        self.refresh()

    def _new_pin(self):
        if self._is_on():
            self._pin_info(regen=True)
            self.refresh()

    def _kick_all(self):
        self._kick()
        self.refresh()

    # ── render ──
    def refresh(self):
        on = self._is_on()
        self.btn_toggle.setText("ปิด LAN" if on else "เปิด LAN")
        self.btn_toggle.setStyleSheet(
            tinted_btn(T("red"), radius=9, font=13, weight=800) if on
            else filled_btn(T("accent"), radius=9, font=13))
        self.btn_newpin.setEnabled(on)
        self.btn_kick.setEnabled(on)
        self.box_pin.setVisible(on)
        self.lbl_url_cap.setVisible(on)
        self.lbl_urls.setVisible(on)

        if not on:
            self.lbl_state.setText("● ปิดอยู่ — ไม่มีการเปิดรับการเชื่อมต่อใด ๆ")
            self.lbl_state.setStyleSheet(
                f"color:{T('faint')}; font-size:15px; font-weight:800;")
            self.lbl_clients.setText(
                "เปิดแล้วเครื่องอื่นในวง WiFi เดียวกันจะเข้าดูได้ (ต้องใส่ PIN ก่อน)")
            return

        pin, ttl, urls = self._pin_info()
        self.lbl_state.setText("● เปิดอยู่ — แท็บเล็ตในวง LAN เข้าดูได้")
        self.lbl_state.setStyleSheet(
            f"color:{T('green')}; font-size:15px; font-weight:800;")
        self.lbl_pin.setText(" ".join(pin) if pin else "หมดอายุ")
        if pin:
            self.lbl_pin_ttl.setText("ใช้ได้อีก %d:%02d นาที · ใส่ผิดหลายครั้ง PIN จะถูกยกเลิก"
                                     % (ttl // 60, ttl % 60))
        else:
            self.lbl_pin_ttl.setText("PIN หมดอายุหรือถูกยกเลิก — กด “PIN ใหม่”")
        self.lbl_urls.setText("\n".join(urls))
        n = self._clients()
        self.lbl_clients.setText("เครื่องที่จับคู่แล้ว: %d" % n)

    def closeEvent(self, e):
        self._tick.stop()
        super().closeEvent(e)
