"""
takeoff_panel.py — แผง Take off (Right Panel)

  • โหมด All / Sequential (แคปซูลสวิตช์ พร้อมคำอธิบาย)
  • ปุ่ม FLEET = เลือกโดรน "ทุกลำ" ทางฝั่งซ้ายให้อัตโนมัติ; กดยกเลิก = ล้างการเลือก
    แล้วกลับไปโหมดผู้ใช้คลิกเลือกเอง (Ctrl+Click เลือกหลายลำได้)
  • ความสูงดีฟอลต์ 20 m (ตั้งรายลำได้ในการ์ดโดรนฝั่งซ้าย)

ไม่มี checkbox เลือกลำในแผงนี้แล้ว — การเลือกโดรนทำที่การ์ดฝั่งซ้ายอย่างเดียว

emit:
  takeoff_requested(str mode)   กด TAKE OFF (app ไปดึงลำที่เลือกเอง)
  fleet_toggled(bool)           กด FLEET เปิด/ปิด
  changed()                     mode เปลี่ยน (ให้ summary อัปเดต)
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from ..core.theme import T, rgba, FONT_MONO, filled_btn
from .controls import Segmented


class TakeoffPanel(QWidget):
    takeoff_requested = pyqtSignal(str)    # mode
    fleet_toggled = pyqtSignal(bool)
    changed = pyqtSignal()

    def __init__(self, default_alt=20.0, parent=None):
        super().__init__(parent)
        self._default_alt = float(default_alt)
        self._fleet_on = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)

        # ── โหมด ──
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        ml = QLabel("MODE")
        ml.setStyleSheet(f"color:{T('faint')}; font-size:10px; font-weight:600;"
                         f" letter-spacing:1.2px;")
        mrow.addWidget(ml)
        self.seg_mode = Segmented(["All", "Sequential"], selected=0,
                                  accent=T("accent"), height=26, style="capsule")
        self.seg_mode.changed.connect(lambda _: self.changed.emit())
        mrow.addWidget(self.seg_mode, 1)
        v.addLayout(mrow)

        hint = QLabel(
            "• All — โดรนที่เลือกทุกลำขึ้นบินพร้อมกันทีเดียว\n"
            "• Sequential — Head ขึ้นก่อน แล้วลูกขบวนทยอยขึ้นตามลำดับ")
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{T('dim')}; font-size:10px; line-height:140%;"
            f" background:{rgba('#ffffff', 0.03)}; border-radius:6px; padding:5px 8px;")
        v.addWidget(hint)

        # ── FLEET toggle ──
        self.btn_fleet = QPushButton("◎  FLEET — เลือกทุกลำ")
        self.btn_fleet.setCheckable(True)
        self.btn_fleet.setMinimumHeight(34)
        self.btn_fleet.setCursor(Qt.PointingHandCursor)
        self.btn_fleet.setToolTip(
            "เปิด = ติ๊กเลือกโดรนทุกลำทางฝั่งซ้าย · ปิด = ล้างการเลือก แล้วคลิกเลือกเอง")
        self.btn_fleet.clicked.connect(self._on_fleet_clicked)
        self._style_fleet()
        v.addWidget(self.btn_fleet)

        self.lbl_sel = QLabel("ยังไม่ได้เลือกลำใด")
        self.lbl_sel.setWordWrap(True)
        self.lbl_sel.setStyleSheet(
            f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        v.addWidget(self.lbl_sel)

        sel_hint = QLabel("เลือกโดรนได้ที่การ์ดฝั่งซ้าย (Ctrl+Click = เลือกหลายลำ) "
                          "· ตั้ง ALT/SPACING รายลำได้ในการ์ดด้านล่าง")
        sel_hint.setWordWrap(True)
        sel_hint.setStyleSheet(f"color:{T('faint')}; font-size:9px;")
        v.addWidget(sel_hint)

        # ── ปุ่มสั่ง ──
        self.btn_takeoff = QPushButton("TAKE OFF")
        self.btn_takeoff.setMinimumHeight(40)
        self.btn_takeoff.setCursor(Qt.PointingHandCursor)
        self.btn_takeoff.setStyleSheet(filled_btn(T("green"), radius=8, font=12))
        self.btn_takeoff.clicked.connect(
            lambda: self.takeoff_requested.emit(self.mode()))
        v.addWidget(self.btn_takeoff)

    # ── FLEET ──
    def _style_fleet(self):
        if self._fleet_on:
            self.btn_fleet.setStyleSheet(
                f"QPushButton {{ background:{rgba(T('accent'), 0.28)};"
                f" border:1px solid {rgba(T('accent'), 0.75)}; border-radius:8px;"
                f" color:{T('text')}; font-size:11px; font-weight:800;"
                f" letter-spacing:0.5px; }}"
                f"QPushButton:hover {{ background:{rgba(T('accent'), 0.38)}; }}")
        else:
            self.btn_fleet.setStyleSheet(
                f"QPushButton {{ background:{rgba('#ffffff', 0.04)};"
                f" border:1px solid {rgba('#ffffff', 0.14)}; border-radius:8px;"
                f" color:{T('dim')}; font-size:11px; font-weight:700;"
                f" letter-spacing:0.5px; }}"
                f"QPushButton:hover {{ background:{rgba('#ffffff', 0.09)};"
                f" color:{T('text')}; }}")

    def _on_fleet_clicked(self):
        self._fleet_on = self.btn_fleet.isChecked()
        self.btn_fleet.setText("◉  FLEET — เลือกทุกลำแล้ว" if self._fleet_on
                               else "◎  FLEET — เลือกทุกลำ")
        self._style_fleet()
        self.fleet_toggled.emit(self._fleet_on)

    def fleet_on(self):
        return self._fleet_on

    def set_fleet_on(self, on, emit=False):
        """ตั้งสถานะปุ่ม FLEET จากโค้ด (เช่นผู้ใช้ไปคลิกเลือกเองทีหลัง)"""
        on = bool(on)
        self._fleet_on = on
        self.btn_fleet.blockSignals(True)
        self.btn_fleet.setChecked(on)
        self.btn_fleet.blockSignals(False)
        self.btn_fleet.setText("◉  FLEET — เลือกทุกลำแล้ว" if on
                               else "◎  FLEET — เลือกทุกลำ")
        self._style_fleet()
        if emit:
            self.fleet_toggled.emit(on)

    # ── state ──
    def set_selection_text(self, ids):
        ids = sorted(int(i) for i in ids)
        if not ids:
            self.lbl_sel.setText("ยังไม่ได้เลือกลำใด")
            self.lbl_sel.setStyleSheet(
                f"color:{T('faint')}; font-size:10px; font-family:{FONT_MONO};")
        else:
            self.lbl_sel.setText(
                f"เลือก {len(ids)} ลำ · " + ", ".join(f"D{i}" for i in ids))
            self.lbl_sel.setStyleSheet(
                f"color:{T('green')}; font-size:10px; font-weight:700;"
                f" font-family:{FONT_MONO};")

    def set_default_alt(self, alt):
        self._default_alt = float(alt)

    def mode(self):
        return "all" if self.seg_mode.current() == 0 else "sequential"
