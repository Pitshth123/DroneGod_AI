#!/usr/bin/env python3
"""Read one ArduPilot parameter directly from a SITL MAVLink TCP endpoint."""
import argparse
import sys
import time

from pymavlink import mavutil


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("param")
    ap.add_argument("--endpoint", default="tcp:127.0.0.1:5760")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    master = mavutil.mavlink_connection(args.endpoint, source_system=255)
    master.wait_heartbeat(timeout=args.timeout)
    wanted = args.param.upper()
    master.param_fetch_one(wanted)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=1.0)
        if msg is None:
            continue
        raw = msg.param_id
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="ignore")
        name = str(raw).rstrip("\x00").upper()
        if name == wanted:
            print(f"{name}={msg.param_value}")
            return 0
    print(f"timeout reading {wanted}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
