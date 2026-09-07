"""Read a fresh, disarmed aircraft position from the setup Core for HIL restart."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
if FRONTEND not in sys.path:
    sys.path.insert(0, FRONTEND)

from swarmgod_gui.core.grpc_client import CoreClient, format_rpc_error  # noqa: E402
from swarmgod_gui.core.rpc import service_pb2  # noqa: E402


def main() -> int:
    client = CoreClient()
    try:
        try:
            snap = client.stub.GetFleetSnapshot(
                service_pb2.FleetSnapshotRequest(), timeout=5)
        except Exception as exc:
            print(
                f"Setup Core telemetry is unavailable: {format_rpc_error(exc)}",
                file=sys.stderr,
            )
            return 3
    finally:
        client.close()

    now_ms = int(time.time() * 1000)
    usable = []
    for drone in snap.drones:
        age_ms = max(0, now_ms - int(drone.timestamp_ms or 0))
        if (
            drone.telemetry_verified
            and not drone.armed
            and int(drone.gps_fix) >= 3
            and drone.position.lat != 0.0
            and drone.position.lon != 0.0
            and age_ms <= 5000
        ):
            usable.append(drone)
    if not usable:
        print(
            "No fresh, GPS-verified, disarmed drone is available to establish HIL HOME.",
            file=sys.stderr,
        )
        return 2
    drone = max(usable, key=lambda item: int(item.timestamp_ms or 0))
    print(f"{drone.position.lat:.7f},{drone.position.lon:.7f},0,0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
