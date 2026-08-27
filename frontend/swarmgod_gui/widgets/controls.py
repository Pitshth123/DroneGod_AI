"""
controls.py — reusable UI controls สไตล์ SPA ดาร์ค
- SliderField : ตัวควบคุมค่าตัวเลข [−] [slider] [ช่องพิมพ์+หน่วย] [+] (ลาก/กด/พิมพ์ได้)
- Segmented / CapsuleSwitch : แคปซูลสวิตช์สไตล์ iOS (เช่น UI/REMOTE, Selected/Fleet)
- AccordionSection : หมวดพับเก็บได้ (header กดเพื่อกาง/ยุบ)
"""
from PyQt5.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect
from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QDoubleSpinBox, QButtonGroup, QSizePolicy,
)

from ..core.theme import T, rgba


def _slider_qss(accent: str) -> str:
    return (
        f"QSlider::groove:horizontal {{ height:4px; background:{rgba('#ffffff', 0.12)};"
        f" border-radius:2px; }}"
        f"QSlider::sub-page:horizontal {{ background:{accent}; border-radius:2px; }}"
        f"QSlider::add-page:horizontal {{ background:{rgba('#ffffff', 0.12)}; border-radius:2px; }}"
        f"QSlider::handle:horizontal {{ background:#ffffff; width:16px; height:16px;"
        f" margin:-7px 0; border-radius:8px; }}"
        f"QSlider::handle:horizontal:hover {{ background:{accent}; }}"
    )


def _step_btn_qss() -> str:
    return (
        f"QPushButton {{ background:{rgba('#ffffff', 0.08)}; border:none; border-radius:8px;"
        f" color:{T('text')}; font-size:18px; font-weight:800; }}"
        f"QPushButton:hover {{ background:{rgba('#ffffff', 0.16)}; }}"
        f"QPushButton:pressed {{ background:{rgba('#ffffff', 0.22)}; }}"
        f"QPushButton:disabled {{ color:{T('faint')}; background:{rgba('#ffffff', 0.04)}; }}"
    )


class SliderField(QWidget):
    """ค่าตัวเลข: ลาก slider / กด ± / พิมพ์ค่าเองได้ — emit valueChanged(float)"""
    valueChanged = pyqtSignal(float)

    def __init__(self, label="", minv=0.0, maxv=100.0, value=0.0, step=1.0,
                 unit="", decimals=1, accent=None, parent=None):
        super().__init__(parent)
        accent = accent or T("green")
        self._min, self._max, self._step = float(minv), float(maxv), float(step)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(7)
        if label:
            lab = QLabel(label)
            lab.setStyleSheet(f"color:{T('dim')}; font-size:11px; letter-spacing:0.5px;")
            v.addWidget(lab)

        row = QHBoxLayout()
        row.setSpacing(7)
        self.btn_minus = QPushButton("\u2212")
        self.btn_plus = QPushButton("+")
        for b in (self.btn_minus, self.btn_plus):
            b.setFixedSize(30, 30)
            b.setStyleSheet(_step_btn_qss())

        self.slider = QSlider(Qt.Horizontal)
        self._n = max(1, int(round((self._max - self._min) / self._step)))
        self.slider.setRange(0, self._n)
        self.slider.setStyleSheet(_slider_qss(accent))
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(self._min, self._max)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(self._step)
        self.spin.setValue(float(value))
        self.spin.setSuffix((" " + unit) if unit else "")
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setFixedWidth(88)
        self.spin.setAlignment(Qt.AlignCenter)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        self.btn_minus.clicked.connect(lambda: self.spin.stepBy(-1))
        self.btn_plus.clicked.connect(lambda: self.spin.stepBy(1))
        self._sync_slider(float(value))

        row.addWidget(self.btn_minus)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        row.addWidget(self.btn_plus)
        v.addLayout(row)

    def _v2s(self, val: float) -> int:
        return int(round((val - self._min) / self._step))

    def _s2v(self, s: int) -> float:
        return self._min + s * self._step

    def _sync_slider(self, val: float):
        self.slider.blockSignals(True)
        self.slider.setValue(self._v2s(val))
        self.slider.blockSignals(False)

    def _on_slider(self, s: int):
        val = self._s2v(s)
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self.valueChanged.emit(val)

    def _on_spin(self, val: float):
        self._sync_slider(val)
        self.valueChanged.emit(val)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, val: float):
        self.spin.setValue(float(val))


class Segmented(QFrame):
    """แคปซูลสวิตช์หลายตัวเลือก (exclusive) — emit changed(index)

    style:
      - \"capsule\"  (default) แคปซูลเต็มโค้ง สไตล์ iOS
      - \"pill\"     มุมโค้งปานกลาง (แท็บ filter)
    """
    changed = pyqtSignal(int)

    def __init__(self, options, selected=0, accent=None, height=32,
                 style="capsule", accents=None, compact=False, parent=None):
        super().__init__(parent)
        self._accent = accent or T("accent")
        self._accents = list(accents) if accents else None
        self._height = max(26, int(height))
        self._style = style
        self._compact = bool(compact)
        radius = self._height // 2 if style == "capsule" else 10
        inner_r = max(6, radius - 2)

        self.setObjectName("Segmented")
        self.setFixedHeight(self._height + 6)
        self.setStyleSheet(
            f"#Segmented {{ background:{rgba('#ffffff', 0.07)}; border:1px solid {rgba('#ffffff', 0.06)};"
            f" border-radius:{radius + 3}px; }}")

        h = QHBoxLayout(self)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns = []
        self._inner_r = inner_r

        for i, opt in enumerate(options):
            b = QPushButton(str(opt))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(self._height)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if i == selected:
                b.setChecked(True)
            self._group.addButton(b, i)
            self._btns.append(b)
            h.addWidget(b, 1)

        self._group.idClicked.connect(self._on_clicked)
        self._apply_button_styles()

    def _option_accent(self, i: int) -> str:
        if self._accents and i < len(self._accents) and self._accents[i]:
            return self._accents[i]
        return self._accent

    def _apply_button_styles(self):
        pad = 8 if self._compact else 14
        font = 9 if self._compact else 11
        spacing = 0.3 if self._compact else 0.6
        for i, b in enumerate(self._btns):
            acc = self._option_accent(i)
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; border:none;"
                f" border-radius:{self._inner_r}px; color:{T('dim')};"
                f" padding:0 {pad}px; font-weight:700; font-size:{font}px;"
                f" letter-spacing:{spacing}px; }}"
                f"QPushButton:hover {{ color:{T('text')};"
                f" background:{rgba('#ffffff', 0.05)}; }}"
                f"QPushButton:checked {{ background:{acc}; color:#ffffff; }}"
                f"QPushButton:checked:hover {{ background:{acc}; color:#ffffff; }}"
            )

    def _on_clicked(self, idx: int):
        self._apply_button_styles()
        self.changed.emit(idx)

    def current(self) -> int:
        return self._group.checkedId()

    def setCurrent(self, idx: int):
        if 0 <= idx < len(self._btns):
            self._btns[idx].setChecked(True)
            self._apply_button_styles()


class CapsuleSwitch(QWidget):
    """แคปซูล 2 ฝั่ง — เม็ดสไลด์ไปมาแบบ iOS (UI ↔ REMOTE)"""
    changed = pyqtSignal(int)

    def __init__(self, left="UI", right="REMOTE", selected=0,
                 left_accent=None, right_accent=None, height=30, parent=None):
        super().__init__(parent)
        self._left = left
        self._right = right
        self._idx = int(selected)
        self._acc = [left_accent or T("accent"), right_accent or T("amber")]
        self._h = max(26, int(height))
        self._pad = 3
        self.setFixedSize(168, self._h + 6)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.setObjectName("CapsuleSwitch")
        self.setStyleSheet(
            f"#CapsuleSwitch {{ background:{rgba('#ffffff', 0.08)};"
            f" border:1px solid {rgba('#ffffff', 0.08)};"
            f" border-radius:{(self._h + 6) // 2}px; }}")

        # เม็ดสไลด์ด้านหลังป้าย
        self._thumb = QFrame(self)
        self._thumb.setObjectName("CapThumb")
        self._thumb.raise_()

        self._lab_l = QPushButton(left, self)
        self._lab_r = QPushButton(right, self)
        for b in (self._lab_l, self._lab_r):
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            b.setFocusPolicy(Qt.NoFocus)
        self._lab_l.clicked.connect(lambda: self.setCurrent(0))
        self._lab_r.clicked.connect(lambda: self.setCurrent(1))

        self._anim = QPropertyAnimation(self._thumb, b"geometry", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self._apply_colors()
        self._layout_parts(animate=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_parts(animate=False)

    def _half_w(self):
        return (self.width() - 2 * self._pad) // 2

    def _thumb_rect(self, idx: int) -> QRect:
        tw = self._half_w()
        th = self.height() - 2 * self._pad
        x = self._pad + (tw if idx else 0)
        return QRect(x, self._pad, tw, th)

    def _layout_parts(self, animate=True):
        tw = self._half_w()
        th = self.height() - 2 * self._pad
        self._lab_l.setGeometry(self._pad, self._pad, tw, th)
        self._lab_r.setGeometry(self._pad + tw, self._pad, tw, th)
        self._lab_l.raise_()
        self._lab_r.raise_()
        target = self._thumb_rect(self._idx)
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._thumb.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._anim.stop()
            self._thumb.setGeometry(target)

    def _apply_colors(self):
        acc = self._acc[self._idx]
        r = max(8, self._h // 2)
        self._thumb.setStyleSheet(
            f"#CapThumb {{ background:{acc}; border:none; border-radius:{r}px; }}")
        for i, b in enumerate((self._lab_l, self._lab_r)):
            on = (i == self._idx)
            col = "#ffffff" if on else T("dim")
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; border:none;"
                f" color:{col}; font-weight:700; font-size:11px;"
                f" letter-spacing:0.6px; }}"
                f"QPushButton:hover {{ color:{'#ffffff' if on else T('text')}; }}")

    def current(self) -> int:
        return self._idx

    def setCurrent(self, idx: int):
        idx = 0 if idx <= 0 else 1
        if idx == self._idx:
            # ค่าเดิมอยู่แล้ว — ห้ามยิง changed ซ้ำเด็ดขาด
            # slot ที่รับ changed มักเรียก setCurrent กลับมาเพื่อ sync สวิตช์
            # ถ้าตอนนั้นเม็ดยังสไลด์ไม่ถึงที่ (อนิเมชันกำลังวิ่ง) แล้วเรายิงซ้ำ
            # จะกลายเป็น changed → slot → setCurrent → changed วนไม่รู้จบจนแอปเด้ง
            if (self._anim.state() != QPropertyAnimation.Running
                    and self._thumb.geometry() != self._thumb_rect(idx)):
                self._layout_parts(animate=False)
            return
        self._idx = idx
        self._apply_colors()
        self._layout_parts(animate=True)
        self.changed.emit(idx)


class AccordionSection(QWidget):
    """หมวดพับเก็บได้ — หัวข้อเงียบแบบแผงเครื่องมือ"""
    toggled = pyqtSignal(bool)

    def __init__(self, title, accent=None, expanded=False, parent=None):
        super().__init__(parent)
        self._accent = accent or T("dim")
        self._expanded = expanded
        self._locked = False
        self._title = title

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QPushButton()
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setFixedHeight(36)
        self.header.clicked.connect(self._toggle)
        self._render_header()
        root.addWidget(self.header)

        self.body = QFrame()
        self.body.setObjectName("AccBody")
        self.body.setStyleSheet("#AccBody { background:transparent; border:none; }")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(2, 6, 2, 12)
        self.body_layout.setSpacing(9)
        root.addWidget(self.body)
        self.body.setVisible(expanded)

    def _render_header(self):
        chev = "▾" if self._expanded else "▸"
        col = T("text") if self._expanded else T("dim")
        lock = "  LOCKED" if self._locked else ""
        text = f"  {chev}  {self._title}{lock}".replace("&", "&&")
        self.header.setText(text)
        # เส้น hairline บาง + แถบซ้ายบางเมื่อกาง
        bar = self._accent if self._expanded else "transparent"
        self.header.setStyleSheet(
            f"QPushButton {{ background:transparent; border:none; text-align:left;"
            f" color:{col}; font-weight:600; font-size:11px; letter-spacing:1.2px;"
            f" padding:6px 4px 6px 0; border-top:1px solid {rgba('#ffffff', 0.06)};"
            f" border-left:2px solid {bar}; }}"
            f"QPushButton:hover {{ color:{T('text')}; background:{rgba('#ffffff', 0.03)}; }}")

    def _toggle(self):
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self._render_header()
        self.toggled.emit(self._expanded)

    def setExpanded(self, on: bool):
        if on != self._expanded:
            self._toggle()

    def set_locked(self, locked: bool):
        self._locked = locked
        self.body.setEnabled(not locked)
        self._render_header()

    def add_widget(self, w):
        self.body_layout.addWidget(w)

    def add_layout(self, l):
        self.body_layout.addLayout(l)
