import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "v3_s12_endurance.py"
sys.path.insert(0, str(ROOT / "frontend"))
SPEC = importlib.util.spec_from_file_location("v3_s12_endurance", SCRIPT)
endurance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(endurance)


@pytest.mark.parametrize("text,seconds", [
    ("500ms", 0.5), ("30s", 30), ("30m", 1800), ("2h", 7200),
])
def test_parse_duration(text, seconds):
    assert endurance.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "0s", "12", "forever", "-1m"])
def test_parse_duration_rejects_invalid_values(text):
    with pytest.raises(Exception):
        endurance.parse_duration(text)


def test_summary_records_baseline_final_delta_and_distribution():
    got = endurance.summarize([10, 12, 14, 13])
    assert got == {
        "samples": 4, "start": 10.0, "end": 13.0, "delta": 3.0,
        "min": 10.0, "max": 14.0, "mean": 12.25,
    }


def test_growth_flag_requires_five_monotonic_samples():
    assert endurance.monotonic_growth([1, 2, 3, 4, 5])
    assert not endurance.monotonic_growth([1, 2, 3, 4])
    assert not endurance.monotonic_growth([1, 3, 2, 4, 5])
    assert not endurance.monotonic_growth([5, 5, 5, 5, 5])


def test_classification_distinguishes_pass_fail_and_abort():
    assert endurance.classify_run(
        aborted=False, hard_failures=[], requested_s=1, actual_s=1,
        telemetry_required=True, telemetry_total=1)[0] == "PASS"
    assert endurance.classify_run(
        aborted=False, hard_failures=["core crash"], requested_s=1, actual_s=1,
        telemetry_required=True, telemetry_total=1)[0] == "FAIL"
    assert endurance.classify_run(
        aborted=True, hard_failures=[], requested_s=10, actual_s=1,
        telemetry_required=True, telemetry_total=1)[0] == "ABORTED"


def _minimal_report():
    return {
        "schema": endurance.SCHEMA,
        "generated_at": "2026-08-29T00:00:00Z",
        "source": {}, "profile": "sitl", "scenario": "idle-connected",
        "requested_duration_seconds": 1, "actual_duration_seconds": 1,
        "result": "PASS", "start_reason": "test", "end_reason": "done",
        "processes": {}, "participant_count": 1, "sample_interval_seconds": 1,
        "samples": [{"phase": "initial"}], "aggregates": {}, "counters": {},
        "file_growth": {}, "availability": {}, "acceptance": {},
    }


def test_evidence_is_parseable_and_never_overwrites(tmp_path):
    path = tmp_path / "evidence.json"
    endurance.write_evidence(path, _minimal_report())
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == endurance.SCHEMA
    with pytest.raises(FileExistsError):
        endurance.write_evidence(path, _minimal_report())


def test_evidence_validation_rejects_incomplete_report():
    with pytest.raises(ValueError, match="missing fields"):
        endurance.validate_evidence({"schema": endurance.SCHEMA})


def test_authority_gate_refuses_non_live_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMGOD_MISSION_AUTHORITY", "core-separate")
    args = endurance.parser().parse_args([
        "--duration", "1s", "--output", str(tmp_path / "x.json")])
    with pytest.raises(ValueError, match="non-live"):
        endurance.normalize_args(args, ROOT)


def test_mission_scenario_requires_explicit_sitl_ack(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARMGOD_PROFILE", "sitl")
    monkeypatch.setenv("SWARMGOD_MISSION_AUTHORITY", "core-single")
    args = endurance.parser().parse_args([
        "--scenario", "mission-start-cancel", "--duration", "1s",
        "--output", str(tmp_path / "x.json")])
    with pytest.raises(ValueError, match="sitl-mission-ack"):
        endurance.normalize_args(args, ROOT)


def test_telemetry_session_uses_request_from_telemetry_contract(monkeypatch):
    class Stub:
        pass

    session = endurance.TelemetrySession(
        type("Client", (), {"stub": Stub()})(), [1, 2], lambda _message: None)
    assert list(session._request.drone_ids) == [1, 2]
    assert session._request.max_hz == 20


def test_current_process_resource_probe_exposes_rss_and_os_threads():
    metrics = endurance.process_metrics(os.getpid())
    assert metrics["rss_bytes"] > 0
    assert metrics["os_threads"] >= 1
