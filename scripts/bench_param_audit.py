#!/usr/bin/env python3
"""F9A read-only ArduPilot parameter capture/audit helper.

Default mode audits an existing text snapshot and never opens MAVLink. Use
--endpoint only when the FC/SITL is intentionally available for read-only bench
capture. It may send PARAM_REQUEST_LIST/READ but never PARAM_SET.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path


def parse_snapshot(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            key, value = parts[0], parts[1]
        key = key.strip().upper()
        value = value.strip().split()[0]
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number):
            out[key] = number
    return out


def capture(endpoint: str, names: list[str], timeout: float) -> dict[str, float]:
    try:
        from pymavlink import mavutil
    except ImportError as exc:
        raise RuntimeError("pymavlink is required only for --endpoint capture") from exc

    master = mavutil.mavlink_connection(endpoint, source_system=255)
    hb = master.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise RuntimeError(f"no heartbeat from {endpoint}")
    remaining = {name.upper() for name in names}
    values: dict[str, float] = {}
    # A full parameter snapshot is more reliable across ArduPilot versions than a
    # burst of single-name requests and is exactly what F9 requires before any
    # bench change. PARAM_REQUEST_LIST is read-only; this script never PARAM_SETs.
    master.param_fetch_all()
    deadline = time.monotonic() + timeout
    next_request = time.monotonic() + 5.0
    while remaining and time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_request:
            master.param_fetch_all()
            next_request = now + 5.0
        msg = master.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
        if msg is None:
            continue
        raw = msg.param_id
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="ignore")
        name = str(raw).rstrip("\x00").upper()
        values[name] = float(msg.param_value)
        if name in remaining:
            remaining.remove(name)
    if remaining:
        print("WARN: parameter snapshot did not include: " + ", ".join(sorted(remaining)), file=sys.stderr)
    return values


def audit(values: dict[str, float], policy: dict) -> tuple[list[dict], bool]:
    results: list[dict] = []
    ok = True
    for rule in policy.get("parameters", []):
        canonical = str(rule["name"]).upper()
        candidates = [canonical] + [str(x).upper() for x in rule.get("aliases", [])]
        name = next((x for x in candidates if x in values), canonical)
        value = values.get(name)
        status = "PASS"
        reason = "captured" if name == canonical else f"captured via alias {name}"
        if value is None:
            status = "FAIL" if rule.get("required", True) else "WARN"
            reason = "missing"
        else:
            if rule.get("nonzero") and value == 0:
                status, reason = "FAIL", "must be non-zero for the intended bench policy"
            allowed = rule.get("allowed")
            if allowed is not None and value not in [float(x) for x in allowed]:
                status, reason = "FAIL", f"value {value:g} not in allowed set {allowed}"
            if "equals" in rule and value != float(rule["equals"]):
                status, reason = "FAIL", f"value {value:g} != expected {rule['equals']}"
            env_name = rule.get("equals_env")
            if env_name:
                env_value = os.environ.get(env_name)
                if env_value is None:
                    if status == "PASS":
                        status, reason = "WARN", f"environment {env_name} not set; equality not checked"
                else:
                    try:
                        expected = float(env_value)
                    except ValueError:
                        status, reason = "FAIL", f"environment {env_name} is not numeric"
                    else:
                        if value != expected:
                            status, reason = "FAIL", f"value {value:g} != {env_name}={expected:g}"
            minimum = rule.get("min")
            maximum = rule.get("max")
            if minimum is not None and value < float(minimum):
                status, reason = "FAIL", f"value {value:g} < min {minimum}"
            if maximum is not None and value > float(maximum):
                status, reason = "FAIL", f"value {value:g} > max {maximum}"
        if status == "FAIL":
            ok = False
        results.append({"name": name, "canonical": canonical, "value": value,
                        "status": status, "reason": reason, "purpose": rule.get("purpose", "")})
    for group in policy.get("required_any", []):
        names = [str(x).upper() for x in group.get("names", [])]
        present = [name for name in names if name in values]
        status = "PASS" if present else "FAIL"
        if not present:
            ok = False
        results.append({"name": "ANY(" + ",".join(names) + ")", "canonical": "required_any",
                        "value": None, "status": status,
                        "reason": "present: " + ", ".join(present) if present else "none present",
                        "purpose": group.get("purpose", "")})
    return results, ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="docs/f9_expected_params.json")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="existing NAME=value snapshot")
    source.add_argument("--endpoint", help="read-only MAVLink endpoint, e.g. tcp:127.0.0.1:5760")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--snapshot-out", help="optional NAME=value capture output")
    ap.add_argument("--json-out", help="optional JSON audit report")
    args = ap.parse_args()

    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    names = []
    for p in policy.get("parameters", []):
        names.append(str(p["name"]).upper())
        names.extend(str(x).upper() for x in p.get("aliases", []))
    try:
        if args.input:
            values = parse_snapshot(Path(args.input).read_text(encoding="utf-8"))
        else:
            values = capture(args.endpoint, names, args.timeout)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.snapshot_out:
        body = "".join(f"{k}={values[k]:g}\n" for k in sorted(values))
        Path(args.snapshot_out).write_text(body, encoding="utf-8")

    results, passed = audit(values, policy)
    report = {"policy": args.policy, "source": args.input or args.endpoint,
              "passed": passed, "results": results}
    for item in results:
        value = "MISSING" if item["value"] is None else f"{item['value']:g}"
        print(f"{item['status']:4} {item['name']:<18} {value:<10} {item['reason']}")
    print("F9A PARAM AUDIT:", "PASS" if passed else "FAIL")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
