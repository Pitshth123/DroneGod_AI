"""
เทสต์รอบแก้บั๊ก Head / Swarm takeoff / RTL config / Mode badge

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_head_rtlcfg_mode -v

ครอบคลุม:
  ข้อ 1 — swarm takeoff: ลำแม่ต้องถูกสั่งขึ้นด้วย + form up ต้องรอแม่ลอยก่อน
  ข้อ 2 — เปลี่ยน Head ได้จริง (ไม่เด้งกลับ) + เงื่อนไข 1.1-1.4
  ข้อ 3 — เปลี่ยน Head จาก dropdown ได้จริง
  ข้อ 4 — config RTL (ชั้นล่างสุด/ระยะห่างชั้น) + ปุ่มบันทึกแล้วนำไปใช้
  ข้อ 5 — ป้ายบอกโหมดการบิน SWARM / FLIGHT
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

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from swarmgod_gui.core import swarm_logic as SL  # noqa: E402

from tests.test_ui_selection import FakeClient, _fake_telem, _pump  # noqa: E402

_app = QApplication.instance() or QApplication([])

_CONFIRM = "swarmgod_gui.widgets.confirm.confirm"


class _SwarmState:
    """SwarmState ปลอมจาก core"""
    def __init__(self, active=True, leader_id=1):
        self.active = active
        self.leader_id = leader_id
        self.edges = []
        self.formation = 1
        self.spacing = 12.0
        self.note = ""


class FakeClient2(FakeClient):
    def __init__(self):
        super().__init__()
        self.leader_pushes = []

    def set_leader(self, leader_id, followers=None):
        self.leader_pushes.append(int(leader_id))
        self.calls.append(("set_leader", int(leader_id)))

        class _R:
            ok = True
            message = "ok"
        return _R()


class Base(unittest.TestCase):
    N = 5

    def setUp(self):
        self._confirm_patch = mock.patch(_CONFIRM, return_value=True)
        self._confirm_patch.start()
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient2()
        self.win.client = self.fake
        # ด่าน PRE-FLIGHT จะเด้ง popup ถามก่อน TAKEOFF — เทสชุดนี้ไม่ได้ทดสอบด่านนั้น
        # (ดู tests/test_preflight.py) จึงตั้งว่าเทสผ่านแล้วให้ผ่านไปเงียบ ๆ
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        self.ids = list(range(1, self.N + 1))
        for d in self.ids:
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._home_pos[d] = (14.0, 100.0)
            self.win._last_alt[d] = 0.0
            self.win._last_telem[d] = _fake_telem(d, 14.0, 100.0 + 0.0002 * d)
        self.win._refresh_takeoff_panel()
        self.win._refresh_leader_combo()

    def tearDown(self):
        self.win._rtl_active = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()
        self._confirm_patch.stop()

    def airborne(self, alt=20.0, only=None):
        for d in (only or self.ids):
            self.win._last_alt[d] = alt
            self.win._last_telem[d] = _fake_telem(
                d, 14.0, 100.0 + 0.0002 * d, alt_rel=alt)

    def kinds(self):
        return [c[0] for c in self.fake.calls]


# ─────────────────────────────────────────────────────────────
#  ข้อ 1 — ลำแม่ต้องขึ้นบินด้วยใน Swarm takeoff
# ─────────────────────────────────────────────────────────────
class TestSwarmTakeoffHead(Base):
    def test_head_is_included_in_takeoff(self):
        self.win._apply_head(1, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        _pump(1.2)
        sent = sorted(c[1][0] for c in self.fake.calls if c[0] == "takeoff")
        self.assertIn(1, sent, "ลำแม่ไม่ถูกสั่งขึ้นบิน")
        self.assertEqual(sent, [1, 2, 3, 4, 5])

    def test_head_included_when_head_is_not_drone_one(self):
        self.win._apply_head(3, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        _pump(1.2)
        sent = sorted(c[1][0] for c in self.fake.calls if c[0] == "takeoff")
        self.assertIn(3, sent)

    def test_formup_waits_for_head_even_if_others_airborne(self):
        """บั๊ก: ลูกขึ้นครบแต่แม่ยังอยู่บนพื้น → เดิม form up เลย ตอนนี้ต้องรอ"""
        self.win._apply_head(1, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        self.airborne(20.0, only=[2, 3, 4, 5])   # ทุกลำขึ้น ยกเว้นแม่
        self.win._last_alt[1] = 0.0
        _pump(5.0)
        self.assertNotIn("swarm_start", self.kinds(),
                         "แม่ยังไม่ลอย แต่ FORM UP ไปแล้ว")

    def test_formup_proceeds_once_head_airborne(self):
        self.win._apply_head(1, push=False, reason="manual")
        self.win._on_fleet_toggled(True)
        self.win._swarm_takeoff()
        self.airborne(20.0)                      # ขึ้นครบรวมแม่
        _pump(4.5)
        self.assertIn("swarm_start", self.kinds(),
                      "ขึ้นครบแล้วแต่ไม่ FORM UP")


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 + 3 — เปลี่ยน Head ต้องอยู่ ไม่เด้งกลับ
# ─────────────────────────────────────────────────────────────
class TestHeadChange(Base):
    def test_change_head_via_card(self):
        self.win._apply_head(1, push=False, reason="manual")
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        self.assertEqual(self.win._head_id, 2)

    def test_change_head_pushes_to_core(self):
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        _pump(0.4)
        self.assertIn(2, self.fake.leader_pushes,
                      "ไม่ได้ส่ง SetLeader ไปที่ core")

    def test_cancel_confirm_keeps_old_head(self):
        self.win._apply_head(1, push=False, reason="manual")
        with mock.patch(_CONFIRM, return_value=False):
            self.win._on_head_req(2)
        self.assertEqual(self.win._head_id, 1)

    def test_swarm_poll_does_not_revert_user_choice(self):
        """บั๊กหลัก: poll ทุก 1.2s ดึง head กลับไปลำ 1"""
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        self.assertEqual(self.win._head_id, 2)
        for _ in range(5):                       # core ยังรายงาน leader=1
            self.win._on_swarm_update(_SwarmState(active=True, leader_id=1))
        self.assertEqual(self.win._head_id, 2,
                         "Head ถูกดึงกลับเป็นลำ 1 หลัง swarm poll")

    def test_mismatch_repushes_setleader(self):
        """core ไม่ตรงกับที่เลือก → ต้องยืนยันค่ากลับไป ไม่ใช่ยอมตาม"""
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        self.fake.leader_pushes.clear()
        self.win._head_push_ts = 0.0
        self.win._on_swarm_update(_SwarmState(active=True, leader_id=1))
        _pump(0.4)
        self.assertIn(2, self.fake.leader_pushes)

    def test_failover_still_works_when_pinned_drone_lost(self):
        """ลำที่ปักหมุดหลุด → ต้องยอมให้ core failover"""
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        self.win._last_seen.pop(2, None)          # ลำ 2 หลุด
        self.win._on_swarm_update(_SwarmState(active=True, leader_id=3))
        self.assertEqual(self.win._head_id, 3)

    # ── dropdown (ข้อ 3) ──
    def test_change_head_via_dropdown(self):
        self.win._apply_head(1, push=False, reason="manual")
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_leader_combo("Drone 4")
        self.assertEqual(self.win._head_id, 4)

    def test_dropdown_choice_survives_swarm_poll(self):
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_leader_combo("Drone 4")
        for _ in range(5):
            self.win._on_swarm_update(_SwarmState(active=True, leader_id=1))
        self.assertEqual(self.win._head_id, 4)

    def test_dropdown_auto_unpins(self):
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_leader_combo("Drone 4")
        self.win._on_leader_combo("Auto")
        _pump(0.3)
        self.assertEqual(self.win._head_pinned, 0)


# ─────────────────────────────────────────────────────────────
#  ข้อ 2 (1.1-1.4) — เงื่อนไขว่าเปลี่ยน Head ได้เมื่อไหร่
# ─────────────────────────────────────────────────────────────
class TestHeadChangeConditions(Base):
    def _set_status(self, did, status_name):
        from swarmgod_gui.core import rpc
        enum = rpc.common_pb2.LinkStatus.Value("LINK_STATUS_" + status_name)
        t = _fake_telem(did, 14.0, 100.0, alt_rel=self.win._last_alt.get(did, 0.0))
        t.status = enum
        self.win._last_telem[did] = t

    def test_1_1_allowed_before_takeoff(self):
        """1.1 ยังไม่ takeoff → เปลี่ยนได้"""
        for d in self.ids:
            self._set_status(d, "READY")
        self.assertIsNone(self.win._head_change_block_reason())

    def test_1_2_allowed_while_flying(self):
        """1.2 ลอยอยู่กลางอากาศ → เปลี่ยนได้"""
        self.airborne(20.0)
        for d in self.ids:
            self._set_status(d, "FLYING")
        self.assertIsNone(self.win._head_change_block_reason())

    def test_1_3_blocked_during_takeoff(self):
        self._set_status(2, "TAKEOFF")
        self.assertIsNotNone(self.win._head_change_block_reason())

    def test_1_3_blocked_during_landing(self):
        self._set_status(3, "LANDING")
        self.assertIsNotNone(self.win._head_change_block_reason())

    def test_1_3_head_request_rejected_during_takeoff(self):
        self.win._apply_head(1, push=False, reason="manual")
        self._set_status(2, "TAKEOFF")
        with mock.patch(_CONFIRM, return_value=True):
            self.win._on_head_req(2)
        self.assertEqual(self.win._head_id, 1, "ห้ามเปลี่ยน Head ตอนกำลัง takeoff")

    def test_1_4_blocked_while_formation_moving(self):
        """1.4 ขบวนกำลังเคลื่อนที่ → ห้ามเปลี่ยน"""
        self.airborne(20.0)
        self.win._swarm_active = True
        t = _fake_telem(2, 14.0, 100.0, alt_rel=20.0)
        t.ground_speed = 4.0
        self.win._last_telem[2] = t
        self.assertIsNotNone(self.win._head_change_block_reason())

    def test_1_4_allowed_when_formation_hovering(self):
        """swarm เปิดอยู่แต่ลอยนิ่ง → เปลี่ยนได้ (ตรงกับ 1.2)"""
        self.airborne(20.0)
        self.win._swarm_active = True
        for d in self.ids:
            self._set_status(d, "FLYING")   # ground_speed = 0
        self.assertIsNone(self.win._head_change_block_reason())

    def test_blocked_during_rtl(self):
        self.win._rtl_active = True
        self.assertIsNotNone(self.win._head_change_block_reason())


# ─────────────────────────────────────────────────────────────
#  ข้อ 4 — RTL config
# ─────────────────────────────────────────────────────────────
class LegacyRtlConfig:
    """Migration notes for removed +offset RTL settings; not a TestCase."""
    def test_sliders_exist(self):
        self.assertTrue(hasattr(self.win, "sf_rtl_alt"))
        self.assertTrue(hasattr(self.win, "sf_rtl_gap"))

    def test_base_alt_minimum_is_two(self):
        """สเปก: ต้องเริ่มที่ 2 เมตร → ตั้งต่ำกว่า 2 ไม่ได้
        (ค่าที่แสดงจริงอาจเป็นค่าที่ผู้ใช้เคยบันทึกไว้ ซึ่งถูกต้องแล้ว)"""
        self.win.sf_rtl_alt.setValue(0)
        self.assertGreaterEqual(self.win.sf_rtl_alt.value(), 2)

    def test_factory_default_is_two(self):
        """ค่าดีฟอลต์จากโรงงาน (ก่อนโหลด settings ของผู้ใช้) = 2 m"""
        from swarmgod_gui.widgets.controls import SliderField
        sf = SliderField("t", 2, 120, 2, 1, "m", 0)
        self.assertEqual(sf.value(), 2)

    def test_save_applies_to_runtime(self):
        self.win.sf_rtl_alt.setValue(4)
        self.win.sf_rtl_gap.setValue(3)
        with mock.patch.object(self.win, "_save_settings"):
            self.win._apply_and_save_system()
        self.assertEqual(self.win.RTL_BASE_ALT, 4.0)
        self.assertEqual(self.win.RTL_LAYER_GAP, 3.0)

    def test_saved_config_used_by_rtl(self):
        """config = ค่าที่ "บวกเพิ่ม" จากความสูงปัจจุบันของแต่ละลำ"""
        self.win.sf_rtl_alt.setValue(2)
        self.win.sf_rtl_gap.setValue(2)
        with mock.patch.object(self.win, "_save_settings"):
            self.win._apply_and_save_system()
        for i, d in enumerate(self.ids):        # 10,12,14,16,18 m
            self.win._last_alt[d] = 10.0 + 2.0 * i
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        got = {c[1][0]: c[2] for c in self.fake.calls if c[0] == "change_alt"}
        self.assertEqual(got, {1: 12.0, 2: 14.0, 3: 16.0, 4: 18.0, 5: 20.0},
                         "RTL ไม่ได้ใช้ค่า config ที่บันทึกไว้")

    def test_offset_config_changes_climb(self):
        self.win.sf_rtl_alt.setValue(6)
        self.win.sf_rtl_gap.setValue(2)
        with mock.patch.object(self.win, "_save_settings"):
            self.win._apply_and_save_system()
        for i, d in enumerate(self.ids):
            self.win._last_alt[d] = 10.0 + 2.0 * i
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        got = {c[1][0]: c[2] for c in self.fake.calls if c[0] == "change_alt"}
        self.assertEqual(got[1], 16.0, "10m + config 6m = 16m")

    def test_min_gap_prevents_overlap(self):
        """ทุกลำระดับเดียวกัน → ต้องเหลื่อมตาม min gap ไม่ทับกัน"""
        self.win.sf_rtl_alt.setValue(2)
        self.win.sf_rtl_gap.setValue(8)
        with mock.patch.object(self.win, "_save_settings"):
            self.win._apply_and_save_system()
        self.airborne(20.0)
        self.win._on_fleet_toggled(True)
        self.win._cmd_rtl()
        _pump(0.8)
        alts = sorted(c[2] for c in self.fake.calls if c[0] == "change_alt")
        gaps = [b - a for a, b in zip(alts, alts[1:])]
        self.assertTrue(all(g == 8.0 for g in gaps), f"ระยะห่างไม่ตรง config: {gaps}")

    def test_preview_reflects_config(self):
        for i, d in enumerate(self.ids):
            self.win._last_alt[d] = 10.0 + 2.0 * i
        self.win.sf_rtl_alt.setValue(2)
        self.win.sf_rtl_gap.setValue(2)
        txt = self.win.lbl_rtl_preview.text().replace(" ", "")
        self.assertIn("10→12m", txt)

    def test_config_is_persisted(self):
        self.win.sf_rtl_alt.setValue(6)
        self.win.sf_rtl_gap.setValue(4)
        data = self.win._collect_settings()
        self.assertEqual(data["sliders"]["rtl_alt"], 6)
        self.assertEqual(data["sliders"]["rtl_gap"], 4)

    def test_config_reloads(self):
        self.win._apply_settings({"sliders": {"rtl_alt": 7, "rtl_gap": 2}},
                                 replace_ips=False)
        self.assertEqual(self.win.RTL_BASE_ALT, 7.0)
        self.assertEqual(self.win.RTL_LAYER_GAP, 2.0)


class TestReturnConfig(Base):
    def test_safe_factory_minimums(self):
        self.win.sf_rtl_alt.setValue(0)
        self.win.sf_rtl_gap.setValue(0)
        self.assertGreaterEqual(self.win.sf_rtl_alt.value(), 15)
        self.assertGreaterEqual(self.win.sf_rtl_gap.value(), 5)

    def test_save_applies_absolute_plan_to_runtime(self):
        self.win.sf_rtl_alt.setValue(25)
        self.win.sf_rtl_gap.setValue(8)
        with mock.patch.object(self.win, "_save_settings"):
            self.win._apply_and_save_system()
        self.assertEqual(self.win.RTL_BASE_ALT, 25.0)
        self.assertEqual(self.win.RTL_LAYER_GAP, 8.0)

    def test_preview_is_absolute_not_current_plus_offset(self):
        self.win.sf_rtl_alt.setValue(20)
        self.win.sf_rtl_gap.setValue(5)
        text = self.win._rtl_preview_text(20, 5)
        self.assertIn("base 20m", text)
        self.assertIn("gap 5m", text)

    def test_config_persists(self):
        self.win.sf_rtl_alt.setValue(25)
        self.win.sf_rtl_gap.setValue(7)
        data = self.win._collect_settings()
        self.assertEqual(data["sliders"]["rtl_alt"], 25)
        self.assertEqual(data["sliders"]["rtl_gap"], 7)


# ─────────────────────────────────────────────────────────────
#  ข้อ 5 — ป้ายบอกโหมดการบิน
# ─────────────────────────────────────────────────────────────
class TestFlightModeBadge(Base):
    def test_badge_exists(self):
        self.assertTrue(hasattr(self.win, "mode_drop"))

    def test_default_is_flight(self):
        self.win._swarm_active = False
        self.win._rtl_active = False
        self.win._refresh_flight_mode()
        self.assertIn("FLIGHT", self.win.mode_drop.text())

    def test_swarm_mode_shown(self):
        self.win._on_swarm_update(_SwarmState(active=True, leader_id=1))
        self.win._refresh_flight_mode()
        self.assertIn("SWARM", self.win.mode_drop.text())

    def test_back_to_flight_when_swarm_stops(self):
        self.win._on_swarm_update(_SwarmState(active=True, leader_id=1))
        self.win._refresh_flight_mode()
        self.win._on_swarm_update(_SwarmState(active=False, leader_id=0))
        self.win._refresh_flight_mode()
        self.assertIn("FLIGHT", self.win.mode_drop.text())

    def test_rtl_mode_shown(self):
        self.win._rtl_active = True
        self.win._refresh_flight_mode()
        self.assertIn("RTL", self.win.mode_drop.text())

    def test_rtl_takes_priority_over_swarm(self):
        self.win._swarm_active = True
        self.win._rtl_active = True
        self.win._refresh_flight_mode()
        self.assertIn("RTL", self.win.mode_drop.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
