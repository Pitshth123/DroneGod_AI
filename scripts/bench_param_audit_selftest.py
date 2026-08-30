#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("bench_param_audit", HERE / "bench_param_audit.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


def main() -> int:
    values = mod.parse_snapshot("""
# comment
SYSID_MYGCS=250
FS_GCS_ENABLE 1
ARMING_CHECK=1
BATT_FS_CRT_ACT=2
junk=not-a-number
""")
    assert values["SYSID_MYGCS"] == 250
    assert "JUNK" not in values

    policy = {"parameters": [
        {"name": "SYSID_MYGCS", "equals_env": "SWARMGOD_MAVLINK_SYSID"},
        {"name": "FS_GCS_ENABLE", "nonzero": True},
        {"name": "ARMING_CHECK", "nonzero": True},
        {"name": "BATT_FS_CRT_ACT", "nonzero": True},
    ]}
    old = os.environ.get("SWARMGOD_MAVLINK_SYSID")
    os.environ["SWARMGOD_MAVLINK_SYSID"] = "250"
    try:
        results, passed = mod.audit(values, policy)
        assert passed, results
        bad = dict(values)
        bad["FS_GCS_ENABLE"] = 0
        results, passed = mod.audit(bad, policy)
        assert not passed
        assert next(r for r in results if r["name"] == "FS_GCS_ENABLE")["status"] == "FAIL"
    finally:
        if old is None:
            os.environ.pop("SWARMGOD_MAVLINK_SYSID", None)
        else:
            os.environ["SWARMGOD_MAVLINK_SYSID"] = old

    # Verify JSON policy shape remains consumable by the same audit function.
    project = HERE.parent
    baseline = json.loads((project / "docs" / "f9_expected_params.json").read_text(encoding="utf-8"))
    assert len(baseline["parameters"]) >= 4

    # Snapshot output is deliberately plain NAME=value so it is diffable and can
    # be archived without any Python-specific serialization.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "snapshot.txt"
        p.write_text("SYSID_MYGCS=250\nFS_GCS_ENABLE=1\n", encoding="utf-8")
        parsed = mod.parse_snapshot(p.read_text(encoding="utf-8"))
        assert parsed == {"SYSID_MYGCS": 250.0, "FS_GCS_ENABLE": 1.0}

    print("bench_param_audit selftest PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
