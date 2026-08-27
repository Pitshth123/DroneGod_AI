"""headless test: Python cockpit client → Go core → SITL (Phase 2 data path)"""
import sys
import time

from swarmgod_gui.core.grpc_client import CoreClient
from swarmgod_gui.core import rpc

core_addr = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:50051"
sitl = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1:5760"
host, _, port = sitl.partition(":")

c = CoreClient(core_addr)
res = c.connect_drone(1, "UAV_1", host, int(port))
print(f"Connect -> ok={res.ok} msg={res.message!r}", flush=True)

count = 0
last = 0.0
for t in c.stub.SubscribeTelemetry(rpc.telemetry_pb2.SubscribeTelemetryRequest()):
    if time.time() - last >= 1.0:
        last = time.time()
        print(f"UAV_{t.drone_id} {rpc.status_name(t.status):<11} "
              f"{rpc.mode_name(t.mode):<9} bat={t.battery_pct:3.0f}% "
              f"{t.voltage:.2f}V sat={t.sat_count} fix={t.gps_fix} "
              f"pos={t.position.lat:.6f},{t.position.lon:.6f}", flush=True)
        count += 1
        if count >= 5:
            break
print("HEADLESS TEST OK", flush=True)
