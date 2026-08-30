"""Deterministic shutdown lifecycle for the gRPC streaming QThreads.

These tests pin the V1 Phase 3 crash repair: the telemetry/event stream workers
must terminate cleanly no matter how ``stop()`` races ``run()``, and
GroundStation must never close its owned gRPC channel while a worker is still
inside the stream iterator (the previous native access violation, 0xC0000005,
seen during headless teardown of the full frontend suite).

    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m pytest -q tests/test_grpc_lifecycle.py
"""
import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

import grpc  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from swarmgod_gui.core.grpc_client import TelemetryThread, EventThread  # noqa: E402
# Import GroundStation (which imports QtWebEngineWidgets) BEFORE any QApplication
# instance exists, exactly like the other headless GUI suites.
from swarmgod_gui.app import GroundStation  # noqa: E402

_app = QApplication.instance() or QApplication([])


# ── fake gRPC transport ────────────────────────────────────────────────────

class _FakeRpcError(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.CANCELLED

    def details(self):
        return "cancelled"


class _BlockingCall:
    """A server-stream stand-in that blocks in __next__ until cancel()."""

    def __init__(self):
        self._ev = threading.Event()
        self._cancelled = False
        self.cancel_count = 0
        self.entered = threading.Event()   # set once the iterator is blocking

    def __iter__(self):
        return self

    def __next__(self):
        self.entered.set()
        self._ev.wait()                    # block until cancel()
        if self._cancelled:
            raise _FakeRpcError()
        raise StopIteration

    def cancel(self):
        self.cancel_count += 1
        self._cancelled = True
        self._ev.set()


class _FakeStub:
    def __init__(self):
        self.open_count = 0
        self.calls = []

    def _make(self):
        self.open_count += 1
        c = _BlockingCall()
        self.calls.append(c)
        return c

    def SubscribeTelemetry(self, _req):
        return self._make()

    def SubscribeEvents(self, _req):
        return self._make()


def _join(th, timeout=2.0):
    return th.wait(int(timeout * 1000))


class ThreadLifecycle(unittest.TestCase):

    # 1 + 3 — stop BEFORE the stream is ever opened (both thread kinds)
    def test_stop_before_stream_opened_never_opens_a_stream(self):
        for cls in (TelemetryThread, EventThread):
            stub = _FakeStub()
            th = cls(stub)
            th.stop()                      # request stop before start()
            th.start()
            self.assertTrue(_join(th), f"{cls.__name__} did not exit")
            self.assertEqual(stub.open_count, 0,
                             f"{cls.__name__} opened a stream after stop()")
            self.assertFalse(th.isRunning())

    # 2 + 3 — stop WHILE blocked inside the stream iterator (both thread kinds)
    def test_stop_while_blocked_in_stream_cancels_and_exits(self):
        for cls in (TelemetryThread, EventThread):
            stub = _FakeStub()
            th = cls(stub)
            th.start()
            # wait until the worker is actually blocking in __next__
            deadline = time.time() + 2.0
            while stub.open_count == 0 and time.time() < deadline:
                time.sleep(0.005)
            self.assertEqual(stub.open_count, 1, f"{cls.__name__} never opened")
            self.assertTrue(stub.calls[0].entered.wait(2.0),
                            f"{cls.__name__} never entered the iterator")
            th.stop()
            self.assertTrue(_join(th), f"{cls.__name__} stayed blocked after stop()")
            self.assertGreaterEqual(stub.calls[0].cancel_count, 1)
            self.assertFalse(th.isRunning())

    # 4 — stop() is idempotent (before start, and repeated while blocked)
    def test_stop_is_idempotent(self):
        stub = _FakeStub()
        th = TelemetryThread(stub)
        th.stop()
        th.stop()                          # repeated pre-start stop is harmless
        th.start()
        self.assertTrue(_join(th))
        self.assertEqual(stub.open_count, 0)

        stub2 = _FakeStub()
        th2 = TelemetryThread(stub2)
        th2.start()
        self.assertTrue(stub2.calls or _wait_calls(stub2))
        th2.stop()
        th2.stop()                         # second cancel must not raise
        self.assertTrue(_join(th2))
        self.assertFalse(th2.isRunning())

    # 8 (thread level) — shutdown() joins and reports terminated
    def test_shutdown_reports_terminated(self):
        stub = _FakeStub()
        th = EventThread(stub)
        th.start()
        _wait_calls(stub)
        self.assertTrue(th.shutdown(2000))
        self.assertFalse(th.isRunning())


def _wait_calls(stub, timeout=2.0):
    deadline = time.time() + timeout
    while stub.open_count == 0 and time.time() < deadline:
        time.sleep(0.005)
    return stub.open_count > 0


# ── GroundStation-level teardown ordering ──────────────────────────────────

class _Sig:
    """A signal stand-in whose disconnect() reports 'no slots' like PyQt."""

    def disconnect(self):
        raise TypeError("no connections")


class _StuckThread:
    """A worker that never terminates — used to prove the channel-close guard."""

    def __init__(self):
        self.telemetry = _Sig()
        self.stream_error = _Sig()
        self.event = _Sig()

    def shutdown(self, _timeout):
        return False

    def isRunning(self):
        return True


class GroundStationTeardown(unittest.TestCase):
    ADDR = "127.0.0.1:59999"      # deliberately dead port — no core running

    def _new(self):
        return GroundStation(self.ADDR)

    def test_js_callbacks_are_suppressed_once_closing_begins(self):
        calls = []

        class _Page:
            def runJavaScript(self, code):
                calls.append(code)

        class _Web:
            def page(self):
                return _Page()

        fake = type("ClosingWindow", (), {})()
        fake._closing = True
        fake._map_ready = True
        fake.web = _Web()
        GroundStation._js(fake, "must-not-run()")
        self.assertEqual(calls, [])

        win = self._new()
        self.assertFalse(win._closing)
        win.close()
        _app.processEvents()
        self.assertTrue(win._closing)

    # 5 — owned CoreClient is closed even after self.client is replaced
    def test_closes_owned_client_even_when_client_replaced(self):
        from tests.test_ui_selection import FakeClient
        win = self._new()
        win.client = FakeClient()          # exactly what the GUI tests do
        owned = win._owned_core_client
        self.assertIsNotNone(owned)
        calls = {"n": 0}
        real_close = owned.close

        def spy():
            calls["n"] += 1
            return real_close()

        owned.close = spy
        win.close()
        _app.processEvents()
        self.assertEqual(calls["n"], 1)
        self.assertIsNone(win._owned_core_client)

    # 6 — channel is closed ONLY after both stream workers have exited
    def test_channel_closed_only_after_threads_exit(self):
        win = self._new()
        tt, et = win.telem_thread, win.event_thread
        owned = win._owned_core_client
        obs = {}
        real_close = owned.close

        def spy():
            obs["telem_running"] = tt.isRunning()
            obs["event_running"] = et.isRunning()
            obs["closed"] = True
            return real_close()

        owned.close = spy
        win.close()
        _app.processEvents()
        self.assertTrue(obs.get("closed"), "owned channel was never closed")
        self.assertFalse(obs["telem_running"], "channel closed under live telemetry worker")
        self.assertFalse(obs["event_running"], "channel closed under live event worker")
        self.assertFalse(tt.isRunning())
        self.assertFalse(et.isRunning())

    # 6 (negative) — a worker that will not stop blocks the channel close
    def test_channel_not_closed_while_a_worker_is_stuck(self):
        win = self._new()
        win.telem_thread.shutdown(2000)    # cleanly retire the real workers first
        win.event_thread.shutdown(2000)
        closed = {"n": 0}

        class _FakeOwned:
            def close(self):
                closed["n"] += 1

        win._owned_core_client = _FakeOwned()
        win.telem_thread = _StuckThread()
        win.event_thread = _StuckThread()
        win.close()
        _app.processEvents()
        self.assertEqual(closed["n"], 0,
                         "channel must not be closed while a worker is still running")

    # 7 + 8 — repeated create/close cycles in ONE process, no native crash,
    #         no worker left alive after each close
    def test_repeated_create_close_cycles(self):
        for i in range(50):
            win = self._new()
            _app.processEvents()
            tt, et = win.telem_thread, win.event_thread
            win.close()
            win.deleteLater()
            _app.processEvents()
            self.assertFalse(tt.isRunning(), f"telemetry worker alive after cycle {i}")
            self.assertFalse(et.isRunning(), f"event worker alive after cycle {i}")


if __name__ == "__main__":
    unittest.main()
