"""Regression tests for PingService teardown lifecycle.

A GroundStation may be closed while its PingWorker is blocked in an ICMP child
process.  The worker must be cancellable and joined before Qt destroys the
parent QObject; otherwise Windows can terminate the process with a native access
violation during large GUI suites.
"""
import os
import sys
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import pinger  # noqa: E402
from swarmgod_gui.core.pinger import PingResult, PingService  # noqa: E402


class PingLifecycle(unittest.TestCase):
    def test_shutdown_cancels_and_joins_active_worker(self):
        entered = threading.Event()
        original = pinger.probe

        def blocking_probe(host, port=0, timeout_ms=1200, cancel_event=None):
            entered.set()
            self.assertIsNotNone(cancel_event)
            cancel_event.wait(2.0)
            return PingResult(host=host, ok=False, ms=None, tcp_ms=None,
                              port=port, method="fail", detail="cancelled")

        pinger.probe = blocking_probe
        try:
            service = PingService()
            self.assertTrue(service.ping(1, "127.0.0.1", 5760))
            self.assertTrue(entered.wait(1.0), "PingWorker never entered probe")
            worker = service._worker
            self.assertIsNotNone(worker)
            self.assertTrue(worker.isRunning())

            self.assertTrue(service.shutdown(1500))
            self.assertFalse(worker.isRunning())
            self.assertFalse(service.busy)
            self.assertIsNone(service._worker)
            self.assertFalse(service.ping(1, "127.0.0.1", 5760),
                             "closed PingService must not start a new worker")
        finally:
            pinger.probe = original


if __name__ == "__main__":
    unittest.main()
