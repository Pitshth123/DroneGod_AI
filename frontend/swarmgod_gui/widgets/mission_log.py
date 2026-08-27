"""
mission_log.py — แถบ log ด้านล่าง
tabs + กรองโดรนแบบ Stage pills สีๆ + search + pause/clear/export
"""
import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
    QButtonGroup, QWidget, QSizePolicy, QScrollArea,
)

from ..core.theme import T, rgba, FONT_MONO, hairline, stage_pill_qss, drone_color
from .controls import Segmented

_TABS = ["All", "Commands", "Alerts", "Telemetry"]

_SEV_COLOR = {
    "INFO": T("dim"), "SUCCESS": T("green"),
    "WARNING": T("amber"), "ERROR": T("red"),
}
_CAT_COLOR = {
    "COMMAND": T("accent"), "STATUS": T("dim"),
    "TELEMETRY": T("cyan"), "ALERT": T("amber"),
}


class MissionLog(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MissionLog")
        self.setStyleSheet(
            f"#MissionLog {{ background:{T('panel')}; border:1px solid {hairline()};"
            f" border-radius:10px; }}")
        self._entries = []
        self._paused = False
        self._tab = "All"
        self._query = ""
        self._vehicle = "All"
        self._alerts = 0
        self._pill_btns = {}  # key -> QPushButton

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(5)

        # ── header toolbar: สองแถวคงที่ ไม่มี horizontal scroll ──
        bar = QHBoxLayout()
        bar.setSpacing(7)
        title = QLabel("MISSION LOG")
        title.setStyleSheet(
            f"color:{T('dim')}; font-weight:700; font-size:11px; letter-spacing:1.2px;")
        bar.addWidget(title)

        self.tabs = Segmented(_TABS, selected=0, accent=T("accent"), height=24,
                              style="capsule", compact=True)
        # ให้แท็บประเภท log แสดงเต็มแถบเหมือนเดิมเมื่อมีพื้นที่พอ แต่ไม่ดัน
        # minimum width ของหน้าต่างตอนเปิดแผนที่ 3D เป็นครั้งแรก
        self.tabs.setMinimumWidth(0)
        self.tabs.setMaximumWidth(300)
        self.tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.tabs.changed.connect(self._on_tab)
        bar.addWidget(self.tabs, 1)
        bar.addStretch(1)
        v.addLayout(bar)

        tools = QHBoxLayout()
        tools.setSpacing(5)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search logs")
        self.search.setMinimumWidth(0)
        self.search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.search.setFixedHeight(26)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            f"QLineEdit {{ background:{T('panel3')}; color:{T('text')};"
            f" border:1px solid {hairline()}; border-radius:6px;"
            f" padding:2px 8px; font-size:11px; }}"
            f"QLineEdit:focus {{ border:1px solid {rgba(T('accent'), 0.55)}; }}")
        self.search.textChanged.connect(self._on_search)
        tools.addWidget(self.search, 1)

        self.btn_pause = self._tool("Pause")
        self.btn_pause.clicked.connect(self._toggle_pause)
        tools.addWidget(self.btn_pause)
        self.btn_clear = self._tool("Clear")
        self.btn_clear.clicked.connect(self.clear)
        tools.addWidget(self.btn_clear)
        self.btn_export = self._tool("Export")
        self.btn_export.clicked.connect(self._export)
        tools.addWidget(self.btn_export)
        v.addLayout(tools)

        # ── Stage-style drone filter pills ──
        # ชื่อ Drone 10/11 ต้องไม่ถูกบีบจนอ่านไม่ครบ: ปุ่มกว้างตามข้อความ และเลื่อน
        # เฉพาะแถบนี้แนวนอนเมื่อจำนวนโดรนมากกว่าพื้นที่ (table/toolbar ไม่เลื่อนตาม)
        self.pill_row = QHBoxLayout()
        self.pill_row.setSpacing(4)
        self.pill_row.setContentsMargins(0, 0, 0, 0)
        self._pill_group = QButtonGroup(self)
        self._pill_group.setExclusive(True)
        self._pill_group.buttonClicked.connect(self._on_pill)
        self.pill_holder = QWidget()
        self.pill_holder.setLayout(self.pill_row)
        self.pill_holder.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.pill_scroll = QScrollArea()
        self.pill_scroll.setObjectName("MissionLogVehicleFilters")
        self.pill_scroll.setWidget(self.pill_holder)
        self.pill_scroll.setWidgetResizable(False)
        self.pill_scroll.setFrameShape(QFrame.NoFrame)
        self.pill_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pill_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pill_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.pill_scroll.setFixedHeight(32)
        self.pill_scroll.setStyleSheet(
            "QScrollArea { border:none; background:transparent; }"
            f"QScrollBar:horizontal {{ height:4px; background:transparent; }}"
            f"QScrollBar::handle:horizontal {{ background:{rgba('#ffffff', 0.22)};"
            " border-radius:2px; min-width:24px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }")
        self._build_pills(["All", "System"])
        v.addWidget(self.pill_scroll)

        # ── table ──
        self.table = QTableWidget(0, 5)
        self.table.setMinimumWidth(0)
        self.table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.table.setHorizontalHeaderLabels(
            ["TIME", "VEHICLE", "CATEGORY", "SEVERITY", "MESSAGE"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.verticalHeader().setDefaultSectionSize(22)
        hh = self.table.horizontalHeader()
        for col, width in ((0, 64), (1, 92), (2, 88), (3, 72)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            hh.resizeSection(col, width)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setHighlightSections(False)
        hh.setFixedHeight(22)
        self.table.setStyleSheet(
            f"QTableWidget {{ background:transparent; border:none; color:{T('text')};"
            f" font-family:{FONT_MONO}; font-size:11px; gridline-color:transparent; }}"
            f"QHeaderView::section {{ background:transparent; color:{T('faint')};"
            f" border:none; border-bottom:1px solid {rgba('#ffffff', 0.08)};"
            f" padding:2px 6px; font-size:9px; letter-spacing:1px; font-weight:700; }}"
            f"QTableWidget::item {{ padding:1px 6px; border:none; }}")
        v.addWidget(self.table, 1)

    def _tool(self, text):
        b = QPushButton(text)
        b.setFixedHeight(28)
        b.setFixedWidth(58)
        b.setStyleSheet(
            f"QPushButton {{ background:{rgba('#ffffff', 0.06)}; border:none;"
            f" border-radius:6px; color:{T('dim')}; font-size:11px; font-weight:600;"
            f" padding:4px 7px; }}"
            f"QPushButton:hover {{ background:{rgba('#ffffff', 0.12)}; color:{T('text')}; }}")
        return b

    def _pill_color(self, key: str) -> str:
        if key == "All":
            return "#475569"  # slate
        if key == "System":
            return "#64748b"
        if key.startswith("Drone "):
            try:
                did = int(key.replace("Drone ", "").strip())
                return drone_color(did)
            except ValueError:
                return T("accent")
        return T("accent")

    def _build_pills(self, keys):
        # clear old
        while self.pill_row.count():
            item = self.pill_row.takeAt(0)
            w = item.widget()
            if w is not None:
                self._pill_group.removeButton(w)
                w.deleteLater()
        self._pill_btns.clear()

        for i, key in enumerate(keys):
            label = "All" if key == "All" else key
            if key.startswith("Drone "):
                label = key  # Drone 1
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(label)
            b.setProperty("veh_key", key)
            bg = self._pill_color(key)
            b.setStyleSheet(stage_pill_qss(bg, checked=(i == 0)))
            # ต้องอ่าน fontMetrics *หลัง* ตั้ง QSS เพราะ font ของแอปก่อน polish
            # ใหญ่กว่า font ที่วาดจริงหลายเท่า จนแถบเลื่อนไปไกลและดูเหมือนปุ่มหาย.
            b.ensurePolished()
            b.setFixedWidth(max(42, b.fontMetrics().horizontalAdvance(label) + 18))
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            if i == 0:
                b.setChecked(True)
            self._pill_group.addButton(b, i)
            self._pill_btns[key] = b
            self.pill_row.addWidget(b)
        self.pill_row.addStretch(1)
        # QScrollArea ต้องมีขนาด content ที่ชัดเจน มิฉะนั้นหลัง rebuild รายชื่อ
        # (ตอนเชื่อมต่อโดรน) อาจเหลือ viewport ว่าง แม้ปุ่มถูกสร้างแล้ว. คำนวณ
        # จากปุ่มจริงโดยตรง เพราะ QHBoxLayout มี stretch ท้ายแถว ทำให้ sizeHint
        # ก่อน layout pass แรกเป็นค่าต่ำกว่าความกว้างเนื้อหาจริง.
        buttons = tuple(self._pill_btns.values())
        margins = self.pill_row.contentsMargins()
        content_width = (margins.left() + margins.right()
                         + sum(button.width() for button in buttons)
                         + max(0, len(buttons) - 1) * self.pill_row.spacing())
        content_height = (margins.top() + margins.bottom()
                          + max((button.sizeHint().height() for button in buttons), default=0))
        self.pill_holder.setFixedSize(content_width, content_height)
        self.pill_holder.updateGeometry()
        self._vehicle = keys[0] if keys else "All"
        self._refresh_pill_styles()

    def _refresh_pill_styles(self):
        for key, b in self._pill_btns.items():
            b.setStyleSheet(stage_pill_qss(self._pill_color(key), checked=b.isChecked()))

    def _on_pill(self, btn):
        key = btn.property("veh_key") or "All"
        self._vehicle = key
        self._refresh_pill_styles()
        self._rebuild()

    def _on_tab(self, idx):
        self._tab = _TABS[idx]
        self._rebuild()

    def _on_search(self, text):
        self._query = text.strip().lower()
        self._rebuild()

    def set_vehicles(self, drone_ids):
        """อัปเดต Stage pills ตามโดรนที่มี"""
        cur = self._vehicle
        keys = ["All", "System"] + [f"Drone {d}" for d in sorted(drone_ids)]
        self._build_pills(keys)
        if cur in self._pill_btns:
            self._pill_btns[cur].setChecked(True)
            self._vehicle = cur
            self._refresh_pill_styles()

    def _toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText("Resume" if self._paused else "Pause")
        if not self._paused:
            self._rebuild()

    def _passes(self, e):
        _, vehicle, category, severity, message = e
        if self._tab == "Commands" and category != "COMMAND":
            return False
        if self._tab == "Alerts" and not (severity in ("WARNING", "ERROR") or category == "ALERT"):
            return False
        if self._tab == "Telemetry" and category != "TELEMETRY":
            return False
        if self._vehicle and self._vehicle != "All":
            v = (vehicle or "").strip()
            want = self._vehicle
            if want == "System":
                if v.lower() not in ("system", "", "sys"):
                    return False
            else:
                # จับชื่อแบบตรงตัวเท่านั้น: การหาเลข "1" แบบ substring เคยทำให้
                # เลือก Drone 1 แล้ว log ของ Drone 10 / Drone 11 ติดมาด้วย.
                if v.casefold() != want.casefold():
                    return False
        if self._query:
            hay = f"{vehicle} {category} {severity} {message}".lower()
            if self._query not in hay:
                return False
        return True

    def add(self, message, vehicle="System", category="STATUS", severity="INFO"):
        category = category.upper()
        severity = severity.upper()
        ts = time.strftime("%H:%M:%S")
        e = (ts, vehicle, category, severity, message)
        self._entries.append(e)
        if len(self._entries) > 600:
            self._entries = self._entries[-600:]
        if severity in ("WARNING", "ERROR") or category == "ALERT":
            self._alerts += 1
        if not self._paused and self._passes(e):
            self._append_row(e)
            self.table.scrollToBottom()

    def clear(self):
        self._entries = []
        self._alerts = 0
        self.table.setRowCount(0)

    def _rebuild(self):
        self.table.setRowCount(0)
        for e in self._entries:
            if self._passes(e):
                self._append_row(e)
        self.table.scrollToBottom()

    def _append_row(self, e):
        ts, vehicle, category, severity, message = e
        r = self.table.rowCount()
        self.table.insertRow(r)
        # vehicle ใช้สีโดรนถ้าเป็น Drone N
        vcolor = T("text")
        if vehicle.startswith("Drone "):
            try:
                did = int(vehicle.replace("Drone ", "").strip())
                vcolor = drone_color(did)
            except ValueError:
                pass
        items = [
            (ts, T("faint")),
            (vehicle, vcolor),
            (category, _CAT_COLOR.get(category, T("dim"))),
            (severity, _SEV_COLOR.get(severity, T("dim"))),
            (message, T("text")),
        ]
        for c, (txt, color) in enumerate(items):
            it = QTableWidgetItem(txt)
            it.setForeground(QColor(color))
            if c in (1, 2, 3):
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.table.setItem(r, c, it)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export log", "mission_log.csv", "CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("time,vehicle,category,severity,message\n")
                for ts, vehicle, cat, sev, msg in self._entries:
                    f.write(f'{ts},{vehicle},{cat},{sev},"{msg}"\n')
        except Exception:
            pass
