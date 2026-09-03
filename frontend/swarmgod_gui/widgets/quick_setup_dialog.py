"""Quick Setup wizard for SwarmGod cockpit.

This dialog only prepares cockpit/UI configuration. It never arms, takes off,
or invokes flight-mutating RPCs. Connection is delegated to the existing
CONNECT dialog through a callback supplied by GroundStation.
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QWidget, QFrame, QCheckBox, QButtonGroup, QRadioButton, QDoubleSpinBox,
    QComboBox, QScrollArea, QSizePolicy,
)

from ..core.theme import T, rgba, filled_btn, ghost_btn, tinted_btn


OPERATIONS = [
    ("direct", "DIRECT / TAKEOFF", "บินตรงหรือขึ้นบินแล้วควบคุมจากหน้า Cockpit เดิม"),
    ("waypoint", "WAYPOINT ROUTE", "วางเส้นทางบนแผนที่แล้วตรวจ Pre-flight ก่อน Execute"),
    ("formation", "FORMATION", "เตรียมรูปขบวน/ระยะ/ความเร็ว โดยไม่ Form Up อัตโนมัติ"),
    ("swarm", "SWARM", "เตรียมค่าฝูงไว้ก่อน แต่ไม่เริ่ม Swarm จาก Quick Setup"),
]


class QuickSetupDialog(QDialog):
    """Six-step configuration-only wizard."""

    def __init__(self, parent=None, *, state=None, state_cb=None, connect_cb=None, safety_cb=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Setup")
        self.setModal(True)
        self.resize(860, 620)
        self.setMinimumSize(760, 560)
        self._state = dict(state or {})
        self._state_cb = state_cb
        self._connect_cb = connect_cb
        self._safety_cb = safety_cb
        self._step = 0
        self.result_config = None
        self._drone_checks = {}
        self._op_buttons = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("QUICK SETUP")
        title.setStyleSheet(f"color:{T('text')}; font-size:20px; font-weight:900; letter-spacing:1px;")
        head.addWidget(title)
        head.addStretch(1)
        self.lbl_step = QLabel("")
        self.lbl_step.setStyleSheet(f"color:{T('accent')}; font-size:12px; font-weight:800;")
        head.addWidget(self.lbl_step)
        root.addLayout(head)

        self.lbl_rail = QLabel("")
        self.lbl_rail.setWordWrap(True)
        self.lbl_rail.setStyleSheet(
            f"color:{T('dim')}; background:{rgba('#ffffff', 0.045)};"
            f" border:1px solid {rgba('#ffffff', 0.07)}; border-radius:10px;"
            " padding:9px 12px; font-size:11px; font-weight:700;")
        root.addWidget(self.lbl_rail)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_connect())
        self.stack.addWidget(self._page_select())
        self.stack.addWidget(self._page_operation())
        self.stack.addWidget(self._page_configure())
        self.stack.addWidget(self._page_safety())
        self.stack.addWidget(self._page_review())
        root.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        self.btn_cancel = QPushButton("CANCEL")
        self.btn_back = QPushButton("BACK")
        self.btn_next = QPushButton("NEXT")
        self.btn_cancel.setStyleSheet(ghost_btn(radius=9, font=11))
        self.btn_back.setStyleSheet(ghost_btn(radius=9, font=11))
        self.btn_next.setStyleSheet(filled_btn(T("accent"), radius=9, font=11))
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_back.clicked.connect(self._back)
        self.btn_next.clicked.connect(self._next)
        nav.addWidget(self.btn_cancel)
        nav.addStretch(1)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        root.addLayout(nav)

        self._load_state()
        self._show_step(0)

    @staticmethod
    def _page_shell(title, text):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(12)
        h = QLabel(title)
        h.setStyleSheet(f"color:{T('text')}; font-size:18px; font-weight:850;")
        v.addWidget(h)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{T('dim')}; font-size:11px; line-height:140%;")
        v.addWidget(d)
        return w, v

    @staticmethod
    def _status_box(text=""):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setStyleSheet(
            f"color:{T('text')}; background:{rgba('#ffffff', 0.04)};"
            f" border:1px solid {rgba('#ffffff', 0.07)}; border-radius:9px;"
            " padding:12px; font-size:11px;")
        return lab

    def _page_connect(self):
        w, v = self._page_shell(
            "1 · CONNECT DRONES",
            "ตรวจว่ามีโดรนตัวไหน online แล้ว ถ้ายังไม่มีให้เปิดหน้าต่าง CONNECT เดิมจากตรงนี้ "
            "Quick Setup จะไม่สร้างเส้นทางเชื่อมต่อใหม่เอง")
        self.lbl_connect = self._status_box()
        v.addWidget(self.lbl_connect)
        b = QPushButton("+ OPEN CONNECT")
        b.setMinimumHeight(38)
        b.setStyleSheet(tinted_btn(T("accent"), radius=9, font=11))
        b.clicked.connect(self._open_connect)
        v.addWidget(b)
        v.addStretch(1)
        return w

    def _page_select(self):
        w, v = self._page_shell(
            "2 · SELECT DRONES",
            "เลือกโดรนที่จะใช้กับแผนนี้ การเลือกตรงนี้เปลี่ยนเฉพาะ selection ใน Cockpit เมื่อกด APPLY")
        self.select_container = QWidget()
        self.select_layout = QVBoxLayout(self.select_container)
        self.select_layout.setContentsMargins(0, 0, 0, 0)
        self.select_layout.setSpacing(7)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.select_container)
        v.addWidget(scroll, 1)
        row = QHBoxLayout()
        ba = QPushButton("SELECT ONLINE")
        bn = QPushButton("CLEAR")
        ba.setStyleSheet(ghost_btn(radius=8, font=10))
        bn.setStyleSheet(ghost_btn(radius=8, font=10))
        ba.clicked.connect(self._select_online)
        bn.clicked.connect(lambda: [c.setChecked(False) for c in self._drone_checks.values()])
        row.addWidget(ba)
        row.addWidget(bn)
        row.addStretch(1)
        v.addLayout(row)
        return w

    def _page_operation(self):
        w, v = self._page_shell(
            "3 · CHOOSE OPERATION",
            "เลือกว่าจะเตรียมงานแบบไหน Quick Setup จะไม่ Execute mission และไม่ส่งคำสั่งการบิน")
        self._op_group = QButtonGroup(self)
        self._op_group.setExclusive(True)
        for i, (key, label, desc) in enumerate(OPERATIONS):
            r = QRadioButton(f"{label}    —    {desc}")
            r.setProperty("operation", key)
            r.setMinimumHeight(42)
            r.setStyleSheet(
                f"QRadioButton {{ color:{T('text')}; font-size:11px; padding:8px; }}"
                f"QRadioButton:hover {{ background:{rgba('#ffffff', 0.04)}; border-radius:8px; }}")
            self._op_group.addButton(r, i)
            self._op_buttons[key] = r
            v.addWidget(r)
        v.addStretch(1)
        return w

    def _spin(self, value, minv, maxv, suffix, step=1.0, decimals=1):
        s = QDoubleSpinBox()
        s.setRange(minv, maxv)
        s.setValue(value)
        s.setSingleStep(step)
        s.setDecimals(decimals)
        s.setSuffix(suffix)
        s.setMinimumHeight(34)
        s.setStyleSheet(
            f"QDoubleSpinBox {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {rgba('#ffffff', 0.12)}; border-radius:8px; padding:4px 9px; }}")
        return s

    def _field_row(self, label, widget):
        row = QHBoxLayout()
        lab = QLabel(label)
        lab.setStyleSheet(f"color:{T('dim')}; font-size:11px; font-weight:700;")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(widget)
        return row

    def _page_configure(self):
        w, v = self._page_shell(
            "4 · CONFIGURE",
            "ตั้งค่าพื้นฐานของแผน ค่าพวกนี้จะสะท้อนกลับไปยัง control เดิมใน Cockpit เมื่อกด APPLY")
        self.sp_takeoff = self._spin(20, 1, 120, " m", 1, 0)
        self.sp_speed = self._spin(3, 0.5, 15, " m/s", 0.5, 1)
        self.sp_spacing = self._spin(12, 1, 50, " m", 1, 0)
        self.sp_offset = self._spin(5, 0, 30, " m", 1, 0)
        self.sp_form_speed = self._spin(4, 0.5, 15, " m/s", 0.5, 1)
        self.cmb_formation = QComboBox()
        self.cmb_formation.addItems(["Wedge", "Line", "Column", "Diamond", "Echelon"])
        self.cmb_formation.setMinimumHeight(34)
        self.cmb_formation.setStyleSheet(
            f"QComboBox {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {rgba('#ffffff', 0.12)}; border-radius:8px; padding:4px 9px; }}")
        v.addLayout(self._field_row("TAKEOFF ALTITUDE", self.sp_takeoff))
        v.addLayout(self._field_row("MISSION SPEED", self.sp_speed))
        v.addSpacing(6)
        self.lbl_form_fields = QLabel("FORMATION / SWARM SETTINGS")
        self.lbl_form_fields.setStyleSheet(f"color:{T('amber')}; font-size:10px; font-weight:800;")
        v.addWidget(self.lbl_form_fields)
        v.addLayout(self._field_row("FORMATION", self.cmb_formation))
        v.addLayout(self._field_row("SPACING", self.sp_spacing))
        v.addLayout(self._field_row("ALTITUDE OFFSET", self.sp_offset))
        v.addLayout(self._field_row("FORMATION SPEED", self.sp_form_speed))
        note = QLabel("Leader / Head ยังไม่เปลี่ยนจาก Quick Setup เพื่อป้องกัน SetLeader RPC โดยไม่ตั้งใจ — แก้จากหน้าเดิมหลัง Apply")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{T('yellow')}; font-size:10px;")
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _page_safety(self):
        w, v = self._page_shell(
            "5 · SAFETY CHECK",
            "หน้านี้อ่านสถานะเท่านั้น ไม่มีการทดสอบที่ยิงคำสั่งอัตโนมัติ ถ้าต้องการ SYSTEM TEST ให้ทำจาก PRE-FLIGHT เดิม")
        self.lbl_safety = self._status_box()
        v.addWidget(self.lbl_safety, 1)
        b = QPushButton("REFRESH STATUS")
        b.setStyleSheet(ghost_btn(radius=8, font=10))
        b.clicked.connect(self._refresh_safety)
        v.addWidget(b)
        return w

    def _page_review(self):
        w, v = self._page_shell(
            "6 · REVIEW & APPLY",
            "ตรวจแผนก่อนนำค่ากลับไปใส่หน้า Cockpit เดิม ปุ่ม APPLY ไม่ ARM และไม่ TAKEOFF")
        self.lbl_review = self._status_box()
        v.addWidget(self.lbl_review, 1)
        return w

    def _load_state(self):
        self.sp_takeoff.setValue(float(self._state.get("takeoff_alt", 20.0)))
        self.sp_speed.setValue(float(self._state.get("speed", 3.0)))
        self.sp_spacing.setValue(float(self._state.get("spacing", 12.0)))
        self.sp_offset.setValue(float(self._state.get("offset", 5.0)))
        self.sp_form_speed.setValue(float(self._state.get("form_speed", 4.0)))
        self.cmb_formation.setCurrentIndex(max(0, min(4, int(self._state.get("formation", 0)))))
        op = str(self._state.get("operation") or "direct")
        self._op_buttons.get(op, self._op_buttons["direct"]).setChecked(True)
        self._rebuild_drone_checks()
        self._refresh_connect()

    def _rebuild_drone_checks(self):
        while self.select_layout.count():
            item = self.select_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._drone_checks = {}
        selected = {int(x) for x in self._state.get("selected_ids", [])}
        online = {int(x) for x in self._state.get("online_ids", [])}
        drones = self._state.get("drones", []) or []
        if not drones:
            e = QLabel("ยังไม่มี Drone ใน Fleet — กลับ Step 1 แล้วกด OPEN CONNECT")
            e.setStyleSheet(f"color:{T('faint')}; padding:10px;")
            self.select_layout.addWidget(e)
        for d in drones:
            did = int(d.get("id", 0))
            name = d.get("name") or f"Drone {did}"
            suffix = "● ONLINE" if did in online else "○ OFFLINE"
            c = QCheckBox(f"{name}    {suffix}")
            c.setChecked(did in selected)
            c.setStyleSheet(f"color:{T('text')}; font-size:11px; padding:7px;")
            self._drone_checks[did] = c
            self.select_layout.addWidget(c)
        self.select_layout.addStretch(1)

    def _open_connect(self):
        if callable(self._connect_cb):
            self._connect_cb()
        if callable(self._state_cb):
            try:
                self.refresh_state(self._state_cb() or {})
                return
            except Exception:
                pass
        self._refresh_connect()

    def _refresh_connect(self):
        online = [int(x) for x in self._state.get("online_ids", [])]
        drones = self._state.get("drones", []) or []
        self.lbl_connect.setText(
            f"Fleet known: {len(drones)} drones\n"
            f"Online now: {len(online)}\n\n"
            "กด OPEN CONNECT เพื่อเพิ่มการเชื่อมต่อ แล้วปิดหน้าต่าง CONNECT เพื่อกลับมาที่ Quick Setup")

    def refresh_state(self, state):
        """Allow the host to refresh fleet/telemetry state without recreating the dialog."""
        self._state.update(dict(state or {}))
        self._rebuild_drone_checks()
        self._refresh_connect()
        self._refresh_safety()

    def _select_online(self):
        online = {int(x) for x in self._state.get("online_ids", [])}
        for did, c in self._drone_checks.items():
            c.setChecked(did in online)

    def _selected_ids(self):
        return sorted(did for did, c in self._drone_checks.items() if c.isChecked())

    def _operation(self):
        for key, b in self._op_buttons.items():
            if b.isChecked():
                return key
        return "direct"

    def _safety_lines(self):
        if callable(self._safety_cb):
            try:
                info = dict(self._safety_cb() or {})
            except Exception as exc:
                return [f"Safety snapshot error: {exc}"]
        else:
            info = {}
        lines = []
        profile = str(info.get("profile") or "-")
        lines.append(f"Profile: {profile}")
        lines.append("HOME LOC: " + ("OK" if info.get("home_loc") else "MISSING / SETUP ONLY"))
        lines.append("mTLS: " + ("OK" if info.get("mtls") else "NOT READY"))
        lines.append("Control: " + ("UI" if info.get("ui_mode") else "REMOTE"))

        # Safety review must follow the targets currently checked in the wizard,
        # not whatever selection happened to be active before the dialog opened.
        selected = set(self._selected_ids())
        state_rows = [d for d in (self._state.get("drones", []) or [])
                      if int(d.get("id", 0)) in selected]
        if state_rows:
            drones = [{
                "id": int(d.get("id", 0)),
                "gps_fix": int(d.get("gps_fix", 0)),
                "sats": int(d.get("sat_count", 0)),
                "batt_pct": float(d.get("battery_pct", 0.0)),
                "link_quality": int(d.get("link_quality", 0)),
            } for d in state_rows]
        else:
            drones = []
        if not drones:
            lines.append("Drones: none selected")
        for d in drones:
            lines.append(
                f"Drone {d.get('id')}: GPS {d.get('gps_fix', 0)} / {d.get('sats', 0)} sats · "
                f"Battery {d.get('batt_pct', 0):.0f}% · Link {d.get('link_quality', 0)}%")
        return lines

    def _refresh_safety(self):
        self.lbl_safety.setText("\n".join(self._safety_lines()))

    def _review_text(self):
        op_names = {k: lab for k, lab, _ in OPERATIONS}
        ids = self._selected_ids()
        lines = [
            f"Targets: {', '.join('Drone ' + str(x) for x in ids) if ids else 'NONE'}",
            f"Operation: {op_names.get(self._operation(), self._operation())}",
            f"Takeoff altitude: {self.sp_takeoff.value():.0f} m",
            f"Mission speed: {self.sp_speed.value():.1f} m/s",
        ]
        if self._operation() in ("formation", "swarm"):
            lines += [
                f"Formation: {self.cmb_formation.currentText()}",
                f"Spacing: {self.sp_spacing.value():.0f} m",
                f"Altitude offset: {self.sp_offset.value():.0f} m",
                f"Formation speed: {self.sp_form_speed.value():.1f} m/s",
                "Leader: unchanged by Quick Setup (safe default)",
            ]
        lines += ["", "SAFETY SNAPSHOT"] + self._safety_lines()
        lines += ["", "APPLY = configuration only · NO ARM · NO TAKEOFF · NO mission execution"]
        return "\n".join(lines)

    def _show_step(self, step):
        self._step = max(0, min(5, int(step)))
        self.stack.setCurrentIndex(self._step)
        names = ["CONNECT", "SELECT", "OPERATION", "CONFIGURE", "SAFETY", "REVIEW"]
        self.lbl_step.setText(f"STEP {self._step + 1} / 6")
        rail = []
        for i, name in enumerate(names):
            if i < self._step:
                rail.append(f"✓ {name}")
            elif i == self._step:
                rail.append(f"● {name}")
            else:
                rail.append(f"○ {name}")
        self.lbl_rail.setText("   →   ".join(rail))
        self.btn_back.setEnabled(self._step > 0)
        self.btn_next.setText("APPLY SETUP" if self._step == 5 else "NEXT")
        if self._step == 4:
            self._refresh_safety()
        if self._step == 5:
            self.lbl_review.setText(self._review_text())

    def _back(self):
        self._show_step(self._step - 1)

    def _next(self):
        if self._step == 1 and not self._selected_ids():
            self.lbl_step.setText("SELECT AT LEAST 1 DRONE")
            return
        if self._step < 5:
            self._show_step(self._step + 1)
            return
        self.result_config = {
            "selected_ids": self._selected_ids(),
            "operation": self._operation(),
            "takeoff_alt": float(self.sp_takeoff.value()),
            "speed": float(self.sp_speed.value()),
            "formation": int(self.cmb_formation.currentIndex()),
            "spacing": float(self.sp_spacing.value()),
            "offset": float(self.sp_offset.value()),
            "form_speed": float(self.sp_form_speed.value()),
        }
        self.accept()
