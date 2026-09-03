"""Flight command panel presentation for the main cockpit.

This module intentionally owns layout/widget composition only. Command handlers,
transport calls, safety gates, and flight authority remain on GroundStation.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QPushButton, QGridLayout, QHBoxLayout

from ..core.theme import T, section_label_qss, tinted_btn
from .controls import SliderField, AccordionSection
from .takeoff_panel import TakeoffPanel


def build_flight_section(host):
    """Build the FLIGHT accordion while preserving GroundStation's public attrs.

    Keeping the existing attributes on ``host`` avoids changing callbacks/tests
    during the first extraction step. A later refactor can replace this adapter
    with explicit signals once the presentation boundary is stable.
    """
    sec = AccordionSection("FLIGHT", accent=T("green"), expanded=True)

    quick_label = QLabel("QUICK FLIGHT")
    quick_label.setStyleSheet(section_label_qss())
    sec.add_widget(quick_label)

    grid = QGridLayout()
    grid.setSpacing(6)
    grid.addWidget(host._flight_btn("▶", "ARM", host._cmd_arm, primary=True), 0, 0)
    grid.addWidget(host._flight_btn("■", "DISARM", host._cmd_disarm), 0, 1)
    grid.addWidget(host._flight_btn("⬇", "LAND", host._cmd_land), 1, 0)
    grid.addWidget(host._flight_btn("↩", "RTL", host._cmd_rtl), 1, 1)
    grid.addWidget(host._flight_btn("❚❚", "HOLD", host._cmd_hold), 2, 0, 1, 2)
    sec.add_layout(grid)

    host.sf_takeoff = SliderField(
        "TAKEOFF ALTITUDE", 1, 120, 20, 1, "m", 0, T("accent"))
    sec.add_widget(host.sf_takeoff)

    host.takeoff_panel = TakeoffPanel(default_alt=20.0)
    host.takeoff_panel.takeoff_requested.connect(host._on_panel_takeoff)
    host.takeoff_panel.fleet_toggled.connect(host._on_fleet_toggled)
    host.takeoff_panel.changed.connect(host._update_takeoff_summary)
    host.sf_takeoff.valueChanged.connect(host.takeoff_panel.set_default_alt)
    sec.add_widget(host.takeoff_panel)

    advanced = AccordionSection(
        "ADVANCED FLIGHT", accent=T("dim"), expanded=False)

    servo_label = QLabel("PAYLOAD SERVO")
    servo_label.setStyleSheet(section_label_qss())
    advanced.add_widget(servo_label)

    servo_row = QHBoxLayout()
    servo_row.setSpacing(8)
    host.btn_servo_a = host._servo_btn("A", T("red"))
    host.btn_servo_b = host._servo_btn("B", T("yellow"))
    servo_row.addWidget(host.btn_servo_a, 1)
    servo_row.addWidget(host.btn_servo_b, 1)
    advanced.add_layout(servo_row)

    host.btn_cancel_nav = QPushButton("CANCEL NAV")
    host.btn_cancel_nav.setMinimumHeight(36)
    host.btn_cancel_nav.setCursor(Qt.PointingHandCursor)
    host.btn_cancel_nav.setToolTip(
        "ยกเลิกเป้าหมาย: ลบจุดเป้า+เส้นประ+เส้นทาง Waypoint บนแผนที่\n"
        "แล้วให้โดรนหยุดลอยค้างที่เดิม (คงโหมด GUIDED · ความสูงเท่าเดิม ไม่ลดระดับ)")
    host.btn_cancel_nav.setStyleSheet(tinted_btn(T("red"), radius=8, font=11))
    host.btn_cancel_nav.clicked.connect(host._cancel_navigation)
    advanced.add_widget(host.btn_cancel_nav)

    mode_label = QLabel("FLIGHT MODE")
    mode_label.setStyleSheet(section_label_qss())
    advanced.add_widget(mode_label)

    mode_grid = QGridLayout()
    mode_grid.setSpacing(6)
    modes = [
        ("Guided", "FLIGHT_MODE_GUIDED"),
        ("Loiter", "FLIGHT_MODE_LOITER"),
        ("Stabilize", "FLIGHT_MODE_STABILIZE"),
        ("PosHold", "FLIGHT_MODE_POSHOLD"),
    ]
    for i, (label, enum) in enumerate(modes):
        mode_grid.addWidget(
            host._abtn(label, T("dim"), lambda _, e=enum: host._cmd_mode(e), "ghost"),
            i // 2,
            i % 2,
        )
    advanced.add_layout(mode_grid)

    sec.add_widget(advanced)
    host.sec_flight_advanced = advanced
    return sec
