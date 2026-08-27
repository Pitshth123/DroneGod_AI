"""แผนที่กระโดดตาม GPS / พิกัดที่พิมพ์ — ไม่ติดที่นครราชสีมา"""
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
from swarmgod_gui.core.map_bridge import parse_latlon  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem  # noqa: E402

_app = QApplication.instance() or QApplication([])
_MAP_HTML = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map.html")


class TestParseLatLon(unittest.TestCase):
    def test_comma(self):
        self.assertEqual(parse_latlon("13.7563, 100.5018"), (13.7563, 100.5018))

    def test_space(self):
        lat, lon = parse_latlon("14.9581695 102.0986187")
        self.assertAlmostEqual(lat, 14.9581695)
        self.assertAlmostEqual(lon, 102.0986187)

    def test_rejects_zero_and_garbage(self):
        self.assertIsNone(parse_latlon("0, 0"))
        self.assertIsNone(parse_latlon("99.1, 10"))
        self.assertIsNone(parse_latlon("hello"))
        self.assertIsNone(parse_latlon(""))


class TestJumpMap(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.win.client = FakeClient()
        self.js = []
        self.js3d = []
        self.win._js = self.js.append
        self.win._js3d = self.js3d.append

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_jump_emits_js_and_3d_origin(self):
        ok = self.win._jump_map_to(13.7563, 100.5018, set_gcs=True)
        self.assertTrue(ok)
        self.assertEqual(self.win._map_center, (13.7563, 100.5018))
        self.assertTrue(any("jumpTo(13.7563000,100.5018000,17)" in c for c in self.js))
        self.assertTrue(any("setGCS(13.7563000,100.5018000)" in c for c in self.js))
        self.assertTrue(any("map3d.setOrigin(100.5018000,13.7563000)" in c for c in self.js3d))

    def test_rejects_null_island(self):
        self.assertFalse(self.win._jump_map_to(0.0, 0.0))
        self.assertIsNone(self.win._map_center)

    def test_first_telemetry_jumps_once(self):
        it = FleetItem(1, "Drone 1", self.win._pixmap)
        self.win._wire_fleet_item(it)
        self.win.fleet_items[1] = it
        self.win.fleet_area.addWidget(it)
        self.win._on_telemetry(_fake_telem(1, 18.7883, 98.9853, alt_rel=0.4))
        self.assertTrue(self.win._map_followed_gps)
        self.assertAlmostEqual(self.win._map_center[0], 18.7883, places=4)
        jumps = [c for c in self.js if c.startswith("jumpTo(")]
        self.assertEqual(len(jumps), 1)
        n = len(self.js)
        self.win._on_telemetry(_fake_telem(1, 18.7890, 98.9860, alt_rel=0.4))
        self.assertEqual(len([c for c in self.js if c.startswith("jumpTo(")]), 1)
        self.assertGreaterEqual(len(self.js), n)

    def test_goto_drone_gps_button(self):
        self.win._last_telem[1] = _fake_telem(1, 13.75, 100.50)
        self.win._selected_id = 1
        self.win._goto_drone_gps()
        self.assertAlmostEqual(self.win._map_center[0], 13.75, places=2)

    def test_goto_typed_gps(self):
        with mock.patch("swarmgod_gui.app.QInputDialog.getText",
                        return_value=("13.7563, 100.5018", True)):
            self.win._goto_typed_gps()
        self.assertAlmostEqual(self.win._map_center[0], 13.7563, places=4)

    def test_html_has_jump_to(self):
        with open(_MAP_HTML, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("function jumpTo(", html)

    def test_geofence_panel_has_gps_buttons(self):
        from PyQt5.QtWidgets import QPushButton
        texts = [b.text() for b in self.win.findChildren(QPushButton)]
        self.assertIn("ไปที่ GPS", texts)
        self.assertIn("ใส่พิกัด", texts)
        self.assertIn("CACHE AREA", texts)


if __name__ == "__main__":
    unittest.main()
