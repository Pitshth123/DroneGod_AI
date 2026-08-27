"""Pure presentation helpers for map drone markers.

This adapter formats read-only telemetry snapshots.  It does not own map
readiness, JavaScript dispatch, navigation targets, routes, or flight state.
"""


class MapPresenter:
    """Build the existing Map 3D drone-marker presentation payload."""

    @staticmethod
    def drone_markers(telemetry_by_id, drone_names, group_of,
                      color_for, mode_name_for):
        markers = []
        for drone_id, telemetry in sorted(telemetry_by_id.items()):
            position = telemetry.position
            if not position.lat and not position.lon:
                continue
            numeric_id = int(drone_id)
            markers.append({
                "id": numeric_id,
                "lat": position.lat,
                "lon": position.lon,
                "name": (
                    drone_names.get(numeric_id)
                    or getattr(telemetry, "name", "")
                    or "โดรน %s" % drone_id
                ),
                "alt": getattr(position, "alt_rel", 0.0),
                "alt_abs": getattr(position, "alt_abs", 0.0),
                "hdg": getattr(telemetry, "heading", 0.0),
                "armed": bool(getattr(telemetry, "armed", False)),
                "group": int(group_of.get(numeric_id, 0)),
                "color": color_for(numeric_id),
                "mode": mode_name_for(getattr(telemetry, "mode", 0)).replace(
                    "FLIGHT_MODE_", ""
                ),
            })
        return markers
