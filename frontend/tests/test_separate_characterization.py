"""V3-S09-B characterization — legacy SEPARATE waypoint behavior.

CHARACTERIZATION / COMPATIBILITY CONTRACT (headless, no Qt/gRPC).  Pins what the
existing Python executor does for a SEPARATE waypoint mission so the Go Core model
(backend/internal/mission/separate_multi.go) can be proven equivalent before any
authority cutover.

Characterized contract (see app.py `_wp_advance_one` / `_on_target_reached`
SEPARATE branch, and waypoint_logic.check_route_conflicts):

  1. Each participant has its OWN route and its OWN index; drones advance
     INDEPENDENTLY (one finishing does not block the others).
  2. A SEPARATE drone flies to its route's RAW waypoint — there is NO group
     formation offset (that is GROUPED-only).  Arrival is judged per drone against
     that raw waypoint.
  3. Per-drone altitude (`_last_alt`/`_alt_for`).
  4. Because routes are independent they can cross, so before executing SEPARATE the
     UI runs a route-conflict preflight (`check_route_conflicts`): a pair is a
     conflict only when BOTH (a) their altitude gap <= alt_sep_m AND (b) their paths
     come within min_dist_m.  Altitude-separated routes never conflict even if they
     overlap on the map.  This gating stays in the Python layer; Core does not
     re-implement it.

This test exercises the ACTUAL legacy method check_route_conflicts.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import waypoint_logic as WPL  # noqa: E402


class TestSeparateRouteConflict(unittest.TestCase):
    """Contract 4 — the SEPARATE route-conflict preflight."""

    def test_crossing_routes_same_altitude_conflict(self):
        routes = {
            1: [(14.000, 100.000), (14.000, 100.002)],  # west->east
            2: [(14.001, 100.001), (13.999, 100.001)],  # north->south, crosses D1
        }
        alts = {1: 20.0, 2: 20.5}  # same layer (gap 0.5 <= 2.0)
        conflicts = WPL.check_route_conflicts(routes, alts)
        self.assertTrue(conflicts, "crossing routes at the same altitude must conflict")
        self.assertEqual(conflicts[0].key(), (1, 2))

    def test_altitude_separated_routes_do_not_conflict(self):
        routes = {
            1: [(14.000, 100.000), (14.000, 100.002)],
            2: [(14.001, 100.001), (13.999, 100.001)],
        }
        alts = {1: 20.0, 2: 40.0}  # gap 20 > alt_sep_m -> different layer
        self.assertEqual(WPL.check_route_conflicts(routes, alts), [],
                         "altitude-separated routes must not conflict")

    def test_far_apart_routes_do_not_conflict(self):
        routes = {
            1: [(14.000, 100.000), (14.000, 100.001)],
            2: [(14.900, 100.900), (14.900, 100.901)],  # far away
        }
        alts = {1: 20.0, 2: 20.0}
        self.assertEqual(WPL.check_route_conflicts(routes, alts), [],
                         "well-separated routes must not conflict")

    def test_start_positions_extend_the_checked_path(self):
        # A drone starting under another's route can conflict on the leg to WP0.
        routes = {
            1: [(14.000, 100.002)],
            2: [(14.000, 100.002)],
        }
        starts = {1: (14.000, 100.000), 2: (13.9999, 100.000)}
        alts = {1: 20.0, 2: 20.0}
        conflicts = WPL.check_route_conflicts(routes, alts, start_positions=starts)
        self.assertTrue(conflicts, "overlapping approach legs must be detected")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
