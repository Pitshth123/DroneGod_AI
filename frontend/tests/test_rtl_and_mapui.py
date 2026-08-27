"""
เทสต์งานรอบนี้ (สเปกข้อ 3 — Self-Verification)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_rtl_and_mapui -v

ครอบคลุม:
  ข้อ 1a — Tooltip บนแผนที่โชว์เฉพาะตอน hover (ไม่ค้างบนจอ)
  ข้อ 1b — ปุ่ม Save/Export/Load ย่อเป็นไอคอน dropdown เดียว
  ข้อ 2  — RTL แยกชั้นความสูง → บินกลับคนละชั้น → ลงจอดทีละลำจากล่างขึ้นบน
"""
import os
import re
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication, QPushButton  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core import swarm_logic as SL  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])

_MAP_HTML = os.path.join(_FRONTEND, "swarmgod_gui", "assets", "map.html")


# ─────────────────────────────────────────────────────────────
#  ข้อ 1a — MAP TOOLTIP (hover only)
# ─────────────────────────────────────────────────────────────
class TestMapTooltip(unittest.TestCase):
    def setUp(self):
        with open(_MAP_HTML, encoding="utf-8") as f:
            self.html = f.read()

    def test_no_permanent_tooltips_left(self):
        """ป้ายชื่อต้องไม่ค้างบนแผนที่ (permanent:true = ค้าง)"""
        self.assertNotIn("permanent:true", self.html.replace(" ", ""),
                         "ยังมี tooltip แบบค้างบนแผนที่อยู่")

    def test_drone_tooltip_is_hover(self):
        self.assertIn("permanent:false", self.html.replace(" ", ""))

    def test_tooltip_does_not_follow_or_capture_cursor(self):
        """เลิกชี้แล้วต้องหาย ไม่รับเมาส์จน Leaflet คิดว่ายัง hover อยู่."""
        self.assertIn("sticky:false", self.html.replace(" ", ""))
        self.assertIn("pointer-events:none", self.html.replace(" ", ""))

    def test_tooltips_close_when_cursor_leaves_map_or_marker(self):
        self.assertIn("drones[id].closeTooltip()", self.html)
        self.assertIn("gcsMarker.on('mouseout'", self.html)

    def test_set_drone_info_function_exists(self):
        self.assertIn("function setDroneInfo", self.html)

    def test_info_cleared_on_drone_removal(self):
        m = re.search(r"function clearDrone\(id\)\{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(m)
        self.assertIn("droneInfo", m.group(1),
                      "ลบโดรนแล้วต้องล้าง droneInfo ด้วย (กัน memory รั่ว)")


class TestMapTooltipWiring(unittest.TestCase):
    def test_app_pushes_hover_info(self):
        """app ต้องส่งข้อมูลไปให้ tooltip ผ่าน setDroneInfo"""
        app_py = os.path.join(_FRONTEND, "swarmgod_gui", "app.py")
        with open(app_py, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("setDroneInfo", src)


# ─────────────────────────────────────────────────────────────
#  BASE สำหรับเทสต์ที่ต้องมีหน้าต่างจริง
# ─────────────────────────────────────────────────────────────
class Base(unittest.TestCase):
    N = 5

    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        self.ids = list(range(1, self.N + 1))
        for d in self.ids:
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
        # ทุกลำลอยอยู่ 20 m, home เดียวกัน, กระจายกันในแนวราบ
        self.home = (14.0, 100.0)
        for d in self.ids:
            self.win._home_pos[d] = self.home
            self.win._last_alt[d] = 20.0
            self.win._last_telem[d] = _fake_telem(
                d, 14.0 + 0.0005 * d, 100.0 + 0.0005 * d, alt_rel=20.0)
        self.win._refresh_takeoff_panel()

    def tearDown(self):
        self.win._rtl_active = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def calls(self, kind):
        return [c for c in self.fake.calls if c[0] == kind]


# ─────────────────────────────────────────────────────────────
#  ข้อ 1b — เมนู Save/Export/Load ถูกย่อ
# ─────────────────────────────────────────────────────────────
class TestConfigMenuCollapsed(Base):
    N = 2

    def test_dropdown_button_exists(self):
        self.assertTrue(hasattr(self.win, "btn_cfg"))
        self.assertIsNotNone(self.win.btn_cfg.menu())

    def test_button_is_small(self):
        b = self.win.btn_cfg
        self.assertLessEqual(b.width(), 32, "ปุ่มยังใหญ่เกินไป")
        self.assertLessEqual(b.height(), 26)

    def test_menu_has_all_actions(self):
        texts = " ".join(a.text() for a in self.win.btn_cfg.menu().actions())
        for want in ("Save", "Export", "Load"):
            self.assertIn(want, texts)

    def test_no_large_standalone_buttons_left(self):
        """ต้องไม่มีปุ่ม SAVE/EXPORT/LOAD ตัวใหญ่ลอยอยู่ในแถบซ้ายแล้ว"""
        stray = [b for b in self.win.left_col.findChildren(QPushButton)
                 if b.text().strip().upper() in ("SAVE", "EXPORT", "LOAD")]
        self.assertEqual(stray, [], f"ยังมีปุ่มเดิมค้างอยู่: {[b.text() for b in stray]}")

    def test_actions_are_wired(self):
        acts = self.win.btn_cfg.menu().actions()
        self.assertTrue(all(a.receivers(a.triggered) > 0 for a in acts),
                        "มีเมนูที่ยังไม่ได้ต่อฟังก์ชัน")


class TestGeofenceVisualCommit(Base):
    N = 1

    def test_rejected_fence_keeps_previous_visual(self):
        old = [(14.0, 100.0), (14.0, 100.1), (14.1, 100.1)]
        self.win._fence_points = old
        js_calls = []
        self.win._js = js_calls.append
        self.fake.set_geofence = lambda points: SimpleNamespace(ok=False, message="rejected")
        self.win._fence_send([(15.0, 101.0)] * 3, "[]")
        _pump(0.1)
        self.assertEqual(self.win._fence_points, old)
        self.assertFalse(any("drawFence" in call for call in js_calls))

    def test_accepted_fence_updates_visual(self):
        points = [(14.0, 100.0), (14.0, 100.1), (14.1, 100.1)]
        js_calls = []
        self.win._js = js_calls.append
        self.win._push_map3d_view = lambda *args, **kwargs: None
        self.fake.set_geofence = lambda value: SimpleNamespace(ok=True, message="ok")
        self.win._fence_send(points, "[]")
        _pump(0.1)
        self.assertEqual(self.win._fence_points, points)
        self.assertTrue(any("drawFence" in call for call in js_calls))


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 — RTL LAYER PLANNING (logic ล้วน)
# ─────────────────────────────────────────────────────────────
class TestRtlPlanPure(unittest.TestCase):
    def test_layers_are_distinct(self):
        layers = SL.plan_rtl_layers({1: 20, 2: 20, 3: 20}, base_alt=15, layer_gap=5)
        alts = [l.alt for l in layers]
        self.assertEqual(len(set(alts)), 3, "ทุกลำได้ความสูงเดียวกัน = ยังชนกัน")

    def test_gap_respected(self):
        layers = SL.plan_rtl_layers({1: 20, 2: 20, 3: 20, 4: 20, 5: 20},
                                    base_alt=15, layer_gap=5)
        self.assertTrue(SL.layers_are_separated(layers, 5.0))
        self.assertEqual(sorted(l.alt for l in layers), [15, 20, 25, 30, 35])

    def test_lowest_drone_gets_lowest_layer(self):
        """ลำที่บินต่ำอยู่แล้วควรได้ชั้นล่าง — เส้นทางแนวดิ่งไม่ตัดกัน"""
        layers = SL.plan_rtl_layers({1: 50, 2: 10, 3: 30}, base_alt=15, layer_gap=5)
        by_id = {l.drone_id: l.index for l in layers}
        self.assertEqual(by_id[2], 0)
        self.assertEqual(by_id[3], 1)
        self.assertEqual(by_id[1], 2)

    def test_landing_order_is_lowest_first(self):
        layers = SL.plan_rtl_layers({1: 50, 2: 10, 3: 30}, base_alt=15, layer_gap=5)
        self.assertEqual(SL.rtl_landing_order(layers), [2, 3, 1])

    def test_custom_gap(self):
        layers = SL.plan_rtl_layers({1: 0, 2: 0}, base_alt=10, layer_gap=3)
        self.assertEqual(sorted(l.alt for l in layers), [10, 13])

    def test_empty(self):
        self.assertEqual(SL.plan_rtl_layers({}), [])

    def test_single_drone(self):
        layers = SL.plan_rtl_layers({7: 30}, base_alt=15, layer_gap=5)
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].alt, 15)

    def test_separation_check_catches_bad_plan(self):
        bad = [SL.RtlLayer(1, 15.0, 0), SL.RtlLayer(2, 16.0, 1)]
        self.assertFalse(SL.layers_are_separated(bad, 5.0))


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 — RTL ทั้ง 3 Step ในแอปจริง
# ─────────────────────────────────────────────────────────────
class LegacyRtlSequence:
    """Historical assertions for the removed UI-orchestrated RTL pipeline.

    Kept temporarily as migration notes; this is intentionally not a TestCase.
    """

    def setUp(self):
        super().setUp()
        # ให้แต่ละลำอยู่คนละความสูง (10,12,14,16,18) เหมือนสถานการณ์จริง
        for i, d in enumerate(self.ids):
            self.win._last_alt[d] = 10.0 + 2.0 * i
        self.win.sf_rtl_alt.setValue(2)
        self.win.sf_rtl_gap.setValue(2)
        self.win.RTL_BASE_ALT = 2.0
        self.win.RTL_LAYER_GAP = 2.0

    def test_single_drone_uses_plain_rtl(self):
        self.win._on_fleet_click(2, False)
        self.win._cmd_rtl()
        _pump(0.5)
        self.assertTrue(self.calls("rtl"))
        self.assertFalse(self.calls("change_alt"), "ลำเดียวไม่ต้องคำนวณชั้น")

    def test_missing_telemetry_is_not_treated_as_home(self):
        self.win._last_telem.pop(1, None)
        self.assertFalse(self.win._rtl_near_home(1, self.home))

    def test_stale_telemetry_is_not_treated_as_home(self):
        t = _fake_telem(1, self.home[0], self.home[1], alt_rel=12.0)
        t.timestamp_ms = 1_000
        self.win._last_telem[1] = t
        self.assertFalse(self.win._rtl_near_home(1, self.home, now_ms=5_001))

    def test_fresh_telemetry_at_home_is_accepted(self):
        t = _fake_telem(1, self.home[0], self.home[1], alt_rel=12.0)
        t.timestamp_ms = 5_000
        self.win._last_telem[1] = t
        self.assertTrue(self.win._rtl_near_home(1, self.home, now_ms=6_000))

    # ── Step 1: ไต่ +offset จากความสูงเดิมของตัวเอง ──
    def test_climbs_current_plus_offset(self):
        """เคสตามสเปก: D1 10m +2 = 12m, D2 12m +2 = 14m ..."""
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        got = {c[1][0]: c[2] for c in self.calls("change_alt")}
        self.assertEqual(got[1], 12.0, "D1 จาก 10m ต้องไต่ไป 12m")
        self.assertEqual(got[2], 14.0, "D2 จาก 12m ต้องไต่ไป 14m")
        self.assertEqual(got[3], 16.0)
        self.assertEqual(got[4], 18.0)
        self.assertEqual(got[5], 20.0)

    def test_offset_follows_config(self):
        self.win.RTL_BASE_ALT = 5.0
        self.win.RTL_LAYER_GAP = 2.0
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        got = {c[1][0]: c[2] for c in self.calls("change_alt")}
        self.assertEqual(got[1], 15.0, "10m + config 5m = 15m")

    def test_same_altitude_drones_still_separated(self):
        """ทุกลำอยู่ระดับเดียวกัน — บวกเท่ากันจะยังชน จึงต้องเหลื่อมให้"""
        for d in self.ids:
            self.win._last_alt[d] = 20.0
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        alts = sorted(c[2] for c in self.calls("change_alt"))
        self.assertEqual(len(set(alts)), 5, "ความสูงซ้ำกัน = ชนกลางอากาศ")
        gaps = [b - a for a, b in zip(alts, alts[1:])]
        self.assertTrue(all(g >= 2.0 - 1e-9 for g in gaps), f"ห่างไม่พอ: {gaps}")

    def test_does_not_use_fc_rtl(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.assertFalse(self.calls("rtl"),
                         "ต้องไม่ใช้ RTL ของ FC (จะไต่ไป RTL_ALT เดียวกันหมด)")

    # ── Step 2+3: แต่ละลำอิสระ ไม่รอกัน ──
    def test_drone_returns_without_waiting_for_others(self):
        """ลำที่ไต่ถึงก่อน ต้องวิ่งกลับได้เลย ไม่ต้องรอลำอื่น"""
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.win._last_alt[1] = 12.0          # เฉพาะ D1 ถึงระดับ
        _pump(2.5)
        gotos = [c[1] for c in self.calls("goto")]
        self.assertIn(1, gotos, "D1 ถึงระดับแล้วแต่ไม่ยอมวิ่งกลับ")
        self.assertNotIn(2, gotos, "D2 ยังไม่ถึงระดับ ไม่ควรวิ่งกลับ")

    def test_drone_lands_without_waiting_for_others(self):
        """ลำที่ถึง home ก่อน ลงจอดได้เลย"""
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.win._last_alt[1] = 12.0
        self.win._last_telem[1] = _fake_telem(1, self.home[0], self.home[1], alt_rel=12.0)
        _pump(5.0)
        landed = [c[1][0] for c in self.calls("land")]
        self.assertIn(1, landed, "D1 ถึง home แล้วแต่ไม่ลงจอด")

    def test_returns_home_at_its_own_altitude(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        for d in self.ids:                     # ทุกลำถึงระดับพร้อมกัน
            self.win._last_alt[d] = {1: 12.0, 2: 14.0, 3: 16.0,
                                     4: 18.0, 5: 20.0}[d]
        _pump(3.0)
        gotos = {c[1]: c[4] for c in self.calls("goto")}
        self.assertEqual(gotos.get(1), 12.0)
        self.assertEqual(gotos.get(2), 14.0)
        self.assertEqual(len(set(gotos.values())), len(gotos),
                         "ระหว่างบินกลับความสูงยุบรวมกัน")

    def test_home_coordinates_used(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        for d in self.ids:
            self.win._last_alt[d] = 30.0
        _pump(3.0)
        for c in self.calls("goto"):
            self.assertAlmostEqual(c[2], self.home[0], places=6)
            self.assertAlmostEqual(c[3], self.home[1], places=6)

    # ── สถานะบนการ์ด ──
    def test_cards_show_rtl_status(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        for d in self.ids:
            txt = self.win.fleet_items[d].badge.text()
            self.assertIn("RTL", txt, f"การ์ด D{d} ไม่แสดงสถานะ RTL (ได้ '{txt}')")

    def test_card_status_clears_when_done(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.assertIn("RTL", self.win.fleet_items[1].badge.text())
        self.win._rtl_finish(1)
        self.assertNotIn("RTL", self.win.fleet_items[1].badge.text())

    def test_selected_card_shows_rtl_status(self):
        self.win._select_drone(2)
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.assertIn("RTL", self.win.sel_card.badge.text())

    def test_rtl_phase_progresses(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        self.assertEqual(self.win._rtl_phase.get(1), "CLIMB")
        self.win._last_alt[1] = 12.0
        _pump(2.5)
        self.assertEqual(self.win._rtl_phase.get(1), "RETURN")

    # ── ความปลอดภัย / กันซ้อน ──
    def test_second_rtl_ignored_while_running(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.6)
        n = len(self.calls("change_alt"))
        self.win._cmd_rtl()
        _pump(0.5)
        self.assertEqual(len(self.calls("change_alt")), n,
                         "กด RTL ซ้ำแล้วสั่งซ้อนกัน")

    def test_estop_aborts_rtl(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.6)
        self.assertTrue(self.win._rtl_active)
        self.win._do_estop([1], "ALL")
        _pump(0.3)
        self.assertFalse(self.win._rtl_active, "E-STOP ต้องตัด RTL")
        self.assertEqual(self.win._rtl_phase, {}, "ต้องล้างสถานะ RTL บนการ์ด")

    def test_finishes_when_all_drones_done(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.6)
        for d in self.ids:
            self.win._rtl_finish(d)
        self.assertFalse(self.win._rtl_active)

    def test_three_drones_scenario(self):
        """เคสตามสเปก 3 ลำ: 10→12, 12→14, 14→16"""
        for d in (4, 5):
            self.win._remove_fleet_item(d)
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        got = {c[1][0]: c[2] for c in self.calls("change_alt")}
        self.assertEqual(got, {1: 12.0, 2: 14.0, 3: 16.0})


class TestCoreReturnSequence(Base):
    def test_single_drone_still_uses_plain_fc_rtl(self):
        self.win._on_fleet_click(2, False)
        self.win._cmd_rtl()
        _pump(0.4)
        self.assertEqual(self.calls("rtl")[0][1], (2,))
        self.assertFalse(self.calls("swarm_return"))

    def test_multi_return_is_delegated_once_to_core(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.4)
        calls = self.calls("swarm_return")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], tuple(self.ids))
        self.assertFalse(self.calls("change_alt"))
        self.assertFalse(self.calls("goto"))
        self.assertFalse(self.calls("land"))

    def test_swarm_return_button_uses_same_core_owned_path(self):
        self.win._on_fleet_toggled(True)
        self.win._swarm_return()
        _pump(0.4)
        self.assertEqual(len(self.calls("swarm_return")), 1)
        self.assertEqual(self.calls("swarm_return")[0][1], tuple(self.ids))

    def test_return_settings_are_sent_to_core(self):
        self.win.RTL_BASE_ALT = 25.0
        self.win.RTL_LAYER_GAP = 8.0
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.4)
        call = self.calls("swarm_return")[0]
        self.assertEqual(call[2:], (25.0, 8.0))

    def test_cards_show_return_phase(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.3)
        self.assertTrue(all(self.win._rtl_phase.get(d) == "RETURN" for d in self.ids))

    def test_monitor_requires_ground_and_disarmed(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        for did in self.ids:
            t = self.win._last_telem[did]
            t.position.alt_rel = 0.2
            t.armed = True
            self.win._last_alt[did] = 0.2
        _pump(1.2)
        self.assertTrue(self.win._rtl_active)
        for did in self.ids:
            self.win._last_telem[did].armed = False
        _pump(1.2)
        self.assertFalse(self.win._rtl_active)

    def test_estop_aborts_core_return_ui_state(self):
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.3)
        self.win._do_estop(self.ids, "ALL")
        _pump(0.3)
        self.assertFalse(self.win._rtl_active)
        self.assertEqual(self.win._rtl_phase, {})

    def test_rpc_error_keeps_return_state_uncertain_until_estop(self):
        self.fake.swarm_return = mock.Mock(side_effect=TimeoutError("deadline"))
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.4)
        self.assertTrue(self.win._rtl_active,
                        "RPC timeout may happen after core accepted; UI must not claim RETURN stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
