"""Headless regression tests for PRE-FLIGHT SUMMARY V2."""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication  # noqa: E402
from swarmgod_gui.core.flight_progress import (  # noqa: E402
    StepStatus, build_takeoff_flow, build_swarm_takeoff_flow,
    build_waypoint_flow, build_wave_flow,
)
from swarmgod_gui.core.swarm_logic import CommandSummary  # noqa: E402
from swarmgod_gui.widgets.command_summary import CommandSummaryBox  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestPlanRows(unittest.TestCase):
    def test_key_update_does_not_duplicate_and_has_stable_order(self):
        plan = CommandSummary()
        plan.set("wave", "WAVE", "G1 → G2")
        plan.set("target", "TARGET", "D1, D2")
        plan.set("head", "HEAD", "D1")
        plan.set("target", "TARGET", "D1, D2, D3")
        self.assertEqual(plan.rows(), [
            ("TARGET", "D1, D2, D3"), ("HEAD", "D1"), ("WAVE", "G1 → G2"),
        ])


class TestWaitPlanRow(unittest.TestCase):
    """WAIT row ใน Pre-flight Summary (spec §11 / T8)"""

    def test_wait_row_ordered_after_ab_before_wave(self):
        # ใช้ canonical key ตามที่ _summ_set map ให้ (payload/wait/wave)
        plan = CommandSummary()
        plan.set("waypoint", "WAYPOINT", "5 จุด")
        plan.set("payload", "PAYLOAD A/B", "#1=A")
        plan.set("wait", "WAIT", "#2=3m · #5=1m")
        plan.set("wave", "WAVE", "G1 → G2")
        labels = [label for label, _ in plan.rows()]
        self.assertLess(labels.index("PAYLOAD A/B"), labels.index("WAIT"))
        self.assertLess(labels.index("WAIT"), labels.index("WAVE"))

    def test_wait_frozen_into_run_snapshot(self):
        source = {"WAYPOINT A/B": "#1=A", "WAIT": "#2=3m"}
        run = build_waypoint_flow(source, has_actions=True)
        self.assertEqual(run.plan_snapshot["WAIT"], "#2=3m")
        # แก้ live plan ทีหลังต้องไม่แตะ snapshot ของ run ที่ freeze ไปแล้ว
        source["WAIT"] = "#2=9m"
        self.assertEqual(run.plan_snapshot["WAIT"], "#2=3m")


class TestProgressModel(unittest.TestCase):
    def test_takeoff_orders_and_one_active_step(self):
        run = build_takeoff_flow({"TARGET": "D1,D2"}, "all")
        self.assertEqual([step.id for step in run.steps], [
            "plan_confirmed", "preflight_gate", "send_takeoff", "wait_airborne", "target_alt", "flight_ready",
        ])
        run.active("send_takeoff")
        run.active("wait_airborne", "Airborne 1/2")
        self.assertEqual(run.step("send_takeoff").status, StepStatus.PENDING)
        self.assertEqual(sum(s.status == StepStatus.ACTIVE for s in run.steps), 1)

    def test_sequential_uses_single_progress_step(self):
        run = build_takeoff_flow({}, "sequential")
        self.assertEqual(run.step("send_takeoff").title, "SEQUENTIAL TAKEOFF")
        run.active("send_takeoff", "D2/5 · D1 done")
        self.assertIn("D2/5", run.step("send_takeoff").detail)

    def test_swarm_wait_failure_skips_formup_and_active(self):
        run = build_swarm_takeoff_flow({}, "all")
        run.active("wait_airborne")
        run.fail("wait_airborne", "Head D1 not airborne")
        self.assertEqual(run.step("wait_airborne").status, StepStatus.FAILED)
        self.assertEqual(run.step("form_up").status, StepStatus.SKIPPED)
        self.assertEqual(run.step("swarm_active").status, StepStatus.SKIPPED)

    def test_waypoint_auto_takeoff_can_be_skipped(self):
        run = build_waypoint_flow({}, auto_takeoff=False)
        self.assertEqual(run.step("auto_takeoff").status, StepStatus.SKIPPED)
        self.assertEqual(run.step("wait_airborne").status, StepStatus.SKIPPED)
        run.active("fly_waypoint", "WP 2/5 · D1,D2")
        self.assertEqual(run.step("fly_waypoint").detail, "WP 2/5 · D1,D2")

    def test_waypoint_payload_action_is_optional(self):
        no_action = build_waypoint_flow({}, has_actions=False)
        action = build_waypoint_flow({}, has_actions=True)
        self.assertNotIn("waypoint_action", [step.id for step in no_action.steps])
        self.assertIn("waypoint_action", [step.id for step in action.steps])

    def test_wave_stays_on_current_group_until_explicit_transition(self):
        run = build_wave_flow({}, [1, 2, 4])
        run.active("g1_block", "ROUTE · WP 3/5")
        self.assertEqual(run.step("g2_block").status, StepStatus.PENDING)
        run.complete("g1_block", "COMPLETE · disarmed")
        run.active("g2_block", "PREPARE")
        self.assertEqual(run.step("g2_block").status, StepStatus.ACTIVE)

    def test_cancel_stops_remaining_steps(self):
        run = build_takeoff_flow({}, "all")
        run.active("wait_airborne")
        run.cancel("E-STOP")
        self.assertEqual(run.step("wait_airborne").status, StepStatus.CANCELLED)
        self.assertEqual(run.step("target_alt").status, StepStatus.SKIPPED)

    def test_snapshot_is_frozen_and_stale_run_is_independent(self):
        snapshot = {"TARGET": ["D1", "D2"]}
        old = build_takeoff_flow(snapshot)
        snapshot["TARGET"].append("D3")
        new = build_takeoff_flow({"TARGET": ["D4"]})
        old.complete("plan_confirmed")
        self.assertEqual(old.plan_snapshot["TARGET"], ["D1", "D2"])
        self.assertEqual(new.step("plan_confirmed").status, StepStatus.PENDING)


class TestSummaryWidget(unittest.TestCase):
    def test_repeated_empty_render_keeps_placeholder_alive(self):
        widget = CommandSummaryBox()
        for _ in range(4):
            widget.render_rows([])
            _app.sendPostedEvents()
            _app.processEvents()
        self.assertEqual(widget._plan_rows.count(), 1)
        self.assertIs(widget._plan_rows.itemAt(0).widget(), widget._empty)
        self.assertTrue(widget._empty.isVisibleTo(widget))
        widget.deleteLater()

    def test_headless_widget_renders_plan_and_timeline(self):
        widget = CommandSummaryBox()
        widget.render_rows([("TARGET", "D1, D2"), ("TAKEOFF", "ALL · 20m")])
        run = build_takeoff_flow({"TARGET": "D1,D2"})
        run.active("send_takeoff", "Command accepted 1/2")
        widget.render_timeline(run)
        _app.processEvents()
        self.assertEqual(widget.lbl_count.text(), "2")
        self.assertIn("STEP 3/6", widget._flow_title.text())
        widget.deleteLater()

    def test_plan_and_active_are_separate_switchable_tabs(self):
        widget = CommandSummaryBox()
        widget.render_rows([("TARGET", "D1, D2")])
        self.assertEqual(widget._stack.currentIndex(), 0)
        self.assertFalse(widget.btn_active.isEnabled())

        run = build_takeoff_flow({"TARGET": "D1,D2"})
        run.active("send_takeoff")
        widget.render_timeline(run)
        self.assertEqual(widget._stack.currentIndex(), 1)
        self.assertTrue(widget.btn_active.isEnabled())

        widget.btn_plan.click()
        self.assertEqual(widget._stack.currentIndex(), 0)
        widget.btn_active.click()
        self.assertEqual(widget._stack.currentIndex(), 1)
        widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
