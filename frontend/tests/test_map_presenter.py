"""Characterization tests for the render-only MapPresenter boundary.

The GroundStation compatibility method remains responsible for the 3D-ready
gate and JavaScript dispatch.  These tests lock the existing drone-marker
payload before its pure formatting is extracted.
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core.theme import drone_color  # noqa: E402
from swarmgod_gui.controllers.map_presenter import MapPresenter  # noqa: E402
from tests.test_map3d import Base  # noqa: E402


def _payload(js_call):
    return json.loads(
        js_call.split("map3d.setDrones(", 1)[1].rsplit(")", 1)[0]
    )


class TestMapPresenterPure(unittest.TestCase):
    def test_drone_markers_keep_defaults_and_do_not_mutate_snapshots(self):
        position = SimpleNamespace(lat=14.25, lon=101.75)
        telemetry = SimpleNamespace(position=position, name="", mode=7)
        telemetry_by_id = {3: telemetry}
        names = {}
        groups = {}

        markers = MapPresenter.drone_markers(
            telemetry_by_id,
            names,
            groups,
            color_for=lambda drone_id: "color-%d" % drone_id,
            mode_name_for=lambda mode: "FLIGHT_MODE_TEST_%d" % mode,
        )

        self.assertEqual(markers, [{
            "id": 3,
            "lat": 14.25,
            "lon": 101.75,
            "name": "โดรน 3",
            "alt": 0.0,
            "alt_abs": 0.0,
            "hdg": 0.0,
            "armed": False,
            "group": 0,
            "color": "color-3",
            "mode": "TEST_7",
        }])
        self.assertEqual(telemetry_by_id, {3: telemetry})
        self.assertEqual(names, {})
        self.assertEqual(groups, {})


class TestMapDronePresentationCharacterization(Base):
    def setUp(self):
        super().setUp()
        self.win._map3d_ready = True

    def test_payload_preserves_sorting_formatting_and_has_no_command_side_effect(self):
        first = self.win._last_telem[1]
        first.position.alt_rel = 12.5
        first.position.alt_abs = 112.5
        first.heading = 87.0
        first.armed = True
        first.mode = 0
        self.win._drone_names[1] = "Survey One"
        self.win.group_of[1] = 4
        self.win._last_telem = {
            2: self.win._last_telem[2],
            1: first,
        }
        before = list(self.fake.calls)

        self.win._push_map3d()

        self.assertEqual(len(self.js3d), 1)
        drones = _payload(self.js3d[0])
        self.assertEqual([drone["id"] for drone in drones], [1, 2])
        self.assertEqual(
            drones[0],
            {
                "id": 1,
                "lat": first.position.lat,
                "lon": first.position.lon,
                "name": "Survey One",
                "alt": 12.5,
                "alt_abs": 112.5,
                "hdg": 87.0,
                "armed": True,
                "group": 4,
                "color": drone_color(1),
                "mode": "UNKNOWN",
            },
        )
        self.assertEqual(self.fake.calls, before)

    def test_name_priority_is_custom_then_telemetry_then_thai_fallback(self):
        self.win._drone_names[1] = "Custom Name"
        self.win._last_telem[1].name = "Telemetry One"
        self.win._last_telem[2].name = ""
        self.win._drone_names.pop(2, None)

        self.win._push_map3d()

        drones = {drone["id"]: drone for drone in _payload(self.js3d[0])}
        self.assertEqual(drones[1]["name"], "Custom Name")
        self.assertEqual(drones[2]["name"], "โดรน 2")

    def test_no_gps_entry_is_filtered_without_extra_js_dispatch(self):
        self.win._last_telem[1].position.lat = 0.0
        self.win._last_telem[1].position.lon = 0.0

        self.win._push_map3d()

        self.assertEqual(len(self.js3d), 1)
        self.assertEqual([drone["id"] for drone in _payload(self.js3d[0])], [2])


if __name__ == "__main__":
    unittest.main()
