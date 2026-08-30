"""V3-S09-A Legacy GROUPED characterization using production helpers directly."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import swarm_logic as SW
from swarmgod_gui.core import waypoint_logic as WPL

TGT_REACH_M = 3.0
POS = {
    1: (14.0000, 100.0000),
    2: (14.0000, 100.0010),
    3: (14.0010, 100.0005),
}
WP = (14.0050, 100.0050)
EXPECTED_TARGETS = {
    1: (14.0046666667, 100.0045),
    2: (14.0046666667, 100.0055),
    3: (14.0056666667, 100.0050),
}


class TestGroupedProductionDispatchPlan(unittest.TestCase):
    def test_formation_targets_and_front_of_travel_order(self):
        targets, order = WPL.grouped_dispatch_plan([1, 2, 3], POS, *WP)
        self.assertEqual(order, [3, 1, 2])
        for drone_id, expected in EXPECTED_TARGETS.items():
            self.assertAlmostEqual(targets[drone_id][0], expected[0], places=9)
            self.assertAlmostEqual(targets[drone_id][1], expected[1], places=9)

    def test_centroid_and_pairwise_shape_are_preserved(self):
        targets, _ = WPL.grouped_dispatch_plan([1, 2, 3], POS, *WP)
        self.assertAlmostEqual(
            sum(x[0] for x in targets.values()) / len(targets), WP[0], places=9)
        self.assertAlmostEqual(
            sum(x[1] for x in targets.values()) / len(targets), WP[1], places=9)
        for a, b in ((1, 2), (1, 3), (2, 3)):
            self.assertAlmostEqual(
                POS[a][0] - POS[b][0], targets[a][0] - targets[b][0], places=9)
            self.assertAlmostEqual(
                POS[a][1] - POS[b][1], targets[a][1] - targets[b][1], places=9)

    def test_missing_position_uses_raw_waypoint_and_appends_last(self):
        targets, order = WPL.grouped_dispatch_plan(
            [1, 2, 3], {1: POS[1], 2: POS[2]}, *WP)
        self.assertEqual(targets[3], WP)
        self.assertEqual(order[-1], 3)
        self.assertNotEqual(targets[1], WP)
        self.assertNotEqual(targets[2], WP)

    def test_eastbound_sends_front_drone_first(self):
        pos = {1: (14.0, 100.0), 2: (14.0, 100.001), 3: (14.0, 100.002)}
        self.assertEqual(
            WPL.grouped_goto_order([1, 2, 3], pos, 14.0, 100.01),
            [3, 2, 1])

    def test_single_participant_degenerates_to_raw_waypoint(self):
        targets, order = WPL.grouped_dispatch_plan(
            [7], {7: (14.0, 100.0)}, *WP)
        self.assertEqual(targets[7], WP)
        self.assertEqual(order, [7])


class TestGroupedProductionArrivalBarrier(unittest.TestCase):
    def test_early_arrival_stalls_until_every_participant(self):
        arrived, complete, accepted = WPL.grouped_arrival_update(set(), [1, 2, 3], 1)
        self.assertTrue(accepted)
        self.assertFalse(complete)
        arrived, complete, _ = WPL.grouped_arrival_update(arrived, [1, 2, 3], 2)
        self.assertFalse(complete)
        arrived, complete, _ = WPL.grouped_arrival_update(arrived, [1, 2, 3], 3)
        self.assertTrue(complete)

    def test_partial_command_rejection_never_marks_arrival(self):
        # Legacy command rejection has no target_reached event. Therefore the
        # production barrier helper is called only for D1/D3 and stalls on D2.
        arrived, complete, _ = WPL.grouped_arrival_update(set(), [1, 2, 3], 1)
        arrived, complete, _ = WPL.grouped_arrival_update(arrived, [1, 2, 3], 3)
        self.assertEqual(arrived, {1, 3})
        self.assertFalse(complete)

    def test_unknown_browser_arrival_is_ignored(self):
        arrived, complete, accepted = WPL.grouped_arrival_update({1}, [1, 2], 99)
        self.assertEqual(arrived, {1})
        self.assertFalse(complete)
        self.assertFalse(accepted)

    def test_raw_waypoint_is_not_an_offset_arrival(self):
        targets, _ = WPL.grouped_dispatch_plan([1, 2, 3], POS, *WP)
        for drone_id, target in targets.items():
            self.assertGreater(
                SW.haversine_m(WP[0], WP[1], target[0], target[1]),
                TGT_REACH_M, f"D{drone_id} raw waypoint must not count")


class TestGroupedProductionDispatchTimingGuard(unittest.TestCase):
    def test_150ms_sequence_is_index_times_constant(self):
        _, order = WPL.grouped_dispatch_plan([1, 2, 3], POS, *WP)
        self.assertEqual([i * 150 for i, _ in enumerate(order)], [0, 150, 300])

    def test_stale_wave_generation_is_suppressed(self):
        self.assertFalse(WPL.grouped_dispatch_generation_valid(True, True, 7, 8))
        self.assertTrue(WPL.grouped_dispatch_generation_valid(True, True, 8, 8))
        self.assertTrue(WPL.grouped_dispatch_generation_valid(True, False, 7, 8))
        self.assertFalse(WPL.grouped_dispatch_generation_valid(False, False, 8, 8))


if __name__ == "__main__":
    unittest.main()
