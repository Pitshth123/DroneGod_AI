"""PRE-FLIGHT SUMMARY V2: flight plan snapshot plus live execution flow."""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QScrollArea, QStackedWidget, QWidget,
)

from ..core.flight_progress import StepStatus
from ..core.theme import T, rgba, hairline, FONT_MONO

_STATUS_STYLE = {
    StepStatus.PENDING: ("○", T("faint")), StepStatus.ACTIVE: ("●", T("accent")),
    StepStatus.DONE: ("✓", T("green")), StepStatus.FAILED: ("!", T("red")),
    StepStatus.CANCELLED: ("×", T("amber")), StepStatus.SKIPPED: ("–", T("dim")),
}


class CommandSummaryBox(QFrame):
    """Cockpit renderer; it never decides business state itself."""
    cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CmdSummary")
        self.setStyleSheet(f"#CmdSummary {{ background:{T('panel')}; border:1px solid {hairline()}; border-radius:10px; }}")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._rows, self._run, self._pulse_on = [], None, False

        outer = QVBoxLayout(self); outer.setContentsMargins(10, 8, 10, 8); outer.setSpacing(6)
        head = QHBoxLayout(); head.setSpacing(6)
        title = QLabel("◇ FLIGHT SUMMARY")
        title.setStyleSheet(f"color:{T('amber')}; font-weight:700; font-size:11px; letter-spacing:1.2px;")
        head.addWidget(title); head.addStretch()
        self.btn_clear = QPushButton("Clear"); self.btn_clear.setFixedHeight(24); self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.setStyleSheet(f"QPushButton {{ background:{rgba('#ffffff', 0.06)}; border:none; border-radius:6px; color:{T('dim')}; font-size:10px; font-weight:600; padding:3px 10px; }} QPushButton:hover {{ background:{rgba('#ffffff', 0.12)}; color:{T('text')}; }}")
        self.btn_clear.clicked.connect(self.cleared.emit); head.addWidget(self.btn_clear); outer.addLayout(head)

        # Two compact views instead of one long mixed scroll.  The operator can
        # inspect the frozen plan without losing the live execution position.
        tabs = QHBoxLayout(); tabs.setSpacing(4)
        self.btn_plan = QPushButton("PRE-FLIGHT · 0")
        self.btn_active = QPushButton("ACTIVE · —")
        for button in (self.btn_plan, self.btn_active):
            button.setCheckable(True); button.setFixedHeight(29)
            button.setCursor(Qt.PointingHandCursor); tabs.addWidget(button, 1)
        self.btn_plan.clicked.connect(lambda: self._show_tab(0))
        self.btn_active.clicked.connect(lambda: self._show_tab(1))
        outer.addLayout(tabs)

        # Kept as state mirrors for callers/tests; status is presented inside
        # the tab captions so the header stays uncluttered.
        self.lbl_count = QLabel("0"); self.lbl_count.hide()
        self.lbl_state = QLabel("PLAN · LIVE"); self.lbl_state.hide()

        self._stack = QStackedWidget(); self._stack.setStyleSheet("background:transparent;")
        outer.addWidget(self._stack, 1)

        self._plan_holder = QWidget(); self._plan_holder.setStyleSheet("background:transparent;")
        self._plan_content = QVBoxLayout(self._plan_holder)
        self._plan_content.setContentsMargins(0, 2, 0, 0); self._plan_content.setSpacing(5)
        self._plan_content.setAlignment(Qt.AlignTop)
        self._plan_title = self._section_title("FLIGHT PLAN"); self._plan_content.addWidget(self._plan_title)
        self._plan_rows = QVBoxLayout(); self._plan_rows.setContentsMargins(0, 0, 0, 0); self._plan_rows.setSpacing(3); self._plan_content.addLayout(self._plan_rows)
        self._empty = QLabel("ยังไม่มีแผนก่อนบิน — เลือกลำ / ตั้ง Head / Take off / วาง Waypoint")
        self._empty.setWordWrap(True); self._empty.setStyleSheet(f"color:{T('faint')}; font-size:11px;"); self._plan_rows.addWidget(self._empty)
        plan_scroll = self._scroll(self._plan_holder); self._stack.addWidget(plan_scroll)

        self._active_holder = QWidget(); self._active_holder.setStyleSheet("background:transparent;")
        self._active_content = QVBoxLayout(self._active_holder)
        self._active_content.setContentsMargins(0, 2, 0, 0); self._active_content.setSpacing(4)
        self._active_content.setAlignment(Qt.AlignTop)
        self._flow_title = self._section_title("ยังไม่มีภารกิจที่กำลังทำงาน")
        self._active_content.addWidget(self._flow_title)
        self._timeline = QVBoxLayout(); self._timeline.setContentsMargins(0, 0, 0, 0); self._timeline.setSpacing(0); self._active_content.addLayout(self._timeline)
        active_scroll = self._scroll(self._active_holder); self._stack.addWidget(active_scroll)

        self._pulse = QTimer(self); self._pulse.setInterval(800); self._pulse.timeout.connect(self._toggle_pulse)
        self.btn_active.setEnabled(False)
        self._show_tab(0)

    @staticmethod
    def _scroll(widget):
        scroll = QScrollArea(); scroll.setWidget(widget); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        return scroll

    def _tab_style(self, selected, accent):
        bg = rgba(accent, 0.22) if selected else rgba("#ffffff", 0.035)
        border = rgba(accent, 0.72) if selected else rgba("#ffffff", 0.10)
        color = T("text") if selected else T("dim")
        return (f"QPushButton {{ background:{bg}; border:1px solid {border};"
                f" border-radius:7px; color:{color}; font-size:9px; font-weight:800;"
                f" letter-spacing:0.7px; }}"
                f"QPushButton:disabled {{ color:{T('faint')};"
                f" background:{rgba('#ffffff', 0.02)}; border-color:{rgba('#ffffff', 0.06)}; }}")

    def _show_tab(self, index):
        if index == 1 and not self.btn_active.isEnabled():
            index = 0
        self._stack.setCurrentIndex(index)
        self.btn_plan.setChecked(index == 0); self.btn_active.setChecked(index == 1)
        self.btn_plan.setStyleSheet(self._tab_style(index == 0, T("amber")))
        self.btn_active.setStyleSheet(self._tab_style(index == 1, T("green")))

    def _section_title(self, text):
        label = QLabel(text); label.setStyleSheet(f"color:{T('dim')}; font-size:9px; font-weight:700; letter-spacing:1px;"); return label

    @staticmethod
    def _clear_layout(layout, keep=()):
        keep = set(keep)
        while layout.count():
            item = layout.takeAt(0); widget = item.widget()
            if widget is not None and widget not in keep: widget.deleteLater()

    def render_rows(self, rows):
        self._rows = list(rows)
        # _empty is a persistent widget.  Scheduling it for deleteLater() and
        # immediately adding the same object back leaves a dangling Qt wrapper;
        # the next event-loop pass can abort inside Qt5Core (0xc0000409).
        self._clear_layout(self._plan_rows, keep=(self._empty,))
        self.lbl_count.setText(str(len(self._rows)))
        self.btn_plan.setText(f"PRE-FLIGHT · {len(self._rows)}")
        if not self._rows:
            self._empty.setVisible(True); self._plan_rows.addWidget(self._empty); return
        self._empty.setVisible(False)
        for label, value in self._rows: self._plan_rows.addWidget(self._make_row(label, value))

    def render_timeline(self, run):
        """Render a FlightRun. The pulse only repaints current visual state."""
        new_run = run is not None and run is not self._run
        self._run = run; self._clear_layout(self._timeline)
        if run is None:
            self._flow_title.setText("ยังไม่มีภารกิจที่กำลังทำงาน")
            self.lbl_state.setText("PLAN · LIVE"); self.btn_active.setText("ACTIVE · —")
            self.btn_active.setEnabled(False); self._pulse.stop(); self._show_tab(0); return
        self.btn_active.setEnabled(True); active = run.active_index
        self.lbl_state.setText("MISSION · ACTIVE" if active else "MISSION · COMPLETE")
        self.btn_active.setText("ACTIVE" + (f" · {active}/{len(run.steps)}" if active else " · DONE"))
        self._flow_title.setText("STEP %d/%d" % (active, len(run.steps)) if active else "MISSION COMPLETE")
        if active: self._pulse.start()
        else: self._pulse.stop()
        for index, step in enumerate(run.steps): self._timeline.addWidget(self._make_step(step, index < len(run.steps) - 1))
        if new_run:
            self._show_tab(1)
        else:
            self._show_tab(self._stack.currentIndex())

    def _toggle_pulse(self):
        self._pulse_on = not self._pulse_on
        if self._run is not None: self.render_timeline(self._run)

    def _make_row(self, label, value):
        row = QFrame(); row.setStyleSheet(f"background:{rgba('#ffffff', 0.03)}; border-radius:6px;")
        h = QHBoxLayout(row); h.setContentsMargins(8, 4, 8, 4); h.setSpacing(8)
        key = QLabel(str(label)); key.setStyleSheet(f"color:{T('faint')}; font-size:10px; font-weight:700; letter-spacing:0.5px;"); key.setMinimumWidth(86)
        val = QLabel(str(value)); val.setWordWrap(True); val.setStyleSheet(f"color:{T('text')}; font-size:11px; font-weight:600; font-family:{FONT_MONO};")
        h.addWidget(key, 0); h.addWidget(val, 1); return row

    def _make_step(self, step, connector):
        wrap = QWidget(); v = QVBoxLayout(wrap); v.setContentsMargins(2, 2, 2, 0); v.setSpacing(1)
        symbol, color = _STATUS_STYLE.get(step.status, _STATUS_STYLE[StepStatus.PENDING])
        if step.status == StepStatus.ACTIVE and self._pulse_on: color = T("green")
        line = QHBoxLayout(); line.setSpacing(7)
        dot = QLabel(symbol); dot.setFixedWidth(14); dot.setAlignment(Qt.AlignCenter); dot.setStyleSheet(f"color:{color}; font-size:16px; font-weight:700;"); line.addWidget(dot)
        title = QLabel(step.title); title.setStyleSheet(f"color:{color if step.status == StepStatus.ACTIVE else T('text')}; font-size:10px; font-weight:700;"); line.addWidget(title, 1)
        tag = "ACTIVE" if step.status == StepStatus.ACTIVE else ("NEXT" if step.status == StepStatus.PENDING and self._next_step_id() == step.id else (step.status.value if step.status != StepStatus.PENDING else ""))
        if tag:
            pill = QLabel(tag); pill.setStyleSheet(f"color:{color}; font-size:8px; font-family:{FONT_MONO}; font-weight:700;"); line.addWidget(pill)
        v.addLayout(line)
        if step.detail:
            detail = QLabel(step.detail); detail.setWordWrap(True); detail.setStyleSheet(f"color:{T('dim')}; font-size:9px; margin-left:21px;"); v.addWidget(detail)
        if connector:
            stem = QLabel("│"); stem.setStyleSheet(f"color:{T('green') if step.status == StepStatus.DONE else T('faint')}; font-size:12px; margin-left:4px;"); v.addWidget(stem)
        return wrap

    def _next_step_id(self):
        if self._run is None or not self._run.active_step_id: return None
        active_index = next((i for i, step in enumerate(self._run.steps) if step.id == self._run.active_step_id), -1)
        return next((step.id for step in self._run.steps[active_index + 1:] if step.status == StepStatus.PENDING), None)
