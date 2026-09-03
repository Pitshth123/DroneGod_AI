import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.core import preflight  # noqa: E402
from swarmgod_gui.widgets.quick_setup_dialog import QuickSetupDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestQuickSetupDialog(unittest.TestCase):
    def test_walkthrough_builds_config_without_command_callbacks(self):
        connect_cb = mock.Mock()
        safety_cb = mock.Mock(return_value={
            "profile": "setup", "home_loc": False, "mtls": True,
            "ui_mode": True, "drones": [],
        })
        dlg = QuickSetupDialog(
            state={
                "drones": [{"id": 1, "name": "Drone 1"}],
                "online_ids": [1], "selected_ids": [1],
                "operation": "direct", "takeoff_alt": 20, "speed": 3,
                "formation": 0, "spacing": 12, "offset": 5, "form_speed": 4,
            },
            connect_cb=connect_cb,
            safety_cb=safety_cb,
        )
        self.assertEqual(dlg.stack.count(), 6)
        self.assertTrue(dlg._drone_checks[1].isChecked())
        dlg._op_buttons["waypoint"].setChecked(True)
        dlg.sp_takeoff.setValue(35)
        dlg._show_step(5)
        dlg._next()
        self.assertIsNotNone(dlg.result_config)
        self.assertEqual(dlg.result_config["selected_ids"], [1])
        self.assertEqual(dlg.result_config["operation"], "waypoint")
        self.assertEqual(dlg.result_config["takeoff_alt"], 35.0)
        connect_cb.assert_not_called()
        dlg.deleteLater()


class TestQuickSetupGroundStation(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")

    def tearDown(self):
        self.win._rtl_active = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_commands_header_exposes_quick_setup(self):
        self.assertEqual(self.win.btn_quick_setup.text(), "✓ QUICK SETUP")
        self.assertIsNotNone(self.win.btn_field)
        tip = self.win.btn_quick_setup.toolTip()
        self.assertIn("ไม่ ARM", tip)
        self.assertIn("ไม่ TAKEOFF", tip)

    def test_apply_is_configuration_only_and_invalidates_preflight(self):
        self.win._preflight.mark_selftest(True, "ok")
        self.win._preflight.mark_checklist(True, "ok")
        self.assertTrue(self.win._preflight.ready())

        cfg = {
            "selected_ids": [],
            "operation": "formation",
            "takeoff_alt": 31.0,
            "speed": 5.5,
            "formation": 2,
            "spacing": 18.0,
            "offset": 7.0,
            "form_speed": 6.0,
        }
        with mock.patch.object(self.win, "_dispatch_core") as dispatch, \
             mock.patch.object(self.win, "_gateway") as gateway:
            self.win._apply_quick_setup(cfg)

        dispatch.assert_not_called()
        self.assertFalse(gateway.method_calls)
        self.assertEqual(self.win.sf_takeoff.value(), 31.0)
        self.assertEqual(self.win.sf_speed.value(), 5.5)
        self.assertEqual(self.win.sf_spacing.value(), 18.0)
        self.assertEqual(self.win.sf_offset.value(), 7.0)
        self.assertEqual(self.win.sf_formspeed.value(), 6.0)
        self.assertEqual(self.win.form_picker.current(), 2)
        self.assertEqual(self.win._quick_setup_operation, "formation")
        plan = self.win._flight_snapshot()
        self.assertEqual(plan["QUICK SETUP"], "FORMATION · NONE")
        self.assertEqual(plan["TAKEOFF ALT"], "31 m")
        self.assertEqual(plan["MISSION SPEED"], "5.5 m/s")
        self.assertIn("COLUMN", plan["FORMATION"])
        self.assertIsInstance(self.win._preflight, preflight.PreflightState)
        self.assertFalse(self.win._preflight.ready())


if __name__ == "__main__":
    unittest.main()
