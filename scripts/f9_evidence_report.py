#!/usr/bin/env python3
"""Merge F9 parameter/timing evidence into a human-readable gate report.

This is intentionally conservative: software-preparable evidence can PASS, while
actual FC command/ACK, onboard failsafe, and operator takeover remain PENDING
until F9B is executed on the real bench.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(params: dict, telemetry: dict) -> tuple[str, bool]:
    param_ok = bool(params.get("passed"))
    telem_ok = bool(telemetry.get("telemetry_verified"))
    margin_ok = bool(telemetry.get("timing_below_link_warn_margin"))
    timing = telemetry.get("timing", {})
    mission = telemetry.get("mission", {})
    idle_ok = not bool(mission.get("active"))
    preparable_ok = param_ok and telem_ok and margin_ok and idle_ok

    def mark(v: bool) -> str:
        return "PASS" if v else "FAIL"

    lines = [
        "# F9 Bench Evidence Report",
        "",
        "## F9A software-preparable evidence",
        "",
        f"- Parameter policy audit: **{mark(param_ok)}**",
        f"- Telemetry verified: **{mark(telem_ok)}**",
        f"- Timing below LinkWarn margin: **{mark(margin_ok)}**",
        f"- Baseline mission inactive: **{mark(idle_ok)}**",
        f"- Telemetry samples: {timing.get('samples', 0)}",
        f"- Rate: {timing.get('rate_hz', 0):.2f} Hz",
        f"- Interval p95/max: {timing.get('interval_p95_ms', 0):.1f}/{timing.get('interval_max_ms', 0):.1f} ms",
        f"- Source age p95/max: {timing.get('age_p95_ms', 0):.1f}/{timing.get('age_max_ms', 0):.1f} ms",
        f"- LinkWarn/LinkLost: {telemetry.get('link_warn_seconds', 0):g}/{telemetry.get('link_lost_seconds', 0):g} s",
        "",
        "## F9B actual-FC evidence — mandatory before flight",
        "",
        "- PENDING: real Core→FC command/COMMAND_ACK timing",
        "- PENDING: real onboard GCS/Core-loss failsafe action",
        "- PENDING: real WAIT/HOLD and Cancel on bench",
        "- PENDING: cockpit disconnect/reconnect on real link",
        "- PENDING: operator manual/safety takeover",
        "- PENDING: post-Core-restart no-auto-resume with actual FC",
        "",
        f"**F9A PREPARABLE EVIDENCE: {'PASS' if preparable_ok else 'FAIL'}**",
        "",
        "**F9 OVERALL: PENDING F9B — this report must never unlock real flight by itself.**",
        "",
    ]
    return "\n".join(lines), preparable_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params-json", required=True)
    ap.add_argument("--telemetry-json", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    params = json.loads(Path(args.params_json).read_text(encoding="utf-8"))
    telemetry = json.loads(Path(args.telemetry_json).read_text(encoding="utf-8"))
    text, passed = build(params, telemetry)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
