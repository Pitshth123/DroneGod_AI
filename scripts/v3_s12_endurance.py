#!/usr/bin/env python3
"""Canonical V3-S12 SITL endurance/performance evidence recorder.

The recorder attaches to an already-running Core and SITL fleet.  It owns only
its gRPC clients/streams and therefore never performs broad process cleanup.
Long runs use the same CLI as smoke runs; only --duration changes.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import math
import os
import pathlib
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter


SCHEMA = "dronegod.v3.s12.endurance/v1"
SCENARIOS = (
    "idle-connected",
    "telemetry-5",
    "telemetry-10",
    "frontend-presentation",
    "reconnect-churn",
    "client-lifecycle",
    "mission-start-cancel",
)
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*$", re.I)
_ALLOWED_AUTHORITY = {"", "core-single", "core-single-wait"}


def parse_duration(value: str) -> float:
    """Parse an explicit duration and return seconds."""
    match = _DURATION_RE.match(str(value))
    if not match:
        raise argparse.ArgumentTypeError("duration must look like 500ms, 30s, 30m, 2h")
    amount = float(match.group(1))
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2).lower()]
    seconds = amount * scale
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be greater than zero")
    return seconds


def duration_label(seconds: float) -> str:
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds / 3600:g}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g}m"
    if seconds >= 1:
        return f"{seconds:g}s"
    return f"{seconds * 1000:g}ms"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def summarize(values: list[float]) -> dict | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    return {
        "samples": len(clean),
        "start": clean[0],
        "end": clean[-1],
        "delta": clean[-1] - clean[0],
        "min": min(clean),
        "max": max(clean),
        "mean": statistics.fmean(clean),
    }


def monotonic_growth(values: list[float]) -> bool:
    """Conservative trend flag: at least five samples and four real rises."""
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(clean) < 5 or clean[-1] <= clean[0]:
        return False
    rises = sum(b > a for a, b in zip(clean, clean[1:]))
    return all(b >= a for a, b in zip(clean, clean[1:])) and rises >= 4


def classify_run(*, aborted: bool, hard_failures: list[str], requested_s: float,
                 actual_s: float, telemetry_required: bool, telemetry_total: int) -> tuple[str, str]:
    if aborted:
        return "ABORTED", "operator interrupt"
    if hard_failures:
        return "FAIL", hard_failures[0]
    if actual_s + 0.05 < requested_s:
        return "FAIL", "workload ended before requested duration"
    if telemetry_required and telemetry_total <= 0:
        return "FAIL", "no telemetry received"
    return "PASS", "requested workload duration completed"


def validate_evidence(report: dict) -> None:
    required = {
        "schema", "generated_at", "source", "profile", "scenario",
        "requested_duration_seconds", "actual_duration_seconds", "result",
        "start_reason", "end_reason", "processes", "participant_count",
        "sample_interval_seconds", "samples", "aggregates", "counters",
        "file_growth", "availability", "acceptance",
    }
    missing = sorted(required - report.keys())
    if missing:
        raise ValueError(f"evidence missing fields: {', '.join(missing)}")
    if report["schema"] != SCHEMA:
        raise ValueError("unexpected evidence schema")
    if report["result"] not in {"PASS", "FAIL", "ABORTED"}:
        raise ValueError("invalid result")
    if not isinstance(report["samples"], list) or not report["samples"]:
        raise ValueError("evidence has no resource/telemetry samples")


def write_evidence(path: pathlib.Path, report: dict) -> None:
    validate_evidence(report)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence: {path}")
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        json.loads(temp.read_text(encoding="utf-8"))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def tree_size(path: pathlib.Path | None) -> int | None:
    if path is None or not path.exists():
        return None
    if path.is_file():
        return path.stat().st_size
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        return None
    return total


def process_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    return pathlib.Path(f"/proc/{pid}").exists()


def _windows_process_metrics(pid: int) -> dict | None:
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, int(pid))
    if not handle:
        return None
    try:
        mem = PROCESS_MEMORY_COUNTERS_EX()
        mem.cb = ctypes.sizeof(mem)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(mem), mem.cb):
            return None
        handles = wintypes.DWORD()
        ctypes.windll.kernel32.GetProcessHandleCount(handle, ctypes.byref(handles))
        # NtQueryInformationProcess class 0 exposes no thread total.  The native
        # gRPC pool is still observable through this process-wide OS thread count.
        thread_count = None
        snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        invalid = ctypes.c_void_p(-1).value
        if snapshot != invalid:
            class THREADENTRY32(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                ]
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            count = 0
            ok = ctypes.windll.kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while ok:
                if entry.th32OwnerProcessID == int(pid):
                    count += 1
                ok = ctypes.windll.kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            ctypes.windll.kernel32.CloseHandle(snapshot)
            thread_count = count
        return {
            "rss_bytes": int(mem.WorkingSetSize),
            "private_bytes": int(mem.PrivateUsage),
            "os_threads": thread_count,
            "handles": int(handles.value),
        }
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _proc_process_metrics(pid: int) -> dict | None:
    status = pathlib.Path(f"/proc/{pid}/status")
    if not status.exists():
        return None
    values = {}
    try:
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key] = value.strip()
        kb = lambda key: int(values.get(key, "0 kB").split()[0]) * 1024
        try:
            handles = len(list(pathlib.Path(f"/proc/{pid}/fd").iterdir()))
        except OSError:
            handles = None
        return {
            "rss_bytes": kb("VmRSS"),
            "private_bytes": None,
            "os_threads": int(values.get("Threads", "0")),
            "handles": handles,
        }
    except (OSError, ValueError):
        return None


def process_metrics(pid: int | None) -> dict | None:
    if not pid:
        return None
    return _windows_process_metrics(pid) if os.name == "nt" else _proc_process_metrics(pid)


def git_identity(root: pathlib.Path) -> dict:
    def run(*args):
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, text=True,
                timeout=5, check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    return {
        "git_commit": run("rev-parse", "HEAD"),
        "git_branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "workspace_dirty": bool(run("status", "--porcelain")),
    }


class TelemetrySession:
    def __init__(self, client, drone_ids, on_message):
        from swarmgod_gui.core.rpc import telemetry_pb2
        self._client = client
        self._request = telemetry_pb2.SubscribeTelemetryRequest(
            drone_ids=[int(x) for x in drone_ids], max_hz=20)
        self._on_message = on_message
        self._call = None
        self._thread = None
        self.error = None
        self.ended = False

    def start(self):
        self._call = self._client.stub.SubscribeTelemetry(self._request)
        self._thread = threading.Thread(target=self._run, name="s12-telemetry", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            for message in self._call:
                self._on_message(message)
        except Exception as exc:  # gRPC cancellation during owned cleanup is expected
            if self._call is not None:
                self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ended = True

    def close(self):
        call, self._call = self._call, None
        if call is not None:
            try:
                call.cancel()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            alive = self._thread.is_alive()
            self._thread = None
            return not alive
        return True


class Recorder:
    def __init__(self, args, root: pathlib.Path):
        from swarmgod_gui.core.health_monitor import HealthMonitor
        from swarmgod_gui.core.telemetry_store import TelemetryRenderGate, TelemetryStore
        self.args = args
        self.root = root
        self.stop_event = threading.Event()
        self.aborted = False
        self.client = None
        self.stream = None
        self.lock = threading.RLock()
        self.store = TelemetryStore()
        self.render_gate = TelemetryRenderGate(min_interval_s=0.10)
        self.health = HealthMonitor(heartbeat_ms=1000)
        self.telemetry_total = 0
        self.telemetry_by_drone = Counter()
        self.last_receive = {}
        self.last_source_age_ms = {}
        self.rpc_latencies = []
        self.rpc_errors = []
        self.stream_errors = []
        self.hard_failures = []
        self.investigations = []
        self.reconnect_count = 0
        self.lifecycle_cycles = 0
        self.mission_transitions = 0
        self.mission_cycles = 0
        self.last_mission_key = None
        self.pending_run_id = 0
        self.samples = []
        self.core_crashes = 0
        self.connected_by_harness = []
        self.start_monotonic = None
        self.last_rate_at = None
        self.last_rate_total = 0

    def request_abort(self, *_):
        self.aborted = True
        self.stop_event.set()

    def _new_client(self):
        from swarmgod_gui.core.grpc_client import CoreClient
        return CoreClient(self.args.core)

    def _rpc(self, name, call):
        started = time.perf_counter()
        try:
            result = call()
            elapsed = (time.perf_counter() - started) * 1000.0
            self.rpc_latencies.append(elapsed)
            self.health.note_rpc(name, elapsed)
            return result
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            self.rpc_errors.append({
                "at_seconds": self.elapsed(), "rpc": name,
                "duration_ms": elapsed, "error": f"{type(exc).__name__}: {exc}",
            })
            raise

    def _on_telemetry(self, message):
        now_mono = time.monotonic()
        source_age = max(0.0, time.time() * 1000.0 - float(message.timestamp_ms or 0))
        view = self.store.update(message)
        rendered = self.render_gate.should_render(view)
        if rendered:
            self.health.record_render()
        self.health.note_telemetry(int(message.drone_id), now_mono)
        with self.lock:
            did = int(message.drone_id)
            self.telemetry_total += 1
            self.telemetry_by_drone[did] += 1
            self.last_receive[did] = now_mono
            self.last_source_age_ms[did] = source_age

    def elapsed(self):
        return 0.0 if self.start_monotonic is None else time.monotonic() - self.start_monotonic

    def open(self):
        self.client = self._new_client()
        # Prove Core is reachable before defining workload time zero.
        from swarmgod_gui.core.rpc import service_pb2
        self._rpc("GetFleetSnapshot", lambda: self.client.stub.GetFleetSnapshot(
            service_pb2.FleetSnapshotRequest(), timeout=5))
        self.health.note_core_connected(True)
        if self.args.connect:
            for did in range(1, self.args.drone_count + 1):
                response = self._rpc("Connect", lambda did=did: self.client.connect_drone(
                    did, f"S12_SITL_{did}", self.args.connect_host,
                    self.args.base_port + (did - 1) * 10, "tcp"))
                if not response.ok and "already connected" not in response.message.lower():
                    raise RuntimeError(f"Connect D{did} rejected: {response.message}")
                if response.ok and "already connected" not in response.message.lower():
                    self.connected_by_harness.append(did)
        ids = list(range(1, self.args.drone_count + 1))
        self.stream = TelemetrySession(self.client, ids, self._on_telemetry)
        self.stream.start()
        self.start_monotonic = time.monotonic()
        self.last_rate_at = self.start_monotonic

    def close_stream_and_client(self):
        clean = True
        if self.stream is not None:
            clean = self.stream.close()
            if self.stream.error and not self.aborted:
                self.stream_errors.append(self.stream.error)
            self.stream = None
        if self.client is not None:
            self.client.close()
            self.client = None
        self.health.note_core_connected(False)
        if not clean:
            self.hard_failures.append("owned telemetry thread did not terminate")

    def reconnect(self):
        before = self._mission_state_key()
        self.close_stream_and_client()
        self.client = self._new_client()
        from swarmgod_gui.core.rpc import service_pb2
        self._rpc("GetFleetSnapshot", lambda: self.client.stub.GetFleetSnapshot(
            service_pb2.FleetSnapshotRequest(), timeout=5))
        self.stream = TelemetrySession(
            self.client, range(1, self.args.drone_count + 1), self._on_telemetry)
        self.stream.start()
        self.health.note_core_connected(True)
        self.reconnect_count += 1
        after = self._mission_state_key()
        # Reconnect is presentation-only: it may observe natural progression but
        # must never create a new plan/run identity.
        if before and after and before[:2] != after[:2]:
            self.hard_failures.append(
                f"mission identity changed across client reconnect: {before[:2]} -> {after[:2]}")

    def _mission_state(self):
        return self._rpc("GetMissionState", self.client.get_mission_state)

    def _mission_state_key(self):
        try:
            state = self._mission_state()
        except Exception:
            return None
        return (int(state.run_id), str(state.plan_id), int(state.state), int(state.revision))

    def observe_mission(self):
        state = self._mission_state()
        key = (int(state.run_id), str(state.plan_id), int(state.state), int(state.revision))
        if self.last_mission_key is not None and key != self.last_mission_key:
            self.mission_transitions += 1
        self.last_mission_key = key
        if state.recovery_required and (state.active or state.authority_active):
            self.hard_failures.append("recovery-required mission regained live authority")
        if int(state.state) in (6, 7, 8, 9) and (state.active or state.authority_active):
            self.hard_failures.append("terminal mission retained live authority")
        return state

    def lifecycle_action(self):
        from swarmgod_gui.core.rpc import service_pb2
        for _ in range(self.args.lifecycle_batch):
            temp = self._new_client()
            try:
                self._rpc("LifecycleGetFleetSnapshot", lambda: temp.stub.GetFleetSnapshot(
                    service_pb2.FleetSnapshotRequest(), timeout=5))
            finally:
                temp.close()
            self.lifecycle_cycles += 1

    def mission_action(self):
        from swarmgod_gui.core.rpc import mission_pb2
        state = self.observe_mission()
        if state.active:
            response = self._rpc("CancelMission", lambda: self.client.cancel_mission(
                state.run_id, f"s12-cancel-{uuid.uuid4().hex}"))
            if not response.ok:
                raise RuntimeError(f"CancelMission rejected: {response.message}")
            self.pending_run_id = 0
            self.mission_cycles += 1
            return
        view = self.store.get(1)
        if view is None or not view.armed or view.position is None:
            raise RuntimeError("mission-start-cancel requires armed D1 SITL telemetry with position")
        north_deg = self.args.mission_offset_m / 111_320.0
        target_alt = max(3.0, float(view.position.alt_rel))
        plan_id = f"s12-endurance-{uuid.uuid4().hex}"
        plan = mission_pb2.MissionPlan(
            plan_id=plan_id,
            mode=mission_pb2.MISSION_MODE_GROUPED,
            participants=[1],
            routes=[mission_pb2.MissionRoute(
                drone_id=0,
                points=[mission_pb2.MissionWaypoint(
                    seq=0, lat=float(view.position.lat) + north_deg,
                    lon=float(view.position.lon), alt=target_alt)])],
            arrival_radius_m=3.0,
            participant_altitudes={1: target_alt},
        )
        response = self._rpc("StartMission", lambda: self.client.start_mission(
            plan, f"s12-op-{uuid.uuid4().hex}"))
        if not response.ok or not response.authority_active:
            raise RuntimeError(f"StartMission did not gain allowed authority: {response.message}")
        self.pending_run_id = int(response.run_id)

    def take_sample(self, phase: str):
        now = time.monotonic()
        with self.lock:
            total = self.telemetry_total
            by_drone = dict(self.telemetry_by_drone)
            receive_ages = {
                str(did): max(0.0, (now - stamp) * 1000.0)
                for did, stamp in self.last_receive.items()
            }
            source_ages = {str(k): v for k, v in self.last_source_age_ms.items()}
        elapsed_rate = max(1e-9, now - self.last_rate_at)
        ingest_rate = (total - self.last_rate_total) / elapsed_rate
        self.last_rate_total = total
        self.last_rate_at = now
        render = self.render_gate.stats()
        health = self.health.snapshot()
        python_process = process_metrics(os.getpid())
        if python_process is not None:
            # threading.active_count is the Python-visible subset; os_threads
            # also sees cygrpc/native background workers.
            python_process["python_threads"] = threading.active_count()
        sample = {
            "phase": phase,
            "at_seconds": self.elapsed(),
            "timestamp": utc_now(),
            "python_process": python_process,
            "core_process": process_metrics(self.args.core_pid),
            "telemetry": {
                "total": total,
                "per_drone": by_drone,
                "ingest_rate_hz": ingest_rate,
                "receive_age_ms": receive_ages,
                "source_age_ms": source_ages,
            },
            "presentation": {
                "ui_heartbeat_ms": health["heartbeat_ms"],
                "ui_status": health["status"],
                "stall_count": health["stall_count"],
                "render_total": render["rendered"],
                "render_skipped": render["skipped"],
                "render_rate_hz": self.health.render_rate(),
            },
            "rpc": {
                "calls": len(self.rpc_latencies),
                "errors": len(self.rpc_errors),
                "last_latency_ms": self.rpc_latencies[-1] if self.rpc_latencies else None,
            },
            "mission": {
                "transitions": self.mission_transitions,
                "cycles": self.mission_cycles,
            },
            "reconnect_count": self.reconnect_count,
            "client_lifecycle_cycles": self.lifecycle_cycles,
        }
        self.samples.append(sample)

    def run(self):
        self.open()
        self.health.record_ui_tick()
        self.observe_mission()
        self.take_sample("initial")
        deadline = self.start_monotonic + self.args.duration
        next_sample = self.start_monotonic + self.args.sample_interval
        next_heartbeat = self.start_monotonic + 1.0
        next_action = self.start_monotonic + self.args.action_interval
        consecutive_rpc_errors = 0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            now = time.monotonic()
            wait_until = min(deadline, next_sample, next_heartbeat, next_action)
            self.stop_event.wait(max(0.0, min(0.25, wait_until - now)))
            now = time.monotonic()
            if now >= next_heartbeat:
                self.health.record_ui_tick()
                next_heartbeat += 1.0
            if now >= next_action:
                try:
                    if self.args.scenario == "reconnect-churn":
                        self.reconnect()
                    elif self.args.scenario == "client-lifecycle":
                        self.lifecycle_action()
                    elif self.args.scenario == "mission-start-cancel":
                        self.mission_action()
                    consecutive_rpc_errors = 0
                except Exception as exc:
                    consecutive_rpc_errors += 1
                    self.rpc_errors.append({
                        "at_seconds": self.elapsed(), "rpc": "scenario-action",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    if consecutive_rpc_errors >= 3:
                        self.hard_failures.append("three consecutive scenario/RPC failures")
                        break
                next_action += self.args.action_interval
            if now >= next_sample:
                try:
                    self.observe_mission()
                    consecutive_rpc_errors = 0
                except Exception:
                    consecutive_rpc_errors += 1
                    if consecutive_rpc_errors >= 3:
                        self.hard_failures.append("three consecutive Core health RPC failures")
                        break
                if self.args.core_pid and not process_exists(self.args.core_pid):
                    self.core_crashes += 1
                    self.hard_failures.append("observed Core process exited")
                    break
                if self.stream is not None and self.stream.ended and not self.aborted:
                    self.hard_failures.append("telemetry stream ended unexpectedly")
                    break
                with self.lock:
                    newest_receive = max(self.last_receive.values(), default=None)
                    has_telemetry = self.telemetry_total > 0
                if (has_telemetry and newest_receive is not None and
                        now - newest_receive >= 3 * self.args.sample_interval):
                    self.hard_failures.append(
                        "telemetry pipeline produced no packets for three sample intervals")
                    break
                self.take_sample("steady")
                next_sample += self.args.sample_interval
        self.take_sample("final")

    def cleanup(self):
        if self.pending_run_id and self.client is not None:
            try:
                self.client.cancel_mission(
                    self.pending_run_id, f"s12-cleanup-{uuid.uuid4().hex}")
            except Exception as exc:
                self.rpc_errors.append({"rpc": "cleanup-CancelMission", "error": str(exc)})
        self.close_stream_and_client()

    def report(self, start_at: str, log_start, db_start):
        actual = self.elapsed()
        missing_drones = sorted(
            set(range(1, self.args.drone_count + 1)) - set(self.telemetry_by_drone))
        if missing_drones:
            self.hard_failures.append(
                "no telemetry received for expected participants: " +
                ",".join(str(x) for x in missing_drones))
        result, reason = classify_run(
            aborted=self.aborted, hard_failures=self.hard_failures,
            requested_s=self.args.duration, actual_s=actual,
            telemetry_required=True, telemetry_total=self.telemetry_total)

        def series(group, key):
            out = []
            for sample in self.samples:
                value = (sample.get(group) or {}).get(key)
                if value is not None:
                    out.append(value)
            return out

        py_rss = series("python_process", "rss_bytes")
        py_private = series("python_process", "private_bytes")
        py_threads = series("python_process", "os_threads")
        py_managed_threads = series("python_process", "python_threads")
        core_rss = series("core_process", "rss_bytes")
        core_threads = series("core_process", "os_threads")
        for label, values in (
            ("Python RSS", py_rss), ("Python OS threads", py_threads),
            ("Core RSS", core_rss), ("Core OS threads", core_threads),
        ):
            if monotonic_growth(values):
                self.investigations.append(f"monotonic {label} growth across >=5 samples")
        render = self.render_gate.stats()
        log_end = tree_size(self.args.log_dir)
        db_end = tree_size(self.args.db)
        log_delta = None if log_start is None or log_end is None else log_end - log_start
        db_delta = None if db_start is None or db_end is None else db_end - db_start
        return {
            "schema": SCHEMA,
            "generated_at": utc_now(),
            "started_at": start_at,
            "source": git_identity(self.root),
            "profile": os.getenv("SWARMGOD_PROFILE", "unknown"),
            "authority_token_observed_locally": os.getenv("SWARMGOD_MISSION_AUTHORITY", ""),
            "scenario": self.args.scenario,
            "requested_duration": duration_label(self.args.duration),
            "requested_duration_seconds": self.args.duration,
            "actual_duration_seconds": actual,
            "result": result,
            "start_reason": "operator launched canonical S12 harness",
            "end_reason": reason,
            "processes": {
                "recorder_pid": os.getpid(), "core_pid": self.args.core_pid,
                "owned_child_processes": [], "core_or_sitl_processes_owned": False,
                "core_restart_or_crash_count": self.core_crashes,
            },
            "participant_count": self.args.drone_count,
            "sample_interval_seconds": self.args.sample_interval,
            "samples": self.samples,
            "aggregates": {
                "python_rss_bytes": summarize(py_rss),
                "python_private_bytes": summarize(py_private),
                "python_os_threads": summarize(py_threads),
                "python_managed_threads": summarize(py_managed_threads),
                "core_rss_bytes": summarize(core_rss),
                "core_os_threads": summarize(core_threads),
                "rpc_latency_ms": summarize(self.rpc_latencies),
                "render_total": render["rendered"],
                "render_skipped": render["skipped"],
                "render_skip_percent": (
                    100.0 * render["skipped"] / (render["rendered"] + render["skipped"])
                    if render["rendered"] + render["skipped"] else None),
            },
            "counters": {
                "telemetry_total": self.telemetry_total,
                "telemetry_per_drone": dict(self.telemetry_by_drone),
                "rpc_calls": len(self.rpc_latencies),
                "rpc_errors": len(self.rpc_errors),
                "stream_errors": len(self.stream_errors),
                "reconnects": self.reconnect_count,
                "mission_transitions": self.mission_transitions,
                "mission_start_cancel_cycles": self.mission_cycles,
                "client_lifecycle_cycles": self.lifecycle_cycles,
                "ui_stalls": self.health.snapshot()["stall_count"],
            },
            "errors": {"rpc": self.rpc_errors, "stream": self.stream_errors},
            "file_growth": {
                "log_path": str(self.args.log_dir) if self.args.log_dir else None,
                "log_start_bytes": log_start, "log_end_bytes": log_end,
                "log_delta_bytes": log_delta,
                "database_path": str(self.args.db) if self.args.db else None,
                "database_start_bytes": db_start, "database_end_bytes": db_end,
                "database_delta_bytes": db_delta,
            },
            "availability": {
                "python_rss": bool(py_rss), "python_private_bytes": bool(py_private),
                "python_managed_threads": bool(py_managed_threads),
                "python_os_threads_including_native_grpc": bool(py_threads),
                "core_rss": bool(core_rss), "core_os_threads": bool(core_threads),
                "go_goroutines": "NOT CURRENTLY EXPOSED",
                "native_grpc_threads_separate_from_process_threads": "NOT CURRENTLY EXPOSED",
                "real_gui_frame_timing": "NOT CURRENTLY EXPOSED; presentation gate exercised",
            },
            "acceptance": {
                "hard_failures": self.hard_failures,
                "investigate": self.investigations,
                "telemetry_required": True,
                "completed_requested_duration": actual + 0.05 >= self.args.duration,
                "evidence_parseable": True,
            },
            "cleanup": {
                "owned_stream_closed": self.stream is None,
                "owned_client_closed": self.client is None,
                "external_core_and_sitl_left_running": True,
            },
        }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration", type=parse_duration, default=parse_duration("30m"))
    p.add_argument("--scenario", choices=SCENARIOS, default="idle-connected")
    p.add_argument("--sample-interval", type=parse_duration, default=parse_duration("10s"))
    p.add_argument("--action-interval", type=parse_duration, default=parse_duration("10s"))
    p.add_argument("--output", type=pathlib.Path)
    p.add_argument("--core", default="127.0.0.1:50053")
    p.add_argument("--core-pid", type=int)
    p.add_argument("--drone-count", type=int, default=1)
    p.add_argument("--connect", action="store_true", help="connect Core to SITL TCP ports")
    p.add_argument("--connect-host", default="127.0.0.1")
    p.add_argument("--base-port", type=int, default=5760)
    p.add_argument("--log-dir", type=pathlib.Path)
    p.add_argument("--db", type=pathlib.Path)
    p.add_argument("--lifecycle-batch", type=int, default=5)
    p.add_argument("--mission-offset-m", type=float, default=20.0)
    p.add_argument("--sitl-mission-ack", action="store_true",
                   help="required opt-in for mission-start-cancel; SITL only")
    return p


def normalize_args(args, root: pathlib.Path):
    scenario_counts = {"telemetry-5": 5, "telemetry-10": 10}
    if args.scenario in scenario_counts:
        args.drone_count = scenario_counts[args.scenario]
    if not 1 <= args.drone_count <= 20:
        raise ValueError("drone-count must be 1..20")
    if args.sample_interval <= 0 or args.action_interval <= 0:
        raise ValueError("intervals must be greater than zero")
    if args.lifecycle_batch <= 0:
        raise ValueError("lifecycle-batch must be greater than zero")
    authority = os.getenv("SWARMGOD_MISSION_AUTHORITY", "").strip().lower()
    if authority not in _ALLOWED_AUTHORITY:
        raise ValueError(f"refusing non-live mission authority token: {authority!r}")
    if args.scenario == "mission-start-cancel":
        profile = os.getenv("SWARMGOD_PROFILE", "").strip().lower()
        if not args.sitl_mission_ack or profile != "sitl":
            raise ValueError("mission-start-cancel requires --sitl-mission-ack and SWARMGOD_PROFILE=sitl")
        if authority not in {"core-single", "core-single-wait"}:
            raise ValueError("mission-start-cancel requires core-single or core-single-wait")
        if args.drone_count != 1:
            raise ValueError("live mission authority remains single-drone only")
    if args.output is None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        args.output = root / "evidence" / "v3-s12" / f"{stamp}-{args.scenario}.json"
    if args.log_dir is None:
        args.log_dir = root / "backend" / "logs"
    if args.db is None:
        args.db = root / "backend" / "logs" / "missionverify.db"
    return args


def main(argv=None) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    frontend = root / "frontend"
    if str(frontend) not in sys.path:
        sys.path.insert(0, str(frontend))
    args = normalize_args(parser().parse_args(argv), root)
    recorder = Recorder(args, root)
    start_at = utc_now()
    log_start, db_start = tree_size(args.log_dir), tree_size(args.db)
    old_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[sig] = signal.signal(sig, recorder.request_abort)
        except (ValueError, OSError):
            pass
    exit_code = 1
    try:
        recorder.run()
    except KeyboardInterrupt:
        recorder.request_abort()
    except Exception as exc:
        recorder.hard_failures.append(f"{type(exc).__name__}: {exc}")
        if not recorder.samples:
            recorder.start_monotonic = recorder.start_monotonic or time.monotonic()
            recorder.last_rate_at = recorder.start_monotonic
            recorder.take_sample("initial-failure")
    finally:
        recorder.cleanup()
        if not recorder.samples:
            recorder.take_sample("final")
        report = recorder.report(start_at, log_start, db_start)
        write_evidence(args.output, report)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        print(f"{report['result']}: {report['end_reason']}")
        print(f"evidence: {args.output.resolve()}")
        print(f"actual: {report['actual_duration_seconds']:.3f}s; telemetry: {report['counters']['telemetry_total']}")
        exit_code = 0 if report["result"] == "PASS" else (130 if report["result"] == "ABORTED" else 1)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
