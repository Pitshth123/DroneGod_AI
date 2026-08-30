#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("f9_evidence_report", HERE / "f9_evidence_report.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


def telemetry(**overrides):
    data = {
        "telemetry_verified": True,
        "timing_below_link_warn_margin": True,
        "link_warn_seconds": 3,
        "link_lost_seconds": 10,
        "timing": {"samples": 100, "rate_hz": 10.0, "interval_p95_ms": 101,
                   "interval_max_ms": 110, "age_p95_ms": 2, "age_max_ms": 4},
        "mission": {"active": False},
    }
    data.update(overrides)
    return data


def main() -> int:
    text, passed = mod.build({"passed": True}, telemetry())
    assert passed
    assert "F9A PREPARABLE EVIDENCE: PASS" in text
    assert "F9 OVERALL: PENDING F9B" in text

    text, passed = mod.build({"passed": False}, telemetry())
    assert not passed
    assert "Parameter policy audit: **FAIL**" in text

    text, passed = mod.build({"passed": True}, telemetry(timing_below_link_warn_margin=False))
    assert not passed
    assert "Timing below LinkWarn margin: **FAIL**" in text

    text, passed = mod.build({"passed": True}, telemetry(mission={"active": True}))
    assert not passed
    assert "Baseline mission inactive: **FAIL**" in text

    print("f9_evidence_report selftest PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
