"""V1 Phase 3 app integration — store is presentation-only and legacy flight state stays intact."""
import inspect
import json
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

from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.core.telemetry_store import (  # noqa: E402
    TelemetryRenderGate,
    TelemetryView,
)
from tests.test_ui_selection import FakeClient, _fake_telem  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)


class TelemetryStoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        for did in list(self.win.fleet_items):
            self.win._remove_fleet_item(did)
        self.win.client = FakeClient()
        self.win.show()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_shadow_write_keeps_raw_legacy_message_and_immutable_view(self):
        t = _fake_telem(1, 14.0, 100.0, alt_rel=20.0)
        self.win._on_telemetry(t)

        view = self.win._telemetry_store.get(1)
        self.assertIsInstance(view, TelemetryView)
        self.assertIs(self.win._last_telem[1], t)
        self.assertEqual(view.position.alt_rel, 20.0)
        self.assertEqual(self.win._telemetry_shadow_mismatch_count, 0)

        t.position.alt_rel = 99.0
        self.assertEqual(view.position.alt_rel, 20.0)
        self.assertEqual(self.win._last_telem[1].position.alt_rel, 99.0)

    def test_select_drone_presentation_prefers_frozen_store_over_mutated_raw_message(self):
        t = _fake_telem(1, 14.0, 100.0, alt_rel=21.0)
        t.battery_pct = 88.0
        self.win._on_telemetry(t)

        # Simulate a mutable legacy object changing outside the read model.  A
        # presentation refresh must still use the frozen snapshot for that ingest.
        t.battery_pct = 1.0
        t.position.alt_rel = 2.0
        self.win._select_drone(1)
        self.assertIn("88%", self.win.sel_card._vals["BATTERY"].text())
        self.assertEqual(self.win.sel_card._vals["ALTITUDE"].text(), "21 m")

    def test_field_tablet_presentation_receives_store_view_not_raw_proto(self):
        t = _fake_telem(2, 14.1, 100.2, alt_rel=10.0)
        self.win.field = object()
        try:
            with mock.patch.object(self.win, "_field_push") as push:
                self.win._on_telemetry(t)
            pushed = push.call_args.args[0]
            self.assertIsInstance(pushed, TelemetryView)
            self.assertEqual(pushed.drone_id, 2)
            self.assertEqual(pushed.position.alt_rel, 10.0)
        finally:
            self.win.field = None

    def test_continuous_widget_burst_is_coalesced_but_store_and_raw_state_keep_latest(self):
        clk = FakeClock()
        self.win._telemetry_render_gate = TelemetryRenderGate(clock=clk, min_interval_s=1.0)

        first = _fake_telem(3, 14.0, 100.3, alt_rel=10.0)
        first.heading = 10.0
        self.win._on_telemetry(first)
        item = self.win.fleet_items[3]

        original_update = item.update_from_telemetry
        item.update_from_telemetry = mock.Mock(wraps=original_update)

        clk.advance(0.01)
        second = _fake_telem(3, 14.0, 100.31, alt_rel=11.0)
        second.heading = 11.0
        self.win._on_telemetry(second)
        self.assertEqual(item.update_from_telemetry.call_count, 0)
        self.assertIs(self.win._last_telem[3], second)
        self.assertEqual(self.win._telemetry_store.get(3).position.alt_rel, 11.0)

        # Battery is discrete/operator-visible and must repaint immediately even
        # inside the continuous-motion coalescing interval.
        clk.advance(0.01)
        third = _fake_telem(3, 14.0, 100.32, alt_rel=12.0)
        third.heading = 12.0
        third.battery_pct = 50.0
        self.win._on_telemetry(third)
        self.assertEqual(item.update_from_telemetry.call_count, 1)
        self.assertEqual(self.win._telemetry_store.get(3).battery_pct, 50.0)

    def test_disconnect_forgets_read_model_and_health_age(self):
        self.win._on_telemetry(_fake_telem(4, 14.0, 100.4, alt_rel=5.0))
        self.assertIsNotNone(self.win._telemetry_store.get(4))
        self.assertIsNotNone(self.win._health.telemetry_age_ms(4))

        self.win._mark_disconnected(4)
        self.assertIsNone(self.win._telemetry_store.get(4))
        self.assertIsNone(self.win._health.telemetry_age_ms(4))

    def test_online_count_repaint_is_cached_when_value_does_not_change(self):
        self.win._on_telemetry(_fake_telem(5, 14.0, 100.5, alt_rel=5.0))
        with mock.patch.object(self.win.lbl_count, "setText", wraps=self.win.lbl_count.setText) as set_text, \
                mock.patch.object(self.win.lbl_count, "setStyleSheet", wraps=self.win.lbl_count.setStyleSheet) as set_style:
            self.win._refresh_online_count()
            self.assertEqual(set_text.call_count, 0)
            self.assertEqual(set_style.call_count, 0)

    def test_flight_business_read_paths_remain_on_legacy_raw_telemetry(self):
        # Phase 3 is a frontend read-model migration, not a flight-authority
        # migration. These methods influence navigation/safety behavior and must
        # not start depending on the presentation store in this phase.
        for name in (
            "_fleet_positions",
            "_collision_positions",
            "_wp_advance",
            "_wp_advance_one",
            "_auto_guided_after_land",
            "_sync_servo_from_telemetry",
        ):
            source = inspect.getsource(getattr(GroundStation, name))
            self.assertNotIn("_telemetry_store", source, name)


if __name__ == "__main__":
    unittest.main()
