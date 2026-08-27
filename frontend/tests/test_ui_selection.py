"""
Mock/UI tests สำหรับงานรอบนี้ (สเปกข้อ 7 — Self-Verification)

รัน headless (ไม่ต้องมี core/SITL/จอ):
    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_ui_selection -v

ครอบคลุม:
  - ข้อ 1: เลือกโดรนจากการ์ดซ้าย / Ctrl+Click หลายลำ / ปุ่ม FLEET เปิด-ปิด
  - ข้อ 2: บั๊ก Swarm take off (ลำดับต้องเป็น takeoff ก่อน → form up ทีหลัง)
  - ข้อ 3: Quick actions ในการ์ดล่าง (Arm/Disarm/Land/RTL/Hold) + ALT/SPACING รายลำ
  - ข้อ 6: คิวหลบชนใช้เฉพาะลำที่เลือก
"""
import os
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

from PyQt5.QtWidgets import QApplication, QLabel, QFrame  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402

_app = QApplication.instance() or QApplication([])
_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"


class _R:
    ok = True
    message = "ok"


class FakeClient:
    """ดักคำสั่งที่ยิงออกไป — ตรวจได้ว่าสั่งอะไร ลำไหน ลำดับใด"""

    def __init__(self):
        self.calls = []

    def takeoff(self, ids, alt, confirmed=False):
        self.calls.append(("takeoff", tuple(ids), float(alt))); return _R()

    def arm(self, ids, force=False):
        self.calls.append(("arm", tuple(ids))); return _R()

    def disarm(self, ids, confirmed=False):
        self.calls.append(("disarm", tuple(ids))); return _R()

    def set_mode(self, ids, mode):
        self.calls.append(("set_mode", tuple(ids), int(mode))); return _R()

    # ── servo (A=ch7, B=ch8) — spec: servo.md ──
    def servo_set(self, drone_id, channel, pwm):
        self.calls.append(("servo_set", int(drone_id), int(channel), int(pwm))); return _R()

    def servo_release(self, drone_id, channel):
        self.calls.append(("servo_release", int(drone_id), int(channel))); return _R()

    def servo_reset(self, drone_id):
        self.calls.append(("servo_reset", int(drone_id))); return _R()

    def land(self, ids):
        self.calls.append(("land", tuple(ids))); return _R()

    def rtl(self, ids):
        self.calls.append(("rtl", tuple(ids))); return _R()

    def hold(self, ids):
        self.calls.append(("hold", tuple(ids))); return _R()

    def stop_all(self, ids):
        self.calls.append(("stop_all", tuple(ids))); return _R()

    def goto(self, drone_id, lat, lon, alt):
        self.calls.append(("goto", int(drone_id), float(lat), float(lon), float(alt)))
        return _R()

    def change_alt(self, ids, alt):
        self.calls.append(("change_alt", tuple(ids), float(alt))); return _R()

    def swarm_config(self, sep, formation=None):
        self.calls.append(("swarm_config", sep, formation)); return _R()

    def swarm_start(self):
        self.calls.append(("swarm_start",)); return _R()

    def swarm_stop(self):
        self.calls.append(("swarm_stop",)); return _R()

    def swarm_return(self, ids=None, base_alt=15.0, gap=5.0):
        self.calls.append(("swarm_return", tuple(ids or []), float(base_alt), float(gap)))
        return _R()

    def set_leader(self, i):
        return _R()

    def kinds(self):
        return [c[0] for c in self.calls]


class _FakePos:
    def __init__(self, lat, lon, alt_rel=0.0):
        self.lat, self.lon = lat, lon
        self.alt_rel, self.alt_abs = alt_rel, alt_rel


def _fake_telem(did, lat, lon, alt_rel=0.0):
    """Telemetry ปลอมที่มี field ครบพอให้ DroneCard/SelectedDroneCard วาดได้"""
    class _T:
        drone_id = did
        name = f"Drone {did}"
        status = 0            # LINK_STATUS_* (0 = OFFLINE) — ผ่าน rpc.status_name ได้
        mode = 0              # FLIGHT_MODE_*
        position = _FakePos(lat, lon, alt_rel)
        battery_pct = 100.0
        voltage = 12.4
        ground_speed = 0.0
        heading = 0.0
        gps_fix = 3
        sat_count = 10
        host = "127.0.0.1"
        port = 5760
        rssi = -60
    return _T()


def _pump(seconds=0.8):
    """ปล่อยให้ QTimer/thread ที่ค้างอยู่ทำงานให้เสร็จ"""
    end = time.time() + seconds
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.02)


class Base(unittest.TestCase):
    def setUp(self):
        self._confirm_patch = mock.patch(_CONFIRM, return_value=True)
        self._confirm_patch.start()
        self.win = GroundStation("127.0.0.1:59999")
        # ล้างฝูงที่โหลดมาจาก SQLite ให้เริ่มสะอาด
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        # ด่าน PRE-FLIGHT จะเด้ง popup ถามก่อน TAKEOFF — เทสชุดนี้ไม่ได้ทดสอบด่านนั้น
        # (ดู tests/test_preflight.py) จึงตั้งว่าเทสผ่านแล้วให้ผ่านไปเงียบ ๆ
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        for d in (1, 2, 3, 4, 5):
            self._add(d)
        self.win._refresh_takeoff_panel()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()
        self._confirm_patch.stop()

    def _add(self, did):
        it = FleetItem(did, f"Drone {did}", self.win._pixmap)
        self.win._wire_fleet_item(it)
        self.win.fleet_items[did] = it
        self.win.fleet_area.addWidget(it)
        self.win._last_seen[did] = time.monotonic()


# ─────────────────────────────────────────────────────────────
#  ข้อ 1 — การเลือกโดรน + ปุ่ม FLEET
# ─────────────────────────────────────────────────────────────
class TestSelection(Base):
    def test_plain_click_selects_single(self):
        self.win._on_fleet_click(3, False)
        self.assertEqual(self.win._selected_or_all(), [3])
        self.assertEqual(self.win._selected_id, 3)

    def test_plain_click_replaces_previous(self):
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(4, False)
        self.assertEqual(self.win._selected_or_all(), [4])

    def test_ctrl_click_adds_multiple(self):
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(3, True)
        self.win._on_fleet_click(5, True)
        self.assertEqual(self.win._selected_or_all(), [1, 3, 5])

    def test_ctrl_click_toggles_off(self):
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(3, True)
        self.win._on_fleet_click(3, True)      # กดซ้ำ = เอาออก
        self.assertEqual(self.win._selected_or_all(), [1])

    def test_selected_cards_are_highlighted(self):
        self.win._on_fleet_click(2, False)
        self.win._on_fleet_click(4, True)
        self.assertTrue(self.win.fleet_items[2]._selected)
        self.assertTrue(self.win.fleet_items[4]._selected)
        self.assertFalse(self.win.fleet_items[1]._selected)

    def test_fleet_button_on_selects_all(self):
        self.win._on_fleet_toggled(True)
        self.assertEqual(self.win._selected_or_all(), [1, 2, 3, 4, 5])
        for did in (1, 2, 3, 4, 5):
            self.assertTrue(self.win.fleet_items[did]._selected,
                            f"การ์ด D{did} ต้องถูกไฮไลต์เมื่อเปิด FLEET")

    def test_fleet_button_off_clears_all(self):
        self.win._on_fleet_toggled(True)
        self.win._on_fleet_toggled(False)
        self.assertEqual(self.win._selected_or_all(), [])
        for did in (1, 2, 3, 4, 5):
            self.assertFalse(self.win.fleet_items[did]._selected)

    def test_manual_click_after_fleet_turns_fleet_off(self):
        self.win._on_fleet_toggled(True)
        self.win._on_fleet_click(2, False)     # เลือกเองลำเดียว
        self.assertEqual(self.win._selected_or_all(), [2])
        self.assertFalse(self.win.takeoff_panel.fleet_on(),
                         "เลือกเองแล้วปุ่ม FLEET ต้องดับ")

    def test_selecting_all_manually_lights_fleet_button(self):
        self.win._on_fleet_click(1, False)
        for d in (2, 3, 4, 5):
            self.win._on_fleet_click(d, True)
        self.assertTrue(self.win.takeoff_panel.fleet_on(),
                        "เลือกครบทุกลำเอง ปุ่ม FLEET ควรติด")

    def test_commands_target_selection(self):
        self.win._on_fleet_click(2, False)
        self.win._on_fleet_click(5, True)
        self.assertEqual(self.win._target_ids(), [2, 5])

    def test_removed_drone_drops_out_of_selection(self):
        self.win._on_fleet_toggled(True)
        self.win._remove_fleet_item(3)
        self.assertNotIn(3, self.win._selected_or_all())


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 — Take off (บั๊ก swarm)
# ─────────────────────────────────────────────────────────────
class TestTakeoff(Base):
    def test_takeoff_cancelled_at_confirmation_sends_nothing(self):
        self.win._on_fleet_toggled(True)
        with mock.patch(_CONFIRM, return_value=False):
            self.win._on_panel_takeoff("all")
        _pump(0.2)
        self.assertEqual([c for c in self.fake.calls if c[0] == "takeoff"], [])

    def test_takeoff_all_mode_sends_every_selected(self):
        self.win._on_fleet_toggled(True)
        self.win.takeoff_panel.seg_mode.setCurrent(0)
        self.win._on_panel_takeoff("all")
        _pump(0.5)
        sent = sorted(c[1][0] for c in self.fake.calls if c[0] == "takeoff")
        self.assertEqual(sent, [1, 2, 3, 4, 5])

    def test_takeoff_only_selected(self):
        self.win._on_fleet_click(2, False)
        self.win._on_fleet_click(4, True)
        self.win._on_panel_takeoff("all")
        _pump(0.5)
        sent = sorted(c[1][0] for c in self.fake.calls if c[0] == "takeoff")
        self.assertEqual(sent, [2, 4])

    def test_takeoff_without_selection_sends_nothing(self):
        self.win._on_fleet_toggled(False)
        self.win._on_panel_takeoff("all")
        _pump(0.3)
        self.assertEqual([c for c in self.fake.calls if c[0] == "takeoff"], [])

    def test_default_altitude_is_20m(self):
        self.win._on_fleet_click(1, False)
        self.win._on_panel_takeoff("all")
        _pump(0.5)
        alts = [c[2] for c in self.fake.calls if c[0] == "takeoff"]
        self.assertEqual(alts, [20.0])

    def test_per_drone_altitude_from_card(self):
        self.win._on_card_alt_changed(2, 35)
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.win._on_panel_takeoff("all")
        _pump(0.5)
        by_id = {c[1][0]: c[2] for c in self.fake.calls if c[0] == "takeoff"}
        self.assertEqual(by_id[2], 35.0)
        self.assertEqual(by_id[1], 20.0)     # ไม่ได้ตั้ง → default 20

    def test_sequential_head_goes_first(self):
        self.win._apply_head(3, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.win._on_panel_takeoff("sequential")
        _pump(2.0)
        order = [c[1][0] for c in self.fake.calls if c[0] == "takeoff"]
        self.assertEqual(order[0], 3, f"Head ต้องขึ้นก่อน แต่ได้ {order}")

    # ── BUGFIX หลัก: swarm takeoff ต้อง takeoff ก่อน แล้วค่อย form up ──
    def test_swarm_takeoff_sends_takeoff_commands(self):
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        _pump(1.0)
        sent = sorted(c[1][0] for c in self.fake.calls if c[0] == "takeoff")
        self.assertEqual(sent, [1, 2, 3, 4, 5],
                         "Swarm take off ต้องสั่งขึ้นบินจริงทุกลำที่เลือก")

    def test_swarm_takeoff_order_takeoff_before_formup(self):
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        _pump(1.0)
        kinds = self.fake.kinds()
        self.assertIn("takeoff", kinds)
        first_takeoff = kinds.index("takeoff")
        if "swarm_start" in kinds:
            self.assertLess(first_takeoff, kinds.index("swarm_start"),
                            "ต้อง takeoff ก่อน form up — ไม่งั้นสั่งขบวนใส่โดรนที่ยังอยู่บนพื้น")

    def test_swarm_formup_waits_until_airborne(self):
        """form up ต้องยังไม่ถูกสั่งขณะโดรนยังอยู่บนพื้น (alt = 0)"""
        self.win._on_fleet_toggled(True)
        for d in (1, 2, 3, 4, 5):
            self.win._last_alt[d] = 0.0
        self.win._swarm_takeoff()
        _pump(3.5)
        self.assertNotIn("swarm_start", self.fake.kinds(),
                         "ยังอยู่บนพื้นแต่สั่ง FORM UP แล้ว — จะตีกับ takeoff")

    def test_swarm_formup_fires_after_airborne(self):
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        for d in (1, 2, 3, 4, 5):     # จำลองว่าลอยถึงระดับแล้ว
            self.win._last_alt[d] = 20.0
        _pump(4.0)
        self.assertIn("swarm_start", self.fake.kinds(),
                      "ลอยตัวแล้วต้อง FORM UP อัตโนมัติ")


# ─────────────────────────────────────────────────────────────
#  ข้อ 3 — Quick actions + พารามิเตอร์รายลำในการ์ดล่าง
# ─────────────────────────────────────────────────────────────
class TestBottomCard(Base):
    def setUp(self):
        super().setUp()
        self.win._select_drone(2)
        self.card = self.win.sel_card

    def test_quick_action_buttons_exist(self):
        for name in ("btn_arm", "btn_disarm", "btn_land", "btn_rtl", "btn_hold"):
            self.assertTrue(hasattr(self.card, name), f"ขาดปุ่ม {name} ในการ์ดล่าง")

    def test_arm_targets_only_that_drone(self):
        self.card.arm_req.emit(2)
        _pump(0.4)
        self.assertIn(("arm", (2,)), self.fake.calls)

    def test_rtl_land_hold_target_that_drone(self):
        self.card.rtl_req.emit(2)
        self.card.land_req.emit(2)
        self.card.hold_req.emit(2)
        _pump(0.5)
        self.assertIn(("rtl", (2,)), self.fake.calls)
        self.assertIn(("land", (2,)), self.fake.calls)
        self.assertIn(("hold", (2,)), self.fake.calls)

    def test_spacing_field_exists_and_stores_per_drone(self):
        self.assertTrue(hasattr(self.card, "spin_spacing"))
        self.win._on_card_spacing_changed(2, 8)
        self.win._on_card_spacing_changed(3, 15)
        self.assertEqual(self.win._drone_spacing[2], 8.0)
        self.assertEqual(self.win._drone_spacing[3], 15.0)

    def test_alt_field_stores_per_drone(self):
        self.win._on_card_alt_changed(2, 30)
        self.assertEqual(self.win._alt_for(2), 30.0)
        self.assertEqual(self.win._alt_for(5), 20.0)   # ไม่ได้ตั้ง → default

    def test_card_loads_params_on_select(self):
        self.win._on_card_alt_changed(4, 45)
        self.win._on_card_spacing_changed(4, 9)
        self.win._select_drone(4)
        self.assertEqual(self.card.spin_alt.value(), 45)
        self.assertEqual(self.card.spin_spacing.value(), 9)

    def test_name_can_be_changed_from_selected_card(self):
        self.card.rename_req.emit(2, "ทีมสำรวจ")
        self.assertEqual(self.win._drone_names[2], "ทีมสำรวจ")
        self.assertEqual(self.win.fleet_items[2].lbl_name.text(), "ทีมสำรวจ")
        self.assertEqual(self.card.lbl_name.text(), "ทีมสำรวจ")

    def test_changed_name_is_saved_with_current_endpoint(self):
        self.win._endpoints[2] = ("10.0.0.2", 5760)
        with mock.patch.object(self.win._ip_store, "upsert") as save:
            self.win._on_drone_renamed(2, "โดรนสำรวจ")
        self.assertEqual(save.call_args.kwargs["name"], "โดรนสำรวจ")

    def test_parameter_explanations_are_visible_below_fields(self):
        texts = [w.text() for w in self.card.findChildren(QLabel)]
        self.assertTrue(any("TAKEOFF ALT =" in t and "SPACING =" in t for t in texts))

    def test_mode_metric_keeps_prominent_white_frame_before_telemetry(self):
        mode_cells = [w for w in self.card.findChildren(QFrame)
                      if w.objectName() == "ModeMetric"]
        self.assertEqual(len(mode_cells), 1)
        self.assertIn("255,255,255", mode_cells[0].styleSheet())

    def test_selected_card_frame_uses_selected_drone_color(self):
        self.assertIn("border:2px solid", self.card.styleSheet())
        self.assertIn("rgba(", self.card.styleSheet())

    def test_mode_metric_uses_status_color_after_telemetry(self):
        self.card.update_from_telemetry(_fake_telem(2, 14.0, 100.0))
        mode_cells = [w for w in self.card.findChildren(QFrame)
                      if w.objectName() == "ModeMetric"]
        self.assertIn("border:2px solid", mode_cells[0].styleSheet())


# ─────────────────────────────────────────────────────────────
#  ข้อ 6 — คิวหลบชน ใช้เฉพาะลำที่เลือก
# ─────────────────────────────────────────────────────────────
class TestCollisionOrder(Base):
    def setUp(self):
        super().setUp()
        # เรียงหน้ากระดาน: D1 ซ้ายสุด → D5 ขวาสุด
        self.win._last_telem = {d: _fake_telem(d, 14.0, 100.000 + 0.00003 * (d - 1))
                                for d in (1, 2, 3, 4, 5)}
        self.cp = __import__("swarmgod_gui.core.rpc",
                             fromlist=["command_pb2"]).command_pb2

    def test_move_right_rightmost_first(self):
        self.win._on_fleet_toggled(True)
        self.assertEqual(self.win._ordered_fleet(self.cp.RC_DIR_RIGHT), [5, 4, 3, 2, 1])

    def test_move_left_leftmost_first(self):
        self.win._on_fleet_toggled(True)
        self.assertEqual(self.win._ordered_fleet(self.cp.RC_DIR_LEFT), [1, 2, 3, 4, 5])

    def test_order_limited_to_selected(self):
        self.win._on_fleet_click(2, False)
        self.win._on_fleet_click(4, True)
        self.assertEqual(self.win._ordered_fleet(self.cp.RC_DIR_RIGHT), [4, 2])

    def test_single_selection_skips_queueing(self):
        self.win._on_fleet_click(3, False)
        self.assertFalse(self.win._multi_move(),
                         "เลือกลำเดียวไม่ต้องจัดคิวกันชน")

    def test_multi_selection_enables_queueing(self):
        self.win._on_fleet_click(1, False)
        self.win._on_fleet_click(2, True)
        self.assertTrue(self.win._multi_move())


if __name__ == "__main__":
    unittest.main(verbosity=2)
