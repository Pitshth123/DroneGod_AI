"""
rpc.py — โหลด gRPC stubs ที่ generate จาก proto/ (backend/frontend ใช้ contract เดียวกัน)
ใส่ gen/ เข้า sys.path เพื่อให้ absolute import ภายใน stub (`from swarmgod.v1 import ...`) ทำงาน
"""
import os
import sys

_GEN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gen")
if _GEN not in sys.path:
    sys.path.insert(0, _GEN)

from swarmgod.v1 import common_pb2      # noqa: E402
from swarmgod.v1 import telemetry_pb2   # noqa: E402
from swarmgod.v1 import command_pb2     # noqa: E402
from swarmgod.v1 import swarm_pb2       # noqa: E402
from swarmgod.v1 import service_pb2     # noqa: E402
from swarmgod.v1 import service_pb2_grpc  # noqa: E402
from swarmgod.v1 import mission_pb2      # noqa: E402


def status_name(status_enum: int) -> str:
    """LinkStatus enum → badge string (เช่น 4 → 'READY')"""
    name = common_pb2.LinkStatus.Name(status_enum)  # 'LINK_STATUS_READY'
    return name.replace("LINK_STATUS_", "")


def mode_name(mode_enum: int) -> str:
    """FlightMode enum → string (เช่น 5 → 'GUIDED')"""
    name = common_pb2.FlightMode.Name(mode_enum)  # 'FLIGHT_MODE_GUIDED'
    return name.replace("FLIGHT_MODE_", "")
