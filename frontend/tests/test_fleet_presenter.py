"""Characterization tests for the presentation-only FleetPresenter boundary.

These tests intentionally exercise the current GroundStation compatibility
surface first.  They lock visible output and verify that presentation refreshes
do not emit flight commands before the implementation is extracted.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from tests.test_ui_selection import Base, _fake_telem  # noqa: E402
from swarmgod_gui.controllers.fleet_presenter import FleetPresenter  # noqa: E402


class TestFleetPresenterPure(unittest.TestCase):
    def setUp(self):
        self.presenter = FleetPresenter()

    def test_name_formatting_is_deterministic(self):
        self.assertEqual(
            self.presenter.normalize_display_name("  Survey   Team  "),
            "Survey Team",
        )
        self.assertEqual(
            self.presenter.display_name(4, "", "Telemetry Name"),
            "Telemetry Name",
        )
        self.assertEqual(self.presenter.display_name(4, "", ""), "Drone 4")

    def test_selection_summary_is_sorted_and_does_not_mutate_source(self):
        selected = {3, 1}
        self.assertEqual(self.presenter.selection_summary(selected),
                         "2 ลำ · D1, D3")
        self.assertEqual(selected, {1, 3})

    def test_group_chip_view_matches_existing_cockpit_contract(self):
        populated = self.presenter.group_chip(2, 3)
        self.assertEqual(populated.text, "2·3")
        self.assertIn("Group 2 · 3 ลำ", populated.tooltip)
        self.assertTrue(populated.enabled)

        empty = self.presenter.group_chip(6, 0)
        self.assertEqual(empty.text, "6")
        self.assertFalse(empty.enabled)

    def test_fleet_count_text_matches_existing_label(self):
        self.assertEqual(self.presenter.fleet_count_text(5), "fleet 5")


class TestFleetPresentationCharacterization(Base):
    def test_drone_name_priority_and_fallback(self):
        item = self.win.fleet_items[2]
        item.set_display_name("Card Name")
        self.win._last_telem[2] = SimpleNamespace(name="Telemetry Name")
        self.assertEqual(self.win._drone_name(2), "Card Name")

        item.name = ""
        self.assertEqual(self.win._drone_name(2), "Telemetry Name")

        self.win._last_telem.pop(2)
        self.assertEqual(self.win._drone_name(2), "Drone 2")

    def test_rename_normalizes_whitespace_and_limits_visible_name(self):
        raw = "   ทีม    สำรวจ   " + ("X" * 40)
        self.win._on_drone_renamed(2, raw)
        shown = self.win._drone_names[2]
        self.assertEqual(shown, "ทีม สำรวจ " + ("X" * 22))
        self.assertEqual(len(shown), 32)
        self.assertEqual(self.win.fleet_items[2].lbl_name.text(), shown)

    def test_selection_refresh_formats_sorted_summary_without_command(self):
        self.win._selected_ids = {3, 1}
        before = list(self.fake.calls)
        self.win._refresh_selection_ui()

        self.assertTrue(self.win.fleet_items[1]._selected)
        self.assertFalse(self.win.fleet_items[2]._selected)
        self.assertTrue(self.win.fleet_items[3]._selected)
        self.assertEqual(dict(self.win._cmd_summary.rows())["TARGET"],
                         "2 ลำ · D1, D3")
        self.assertEqual(self.fake.calls, before)

    def test_selection_refresh_keeps_waypoint_render_in_ground_station(self):
        self.win._selected_ids = {2}
        self.win._wp_separate = True
        with mock.patch.object(self.win, "_wp_render_points_label") as render_waypoint:
            self.win._refresh_selection_ui()
        render_waypoint.assert_called_once_with()

    def test_group_refresh_formats_chip_badge_and_sends_no_command(self):
        self.win.group_of.clear()
        self.win.group_of.update({1: 2, 3: 2})
        before = list(self.fake.calls)
        self.win._refresh_group_ui()

        chip = self.win.group_chips[2]
        self.assertEqual(chip.text(), "2·2")
        self.assertIn("Group 2 · 2 ลำ", chip.toolTip())
        self.assertTrue(chip.isEnabled())
        self.assertEqual(self.win.fleet_items[1].lbl_group.text(), "2")
        self.assertEqual(self.win.fleet_items[3].lbl_group.text(), "2")
        self.assertEqual(self.fake.calls, before)

    def test_first_telemetry_refreshes_fleet_count_label(self):
        self.win._on_telemetry(_fake_telem(7, 14.0, 100.0))
        self.assertEqual(self.win.lbl_fcount.text(), "fleet 6")


if __name__ == "__main__":
    unittest.main()
