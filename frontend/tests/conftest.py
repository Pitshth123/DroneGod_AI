"""Global Qt test-isolation teardown.

Background
----------
The full frontend suite previously crashed at ~28% with a native access
violation during headless ``GroundStation`` teardown, so every test after that
point never ran.  The V1 Phase 3 streaming-thread lifecycle repair fixed the
crash, and the whole suite now runs to completion — which exposed a pre-existing
test-hygiene problem it had been masking.

Some GUI tests construct a ``GroundStation`` but never ``close()`` it.  A leaked
cockpit keeps its 5 s ``_ping_timer`` / 2 s ``_conn_timer`` and its telemetry /
event stream ``QThread``s alive.  ``PingService`` in particular starts a
``PingWorker`` ``QThread`` that runs a real ``subprocess.run(['ping', ...])``.
Because ``test_launcher_safety`` mocks ``subprocess.run`` (which, being a shared
module attribute, is patched process-wide), a stray background ping fires inside
that mock window and non-deterministically breaks call-count assertions.  The
same class of leak makes other GUI tests flaky depending on run timing — the
failing set differs run to run, and every failing test passes in isolation.

This autouse fixture closes any ``GroundStation`` a test left open, so its timers
and stream threads are stopped before the next test starts.  ``GroundStation``
already tears these down deterministically in ``closeEvent`` (idempotent), so
closing an already-closed cockpit is safe.

Scope
-----
Test infrastructure only.  No product code, command semantics, flight authority,
or safety behaviour is touched.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _close_leaked_ground_stations():
    # Run the test first; only act on what it leaves behind.
    yield

    # Do not trigger any import: if swarmgod_gui.app was never imported, no
    # GroundStation can exist, and importing it here (after a QApplication may
    # already exist) could raise the QtWebEngine-before-QApplication error.
    app_mod = sys.modules.get("swarmgod_gui.app")
    if app_mod is None:
        return
    GroundStation = getattr(app_mod, "GroundStation", None)
    if GroundStation is None:
        return

    try:
        from PyQt5.QtWidgets import QApplication
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return

    leaked = [w for w in app.topLevelWidgets() if isinstance(w, GroundStation)]
    for w in leaked:
        try:
            w.close()          # stops timers + joins stream QThreads (idempotent)
        except Exception:
            pass
    # Flush deleteLater / pending timer stops so nothing fires into the next test.
    for _ in range(2):
        app.processEvents()
