"""
เทสต์ฟีเจอร์แผนที่ยุทธวิธี (Target / Dashed path / Cancel Nav / Tactical drawing)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_tactical_map -v

ครอบคลุม:
  ข้อ 1 — จุดเป้ากะพริบ + เส้นประสีตรงกับโดรน + หดสั้นลงตามระยะจริง
  ข้อ 2 — ปุ่ม Cancel Nav (ล้างเป้า + สั่งหยุดลอยอยู่กับที่)
  ข้อ 3 — เครื่องมือวาดยุทธวิธี แยกจาก geofence + อัปโหลดสัญลักษณ์ + Save/Load

หมายเหตุ: ฝั่ง JavaScript ของแผนที่ถูกตรวจแยกด้วย `node --check` + รันจริงในเบราว์เซอร์
(ดูรายงานใน docs/SWARM_CONTROL.md) เทสต์ชุดนี้ตรวจฝั่ง Python + สัญญาที่คุยกับ JS
"""
import json
import os
import re
import sys
import time
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
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core.map_bridge import MapBridge  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])
_MAP_HTML = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map.html")


def _map_js():
    with open(_MAP_HTML, encoding="utf-8") as f:
        s = f.read()
    return re.search(r"<script>\n(.*?)\n</script>", s, re.S).group(1)


# ─────────────────────────────────────────────────────────────
#  ฝั่ง JavaScript — ตรวจว่ามี API ครบและวงเล็บสมดุล
# ─────────────────────────────────────────────────────────────
class TestMapJsContract(unittest.TestCase):
    def setUp(self):
        self.js = _map_js()

    def test_target_api_exists(self):
        for fn in ("setTarget", "clearTarget", "clearAllTargets",
                   "hasTarget", "targetCount", "_updateTargetLine"):
            self.assertIn(f"function {fn}(", self.js, f"ขาดฟังก์ชัน {fn}")

    def test_tactical_api_exists(self):
        for fn in ("setTacticalTool", "setTacticalText", "addTacticalIcon",
                   "useTacticalIcon", "listTacticalIcons", "finishTactical",
                   "undoTactical", "clearTactical", "getTacticalGeoJSON",
                   "loadTacticalGeoJSON", "tacticalCount"):
            self.assertIn(f"function {fn}(", self.js, f"ขาดฟังก์ชัน {fn}")

    def test_blink_animation_defined(self):
        with open(_MAP_HTML, encoding="utf-8") as f:
            html = f.read()
        self.assertIn("@keyframes tgtPulse", html)
        self.assertIn(".tgt-ico svg", html)
        self.assertIn("animation:tgtPulse", html.replace(" ", ""))

    def test_dashed_line_configured(self):
        self.assertIn("dashArray", self.js)

    def test_solid_trail_kept(self):
        """เส้นทึบประวัติเส้นทางต้องยังอยู่ (ห้ามลบ)"""
        self.assertIn("trails[id] = L.polyline", self.js)
        self.assertIn("trails[id].addLatLng(ll)", self.js)

    def test_target_line_updates_on_move(self):
        self.assertIn("if(targets[id]) _updateTargetLine(id);", self.js)

    def test_tactical_click_precedes_goto(self):
        """เครื่องมือยุทธวิธีต้องดักคลิกก่อน ไม่ให้กลายเป็นคำสั่ง GOTO"""
        i_tac = self.js.index("if(tacTool !== 'none'){ _tacClick(ll); return; }")
        i_goto = self.js.index("event:'map_click'")
        self.assertLess(i_tac, i_goto)

    def test_tools_are_mutually_exclusive(self):
        """เปิด tactical → ปิด geofence และกลับกัน"""
        self.assertIn("drawTool = 'none'", self.js)
        self.assertIn("tacTool = 'none'", self.js)

    def test_braces_balanced(self):
        for a, b in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(self.js.count(a), self.js.count(b),
                             f"วงเล็บ {a}{b} ไม่สมดุล")


# ─────────────────────────────────────────────────────────────
#  MapBridge — event ใหม่
# ─────────────────────────────────────────────────────────────
class TestMapBridge(unittest.TestCase):
    def test_target_reached_event(self):
        b = MapBridge()
        got = []
        b.target_reached.connect(got.append)
        b.on_map_event(json.dumps({"event": "target_reached", "drone_id": 4}))
        self.assertEqual(got, [4])

    def test_map_click_still_works(self):
        b = MapBridge()
        got = []
        b.map_click.connect(lambda la, lo: got.append((la, lo)))
        b.on_map_event(json.dumps({"event": "map_click", "lat": 14.5, "lon": 100.5}))
        self.assertEqual(got, [(14.5, 100.5)])

    def test_bad_payload_ignored(self):
        b = MapBridge()
        b.on_map_event("not json")       # ต้องไม่ throw


# ─────────────────────────────────────────────────────────────
#  ฝั่ง Python — คำสั่งที่ยิงไป JS + Cancel Nav
# ─────────────────────────────────────────────────────────────
class Base(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        self.js_calls = []
        self.win._js = self.js_calls.append      # ดักคำสั่งที่ยิงเข้าแผนที่
        for d in (1, 2, 3):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._last_alt[d] = 20.0
            self.win._last_telem[d] = _fake_telem(
                d, 14.0 + 0.0003 * d, 100.0 + 0.0003 * d, alt_rel=20.0)
        self.win._refresh_takeoff_panel()
        # คลิกแผนที่สั่งบินต้องปลดล็อกก่อน (ปุ่มรูปโดรนบนแผนที่) — จำลองว่าผู้ใช้กดแล้ว
        self.win._set_goto_armed(True)
        self.js_calls.clear()          # ทิ้ง setGotoArmed ออกจาก baseline ของเทสต์

    def tearDown(self):
        self.win._rtl_active = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def js_matching(self, needle):
        return [c for c in self.js_calls if needle in c]


class TestTargetsFromPython(Base):
    def test_map_click_sets_target_per_drone(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.0)
        calls = self.js_matching("setTarget(")
        self.assertEqual(len(calls), 3, "ต้องตั้งเป้าครบทุกลำที่เลือก")

    def test_target_colour_matches_drone(self):
        from swarmgod_gui.core.theme import drone_color
        self.win._on_fleet_click(2, False)
        self.win._on_map_click(14.5, 100.5)
        _pump(0.6)
        call = self.js_matching("setTarget(")[0]
        self.assertIn(drone_color(2), call, "สีเป้าไม่ตรงกับสีโดรน")

    def test_no_target_when_nothing_selected(self):
        self.win._on_fleet_toggled(False)
        self.win._selected_id = 0
        self.win._on_map_click(14.5, 100.5)
        _pump(0.4)
        self.assertEqual(self.js_matching("setTarget("), [])

    def test_target_reached_clears_state(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.0)
        self.assertTrue(self.win._nav_targets)
        for d in (1, 2, 3):
            self.win._on_target_reached(d)
        self.assertEqual(self.win._nav_targets, {})


class TestCancelNavigation(Base):
    def test_button_exists(self):
        self.assertTrue(hasattr(self.win, "btn_cancel_nav"))

    def test_cancel_clears_targets_on_map(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.0)
        self.js_calls.clear()
        self.win._cancel_navigation()
        _pump(0.4)
        self.assertTrue(self.js_matching("clearAllTargets()"),
                        "ไม่ได้ล้างเป้า/เส้นประบนแผนที่")

    def test_cancel_commands_hold(self):
        self.win._on_fleet_toggled(True)
        self.win._cancel_navigation()
        _pump(0.5)
        kinds = [c[0] for c in self.fake.calls]
        self.assertIn("stop_all", kinds, "ไม่ได้สั่งหยุดเคลื่อนที่")
        self.assertIn("hold", kinds, "ไม่ได้สั่งค้างตำแหน่ง (hover)")

    def test_cancel_targets_selected_drones(self):
        self.win._on_fleet_click(2, False)
        self.win._cancel_navigation()
        _pump(0.5)
        holds = [c for c in self.fake.calls if c[0] == "hold"]
        self.assertEqual(holds[0][1], (2,))

    def test_cancel_clears_nav_state(self):
        self.win._on_fleet_toggled(True)
        self.win._on_map_click(14.5, 100.5)
        _pump(1.0)
        self.win._cancel_navigation()
        _pump(0.4)
        self.assertEqual(self.win._nav_targets, {})

    def test_cancel_aborts_rtl(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.6)
        self.assertTrue(self.win._rtl_active)
        self.win._cancel_navigation()
        _pump(0.4)
        self.assertFalse(self.win._rtl_active, "Cancel ต้องตัด RTL ที่ค้างอยู่")

    def test_cancel_with_no_drones_is_safe(self):
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.win._cancel_navigation()        # ต้องไม่ throw
        _pump(0.3)


class TestTacticalPanel(Base):
    def test_panel_widgets_exist(self):
        self.assertTrue(hasattr(self.win, "_tac_btns"))
        self.assertTrue(hasattr(self.win, "cmb_tac_icon"))
        self.assertTrue(hasattr(self.win, "lbl_tac_status"))

    def test_all_draw_tools_present(self):
        for k in ("none", "line", "polygon", "circle", "text", "icon"):
            self.assertIn(k, self.win._tac_btns, f"ขาดเครื่องมือ {k}")

    def test_selecting_tool_calls_js(self):
        self.win._set_tac_tool("polygon")
        self.assertTrue(self.js_matching("setTacticalTool('polygon')"))
        self.assertEqual(self.win._tac_tool, "polygon")

    def test_text_tool_prompts_and_sends_text(self):
        with mock.patch("swarmgod_gui.app.QInputDialog.getText",
                        return_value=("จุดรวมพล", True)):
            self.win._set_tac_tool("text")
        self.assertTrue(self.js_matching("setTacticalText("))
        self.assertEqual(self.win._tac_tool, "text")

    def test_text_tool_cancelled_does_not_activate(self):
        with mock.patch("swarmgod_gui.app.QInputDialog.getText",
                        return_value=("", False)):
            self.win._set_tac_tool("text")
        self.assertNotEqual(self.win._tac_tool, "text")

    def test_icon_tool_blocked_without_upload(self):
        self.win._tac_icons = {}
        self.win._set_tac_tool("icon")
        self.assertNotEqual(self.win._tac_tool, "icon",
                            "ยังไม่มีสัญลักษณ์ ไม่ควรเปิดเครื่องมือได้")

    def test_finish_undo_clear_call_js(self):
        self.win._tac_finish()
        self.assertTrue(self.js_matching("finishTactical()"))
        self.win._tac_undo()
        self.assertTrue(self.js_matching("undoTactical()"))
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True):
            self.win._tac_clear()
        self.assertTrue(self.js_matching("clearTactical()"))

    def test_clear_asks_confirmation(self):
        with mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=False):
            self.win._tac_clear()
        self.assertFalse(self.js_matching("clearTactical()"),
                         "ต้องถามยืนยันก่อนล้างแผน")


class TestTacticalIconUpload(unittest.TestCase):
    def test_data_url_encoding(self):
        import base64
        import tempfile
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        p = os.path.join(tempfile.gettempdir(), "unit_marker.png")
        with open(p, "wb") as f:
            f.write(png)
        url, name = GroundStation._icon_data_url(p)
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(name, "unit_marker")

    def test_oversize_icon_rejected(self):
        import tempfile
        p = os.path.join(tempfile.gettempdir(), "big_icon.png")
        with open(p, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024 + 10))
        with self.assertRaises(ValueError):
            GroundStation._icon_data_url(p)


class TestTacticalSaveLoad(Base):
    def test_write_plan_to_file(self):
        import tempfile
        p = os.path.join(tempfile.gettempdir(), "plan_out.geojson")
        payload = json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [100.0, 14.0]},
                          "properties": {"tac": "text", "text": "A"}}],
            "icons": {}})
        self.win._tac_write(p, payload)
        with open(p, encoding="utf-8") as f:
            back = json.load(f)
        self.assertEqual(back["type"], "FeatureCollection")
        self.assertEqual(len(back["features"]), 1)

    def test_write_handles_bad_path(self):
        self.win._tac_write(os.path.join("/nope", "x.geojson"), "{}")   # ต้องไม่ throw

    def test_loaded_updates_status(self):
        self.win._tac_loaded("/tmp/p.geojson", 7)
        self.assertIn("7", self.win.lbl_tac_status.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
