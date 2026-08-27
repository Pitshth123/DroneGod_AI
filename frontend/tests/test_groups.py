import os
import sqlite3
import tempfile
import unittest
import time

from swarmgod_gui.core.group_store import GroupStore


class TestGroupStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "fleet_ips.db")
        self.store = GroupStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_group_survives_restart_and_one_drone_has_one_group(self):
        self.store.set_group(3, 1)
        self.store.set_group(3, 5)
        self.assertEqual(GroupStore(self.path).get_all(), {3: 5})

    def test_zero_removes_group(self):
        self.store.set_group(2, 4)
        self.store.set_group(2, 0)
        self.assertEqual(self.store.get_all(), {})

    def test_invalid_group_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_group(1, 7)

    def test_invalid_rows_are_discarded_on_load(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "INSERT INTO drone_groups(drone_id,group_no,updated_at) VALUES(?,?,?)",
                (9, 99, 0.0),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.store.get_all(), {})


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

from PyQt5.QtWidgets import QApplication  # noqa: E402
from swarmgod_gui.app import GroundStation  # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem  # noqa: E402
from tests.test_ui_selection import FakeClient, _fake_telem  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestGroupSelection(unittest.TestCase):
    def setUp(self):
        self.win = GroundStation("127.0.0.1:59999")
        self.fake = FakeClient()
        self.win.client = self.fake
        self.win.group_of.clear()
        for did in range(1, 6):
            item = FleetItem(did, f"Drone {did}", self.win._pixmap)
            self.win._wire_fleet_item(item)
            self.win.fleet_items[did] = item
            self.win.fleet_area.addWidget(item)
            self.win._last_seen[did] = time.monotonic()
        self.win.group_of.update({1: 1, 2: 1, 3: 2, 4: 2})
        self.win._refresh_group_ui()

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()

    def test_select_group_matches_members_and_sends_no_flight_command(self):
        self.win._select_group(1)
        self.assertEqual(self.win._selected_or_all(), [1, 2])
        self.assertEqual(self.fake.calls, [])

    def test_additive_group_selection_combines_groups(self):
        self.win._select_group(1)
        self.win._select_group(2, additive=True)
        self.assertEqual(self.win._selected_or_all(), [1, 2, 3, 4])

    def test_offline_member_is_skipped(self):
        self.win._last_seen[2] = 0.0
        self.win._select_group(1)
        self.assertEqual(self.win._selected_or_all(), [1])

    def test_all_offline_keeps_previous_selection(self):
        self.win._on_fleet_click(5, False)
        self.win._last_seen[1] = self.win._last_seen[2] = 0.0
        self.win._select_group(1)
        self.assertEqual(self.win._selected_or_all(), [5])

    def test_empty_group_chip_is_disabled(self):
        self.assertFalse(self.win.group_chips[6].isEnabled())

    def test_group_chip_shows_group_and_member_count_without_clipping(self):
        self.assertEqual(self.win.group_chips[1].text(), "1·2")
        self.assertGreaterEqual(self.win.group_chips[1].width(), 40)
        self.assertIn("2 ลำ", self.win.group_chips[1].toolTip())

    def test_assign_moves_drone_to_exactly_one_group(self):
        self.win._on_fleet_click(1, False)
        self.win._assign_selected_to_group(3)
        self.assertEqual(self.win.group_of[1], 3)
        self.assertEqual(self.win.fleet_items[1].lbl_group.text(), "3")

    def test_select_group_moves_head_inside_group(self):
        self.win._head_id = 5
        self.win._select_group(2)
        self.assertIn(self.win._head_id, (3, 4))

    def test_restored_group_is_selectable_on_first_telemetry(self):
        did, group = 7, 3
        self.win.group_of[did] = group
        self.win._refresh_group_ui()
        self.assertFalse(self.win.group_chips[group].isEnabled())

        self.win._on_telemetry(_fake_telem(did, 14.0, 100.0))

        self.assertEqual(self.win.fleet_items[did].lbl_group.text(), str(group))
        self.assertTrue(self.win.group_chips[group].isEnabled())
        self.win._select_group(group)
        self.assertEqual(self.win._selected_or_all(), [did])

if __name__ == "__main__":
    unittest.main()
