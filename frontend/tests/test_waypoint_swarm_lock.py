"""
เทสต์ SWARM MODE LOCK ของ Waypoint (spec T6)

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest tests/test_waypoint_swarm_lock.py -q

เมื่อ SWARM ACTIVE ต้องใช้ GROUPED Leader Path เท่านั้น:
  - SEPARATE/WAVE disabled ทั้ง UI + logic guard
  - เข้า Swarm บังคับ GROUPED + ปิด WAVE
  - ออก Swarm controls กลับมา enable แต่ state ไม่ restore
  - Leader Path ยังตั้ง action A/B + WAIT ได้
"""
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

from tests.test_waypoint_ui import Base, _pump  # noqa: E402

_GETINT = "swarmgod_gui.app.QInputDialog.getInt"


class _SwarmState:
    def __init__(self, active=True, leader_id=1):
        self.active = active
        self.leader_id = leader_id
        self.edges = []
        self.formation = 1
        self.spacing = 12.0
        self.note = ""


class SwarmLockBase(Base):
    def _assign_two_groups(self):
        """แบ่งโดรนเป็น 2 กลุ่ม เพื่อให้ WAVE เปิดได้ (ต้องมี ≥2 กลุ่ม)"""
        self.win.group_of.update({1: 1, 2: 1, 3: 2, 4: 2, 5: 2})
        self.win._refresh_wave_group_choices()

    def _enter_swarm(self, leader_id=2):
        self.win._apply_head(leader_id, push=False, reason="manual")
        self.win._on_swarm_update(_SwarmState(active=True, leader_id=leader_id))
        _pump(0.1)

    def _exit_swarm(self):
        self.win._on_swarm_update(_SwarmState(active=False, leader_id=0))
        _pump(0.1)


class TestSwarmForcesGrouped(SwarmLockBase):
    def test_entering_swarm_forces_grouped(self):
        self.win._on_fleet_toggled(True)
        self.win._wp_set_separate(True)
        self.assertTrue(self.win._wp_separate)
        self._enter_swarm()
        self.assertFalse(self.win._wp_separate, "เข้า Swarm ต้องบังคับกลับ GROUPED")

    def test_entering_swarm_turns_off_idle_wave(self):
        self._assign_two_groups()
        self.win._on_fleet_toggled(True)
        self.win._wave_toggle(True)
        self.assertTrue(self.win._wave_enabled)
        self._enter_swarm()
        self.assertFalse(self.win._wave_enabled, "เข้า Swarm ต้องปิด WAVE ที่เปิดค้าง")


class TestSwarmDisablesControls(SwarmLockBase):
    def test_separate_control_disabled(self):
        self._enter_swarm()
        self.assertFalse(self.win.seg_wp_mode.isEnabled(),
                         "SEPARATE control ต้อง disabled ตอน Swarm")

    def test_wave_switch_disabled(self):
        self._enter_swarm()
        self.assertFalse(self.win.sw_wave.isEnabled(),
                         "WAVE switch ต้อง disabled ตอน Swarm")

    def test_grouped_still_enabled_conceptually(self):
        """GROUPED ยังใช้งานได้ (วางจุด Leader Path ได้)"""
        self.win._on_fleet_toggled(True)
        self._enter_swarm()
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.5, 100.5)
        self.assertIsNotNone(self.win._waypoint_route)
        self.assertEqual(len(self.win._waypoint_route), 1)


class TestSwarmLogicGuards(SwarmLockBase):
    def test_set_separate_true_rejected_by_logic(self):
        self._enter_swarm()
        self.win._wp_set_separate(True)      # เรียกจาก code ตรง ๆ
        self.assertFalse(self.win._wp_separate,
                         "logic guard ต้อง reject SEPARATE แม้เรียกจาก code")

    def test_wave_toggle_true_rejected_by_logic(self):
        self._enter_swarm()
        self.win._wave_toggle(True)          # เรียกจาก code ตรง ๆ
        self.assertFalse(self.win._wave_enabled,
                         "logic guard ต้อง reject WAVE แม้เรียกจาก code")

    def test_swarm_start_rejected_while_wave_executing(self):
        # จำลอง WAVE กำลัง execute แล้วพยายามเริ่ม Swarm ทาง UI
        self.win._wave_executing = True
        self.win._swarm_start()
        _pump(0.2)
        self.assertNotIn("swarm_start", [c[0] for c in self.fake.calls],
                         "ห้ามเริ่ม Swarm ขณะ WAVE execute")


class TestSwarmExit(SwarmLockBase):
    def test_exit_reenables_controls(self):
        self._enter_swarm()
        self._exit_swarm()
        self.assertTrue(self.win.seg_wp_mode.isEnabled())
        self.assertTrue(self.win.sw_wave.isEnabled())

    def test_exit_does_not_restore_prior_state(self):
        self.win._on_fleet_toggled(True)
        self.win._wp_set_separate(True)      # เคยเป็น SEPARATE ก่อนเข้า Swarm
        self._enter_swarm()
        self._exit_swarm()
        self.assertFalse(self.win._wp_separate,
                         "ออก Swarm ต้องคง GROUPED · ไม่ restore SEPARATE เดิม")
        self.assertFalse(self.win._wave_enabled,
                         "ออก Swarm ต้องคง WAVE OFF")


class TestSwarmDefensiveWaveStop(SwarmLockBase):
    def test_core_reports_swarm_while_wave_executing(self):
        """Core รายงาน Swarm Active ขณะ WAVE ยังวิ่ง — invalidate โดยไม่ยิงคำสั่งทับ"""
        self.win._wave_executing = True
        self.win._waypoint_executing = True
        self._enter_swarm()
        self.assertFalse(self.win._wave_executing)
        self.assertFalse(self.win._waypoint_executing)
        self.assertFalse(self.win._wp_waits)
        # ห้ามยิงคำสั่ง flight ที่อาจชนกับ Core swarm
        for kind in ("stop_all", "hold", "swarm_stop"):
            self.assertNotIn(kind, [c[0] for c in self.fake.calls],
                             f"defensive stop ห้ามส่ง {kind} ทับ Core swarm")


class TestLeaderPathStillWorks(SwarmLockBase):
    def setUp(self):
        super().setUp()
        self.win._on_fleet_toggled(True)
        self._enter_swarm(leader_id=2)
        self.win._wp_toggle(True)
        self.win._on_waypoint_click(14.50, 100.50)
        self.win._on_waypoint_click(14.60, 100.60)
        self.key = self.win._wp_key   # Leader Path = 0

    def test_leader_path_sets_action(self):
        self.win._wp_set_action(self.key, 0, "servo_a")
        self.assertEqual(self.win._waypoint_route.points[0].action, "servo_a")

    def test_leader_path_sets_wait(self):
        with mock.patch(_GETINT, return_value=(4, True)):
            self.win._wp_prompt_wait(self.key, 1)
        self.assertEqual(self.win._waypoint_route.points[1].wait_seconds, 240)


if __name__ == "__main__":
    unittest.main(verbosity=2)
