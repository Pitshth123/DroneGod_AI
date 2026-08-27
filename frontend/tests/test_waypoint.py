"""
เทสต์ waypoint_logic.py แบบ headless (ไม่ต้องมี Qt / gRPC)

    cd frontend
    python -m unittest tests.test_waypoint -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import waypoint_logic as WP  # noqa: E402


class TestWaypointRoute(unittest.TestCase):
    def test_add_waypoints(self):
        route = WP.WaypointRoute([1])
        w1 = route.add(14.958, 102.099)
        w2 = route.add(14.959, 102.100)
        w3 = route.add(14.960, 102.101)
        self.assertEqual(len(route), 3)
        self.assertEqual([w1.index, w2.index, w3.index], [0, 1, 2])
        self.assertEqual((w1.lat, w1.lon), (14.958, 102.099))

    def test_clear_waypoints(self):
        route = WP.WaypointRoute([1])
        route.add(14.0, 100.0)
        route.add(14.1, 100.1)
        route.clear()
        self.assertTrue(route.is_empty())
        self.assertEqual(len(route), 0)

    def test_remove_last(self):
        route = WP.WaypointRoute([1])
        route.add(14.0, 100.0)
        route.add(14.1, 100.1)
        removed = route.remove_last()
        self.assertEqual(len(route), 1)
        self.assertAlmostEqual(removed.lat, 14.1)
        self.assertAlmostEqual(removed.lon, 100.1)
        # จุดที่เหลือต้องเป็นจุดแรก
        self.assertAlmostEqual(route.points[0].lat, 14.0)

    def test_remove_last_empty(self):
        route = WP.WaypointRoute([1])
        result = route.remove_last()      # ต้องไม่ error
        self.assertIsNone(result)
        self.assertTrue(route.is_empty())

    def test_undo_index_does_not_reuse_after_readd(self):
        """undo แล้ววาดจุดใหม่ต่อ — index ต้องเดินหน้าต่อ ไม่ชนจุดที่เคยลบไป"""
        route = WP.WaypointRoute([1])
        route.add(14.0, 100.0)   # index 0
        route.add(14.1, 100.1)   # index 1
        route.remove_last()      # ลบ index 1 ออก
        w = route.add(14.2, 100.2)   # ต้องได้ index 2 ไม่ใช่ 1 ซ้ำ
        self.assertEqual(w.index, 2)

    def test_as_pairs(self):
        route = WP.WaypointRoute([1])
        route.add(14.0, 100.0)
        route.add(14.5, 100.5)
        self.assertEqual(route.as_pairs(), [(14.0, 100.0), (14.5, 100.5)])

    def test_is_empty(self):
        route = WP.WaypointRoute([1])
        self.assertTrue(route.is_empty())
        route.add(14.0, 100.0)
        self.assertFalse(route.is_empty())

    def test_is_swarm_flag(self):
        single = WP.WaypointRoute([3])
        multi = WP.WaypointRoute([1, 2, 3])
        self.assertFalse(single.is_swarm)
        self.assertTrue(multi.is_swarm)

    def test_drone_ids_deduped_and_ordered(self):
        route = WP.WaypointRoute([3, 1, 3, 2])
        self.assertEqual(route.drone_ids, [3, 1, 2])

    def test_len_matches_points(self):
        route = WP.WaypointRoute([1])
        self.assertEqual(len(route), 0)
        route.add(1.0, 2.0)
        route.add(3.0, 4.0)
        self.assertEqual(len(route), 2)

    def test_waypoint_action_defaults_empty_and_can_be_changed(self):
        route = WP.WaypointRoute([1])
        wp = route.add(13.0, 100.0)
        self.assertEqual(wp.action, "")
        self.assertEqual(route.set_action(wp.index, "servo_a").action, "servo_a")
        self.assertEqual(route.set_action(wp.index, "servo_b").action, "servo_b")
        self.assertEqual(route.set_action(wp.index, "").action, "")

    def test_invalid_waypoint_action_is_rejected(self):
        route = WP.WaypointRoute([1])
        with self.assertRaises(ValueError):
            route.add(13.0, 100.0, "drop_everything")


class TestWaypointWaitModel(unittest.TestCase):
    """WAIT ราย Waypoint — metadata แยกจาก action A/B (spec T1)"""

    def _route(self, n=3):
        route = WP.WaypointRoute([1])
        for i in range(n):
            route.add(14.0 + 0.01 * i, 100.0 + 0.01 * i)
        return route

    def test_default_wait_is_zero(self):
        route = self._route(1)
        self.assertEqual(route.points[0].wait_seconds, 0)
        self.assertEqual(route.wait_count(), 0)

    def test_set_wait_one_minute(self):
        route = self._route(1)
        wp = route.set_wait(0, 60)
        self.assertEqual(wp.wait_seconds, 60)
        self.assertEqual(route.wait_count(), 1)

    def test_set_wait_ten_minutes(self):
        route = self._route(1)
        wp = route.set_wait(0, 600)
        self.assertEqual(wp.wait_seconds, 600)

    def test_set_wait_over_ten_minutes_rejected(self):
        route = self._route(1)
        with self.assertRaises(ValueError):
            route.set_wait(0, 601)

    def test_set_wait_below_one_minute_rejected(self):
        route = self._route(1)
        with self.assertRaises(ValueError):
            route.set_wait(0, 59)

    def test_set_wait_via_minutes_helper(self):
        route = self._route(1)
        wp = route.set_wait_minutes(0, 3)
        self.assertEqual(wp.wait_seconds, 180)
        self.assertEqual(wp.wait_minutes, 3)

    def test_set_wait_minutes_out_of_range_rejected(self):
        route = self._route(1)
        with self.assertRaises(ValueError):
            route.set_wait_minutes(0, 11)
        with self.assertRaises(ValueError):
            route.set_wait_minutes(0, 0)   # 0 นาที ใช้ clear แทน

    def test_route_allows_five_wait_points(self):
        route = self._route(5)
        for i in range(5):
            route.set_wait(i, 60)
        self.assertEqual(route.wait_count(), 5)

    def test_sixth_wait_point_rejected(self):
        route = self._route(6)
        for i in range(5):
            route.set_wait(i, 60)
        with self.assertRaises(WP.WaitLimitError):
            route.set_wait(5, 60)
        # WaitLimitError ต้องเป็น ValueError ด้วย (UI จับ ValueError กว้าง ๆ ได้)
        self.assertTrue(issubclass(WP.WaitLimitError, ValueError))

    def test_edit_existing_wait_when_full(self):
        route = self._route(5)
        for i in range(5):
            route.set_wait(i, 60)
        # แก้จุดเดิมได้แม้ครบ 5 จุดแล้ว
        wp = route.set_wait(2, 300)
        self.assertEqual(wp.wait_seconds, 300)
        self.assertEqual(route.wait_count(), 5)

    def test_clear_wait_then_add_new(self):
        route = self._route(6)
        for i in range(5):
            route.set_wait(i, 60)
        route.clear_wait(0)
        self.assertEqual(route.wait_count(), 4)
        # ตอนนี้เพิ่มจุดใหม่ได้แล้ว
        route.set_wait(5, 120)
        self.assertEqual(route.wait_count(), 5)

    def test_clear_wait_is_idempotent(self):
        route = self._route(1)
        route.clear_wait(0)          # ยังไม่มี wait — ต้องไม่ error
        self.assertEqual(route.points[0].wait_seconds, 0)

    def test_set_wait_zero_clears(self):
        route = self._route(1)
        route.set_wait(0, 120)
        route.set_wait(0, 0)         # 0 = ล้าง
        self.assertEqual(route.wait_count(), 0)

    def test_wait_count_reflects_route_not_counter(self):
        """Undo/Clear แล้วจำนวน WAIT ต้องคำนวณจาก route จริง"""
        route = self._route(3)
        route.set_wait(0, 60)
        route.set_wait(1, 60)
        route.set_wait(2, 60)
        self.assertEqual(route.wait_count(), 3)
        route.remove_last()          # ลบจุดที่ 2 (มี wait)
        self.assertEqual(route.wait_count(), 2)
        route.clear()
        self.assertEqual(route.wait_count(), 0)

    def test_set_wait_unknown_index_raises(self):
        route = self._route(1)
        with self.assertRaises(IndexError):
            route.set_wait(99, 60)

    def test_wait_and_action_coexist(self):
        """จุดเดียวมีทั้ง WAIT และ action A/B ได้ — ไม่กระทบกัน"""
        route = self._route(1)
        route.set_action(0, "servo_a")
        route.set_wait(0, 180)
        wp = route.points[0]
        self.assertEqual(wp.action, "servo_a")
        self.assertEqual(wp.wait_seconds, 180)
        # แก้ WAIT ไม่ล้าง action, แก้ action ไม่ล้าง WAIT
        route.set_wait(0, 240)
        self.assertEqual(wp.action, "servo_a")
        route.set_action(0, "servo_b")
        self.assertEqual(wp.wait_seconds, 240)

    def test_wait_survives_deepcopy_snapshot(self):
        import copy
        route = self._route(3)
        route.set_wait(1, 120)
        route.set_action(1, "servo_b")
        snap = copy.deepcopy(route)
        self.assertEqual(snap.points[1].wait_seconds, 120)
        self.assertEqual(snap.points[1].action, "servo_b")
        # snapshot แยกจากต้นฉบับ — แก้ต้นฉบับไม่กระทบ snapshot
        route.set_wait(1, 300)
        self.assertEqual(snap.points[1].wait_seconds, 120)

    def test_wait_summary_pairs(self):
        route = self._route(6)
        route.set_wait(1, 180)
        route.set_wait(4, 60)
        # (ตำแหน่งจุด 1-based, นาที) เรียงตามลำดับจุด
        self.assertEqual(route.wait_summary(), [(2, 3), (5, 1)])

    def test_add_with_wait_seconds(self):
        route = WP.WaypointRoute([1])
        wp = route.add(14.0, 100.0, wait_seconds=120)
        self.assertEqual(wp.wait_seconds, 120)
        wp2 = route.add(14.1, 100.1, action="servo_a", wait_seconds=60)
        self.assertEqual((wp2.action, wp2.wait_seconds), ("servo_a", 60))

    def test_add_with_invalid_wait_seconds_rejected(self):
        route = WP.WaypointRoute([1])
        with self.assertRaises(ValueError):
            route.add(14.0, 100.0, wait_seconds=30)


class TestWaypointSwarmTargets(unittest.TestCase):
    def test_swarm_targets_keep_formation(self):
        """ระยะห่างสัมพัทธ์ระหว่างลำต้องคงเดิมเป๊ะเมื่อบินไป waypoint"""
        positions = {
            1: (14.0, 100.0000),
            2: (14.0, 100.0002),
            3: (14.0, 100.0004),
        }
        out = WP.waypoint_swarm_targets(positions, 15.0, 101.0)
        d12 = out[2][1] - out[1][1]
        d23 = out[3][1] - out[2][1]
        self.assertAlmostEqual(d12, 0.0002, places=9)
        self.assertAlmostEqual(d23, 0.0002, places=9)

    def test_swarm_targets_single_drone_goes_exact(self):
        out = WP.waypoint_swarm_targets({7: (14.0, 100.0)}, 15.0, 101.0)
        self.assertEqual(out[7], (15.0, 101.0))

    def test_swarm_targets_centroid_lands_on_waypoint(self):
        positions = {1: (14.0, 100.0), 2: (14.0, 100.001), 3: (14.0, 100.002)}
        out = WP.waypoint_swarm_targets(positions, 20.0, 105.0)
        clat = sum(p[0] for p in out.values()) / len(out)
        clon = sum(p[1] for p in out.values()) / len(out)
        self.assertAlmostEqual(clat, 20.0, places=6)
        self.assertAlmostEqual(clon, 105.0, places=6)

    def test_swarm_targets_no_keep_formation_stacks(self):
        positions = {1: (14.0, 100.0), 2: (14.0, 100.001)}
        out = WP.waypoint_swarm_targets(positions, 15.0, 101.0, keep_formation=False)
        self.assertEqual(out[1], out[2])

    def test_swarm_targets_empty(self):
        self.assertEqual(WP.waypoint_swarm_targets({}, 1.0, 2.0), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
