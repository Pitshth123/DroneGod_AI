"""Characterization tests for the extracted summary presentation controller."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.controllers.summary_presenter import SummaryPresenter  # noqa: E402
from swarmgod_gui.core.swarm_logic import CommandSummary  # noqa: E402


class _SummaryBox:
    def __init__(self):
        self.rows = None
        self.run = None

    def render_rows(self, rows):
        self.rows = list(rows)

    def render_timeline(self, run):
        self.run = run


class TestSummaryPresenter(unittest.TestCase):
    def setUp(self):
        self.plan = CommandSummary()
        self.render_requests = []
        self.logs = []
        self.render_metrics = []
        self.box = _SummaryBox()
        self.presenter = SummaryPresenter(
            self.plan,
            request_render=lambda: self.render_requests.append(True),
            log_command=lambda message, severity: self.logs.append((message, severity)),
            summary_box=lambda: self.box,
            record_render=lambda: self.render_metrics.append(True),
        )

    def test_legacy_keys_map_to_stable_plan_rows(self):
        self.assertTrue(self.presenter.set("sel", "SELECTED", "D1, D2"))
        self.assertTrue(self.presenter.set("takeoff_plan", "OLD", "ALL · 20m"))
        self.assertEqual(self.plan.rows(), [
            ("TARGET", "D1, D2"), ("TAKEOFF", "ALL · 20m"),
        ])
        self.assertEqual(len(self.render_requests), 2)

    def test_transient_row_is_ignored_and_event_goes_to_command_log(self):
        self.assertFalse(self.presenter.set("move", "MOVE ORDER", "D1: ↑"))
        self.presenter.event("ยกเลิก", "Waypoint EXECUTE", cancelled=True)
        self.assertEqual(self.plan.rows(), [])
        self.assertEqual(self.logs, [("ยกเลิก: Waypoint EXECUTE", "WARNING")])

    def test_suppressed_render_supports_existing_batch_wrapper(self):
        self.presenter.set("wp_route", "WAYPOINT", "5 จุด", render=False)
        self.presenter.remove("wp_route", render=False)
        self.assertEqual(self.render_requests, [])

    def test_render_forwards_plan_and_run_and_records_metric(self):
        self.presenter.set("head", "HEAD", "D1", render=False)
        run = object()
        self.presenter.render(run)
        self.assertEqual(self.box.rows, [("HEAD", "D1")])
        self.assertIs(self.box.run, run)
        self.assertEqual(self.render_metrics, [True])

    def test_snapshot_uses_visible_canonical_labels(self):
        self.presenter.set("wp_wait", "WAIT", "#2=3m", render=False)
        self.assertEqual(self.presenter.snapshot(), {"WAIT": "#2=3m"})


if __name__ == "__main__":
    unittest.main()
