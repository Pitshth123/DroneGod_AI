"""V3-S08 — command identity / correlation / class-specific dedup.

Proves: correlation ids exist and propagate, duplicate candidates are observed,
commands are classified, and dedup is enforced ONLY for the idempotent class
(double-click) — never for replace-current / cancel-current / emergency.  Also
covers the required scenarios: timeout, late reply, reconnect, double-click,
stale callback.
"""
import os
import sys
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core.command_correlation import (  # noqa: E402
    CommandClass, CommandLedger, classify, command_family)
from swarmgod_gui.core.command_gateway import CommandGateway, DedupedResult  # noqa: E402


class _Result:
    def __init__(self, ok=True, message=""):
        self.ok = ok
        self.message = message


class _Client:
    def __init__(self):
        self.calls = []

    def arm(self, ids):
        self.calls.append(("arm", tuple(ids)))
        return _Result()

    def goto(self, did, lat, lon, alt):
        self.calls.append(("goto", did))
        return _Result()

    def kill(self, ids):
        self.calls.append(("kill", tuple(ids)))
        return _Result()


class Classification(unittest.TestCase):
    def test_class_table(self):
        self.assertEqual(classify("ARM"), CommandClass.IDEMPOTENT)
        self.assertEqual(classify("DISARM"), CommandClass.TAKEOVER)
        self.assertEqual(classify("LAND"), CommandClass.TAKEOVER)
        self.assertEqual(classify("RTL"), CommandClass.TAKEOVER)
        self.assertEqual(classify("HOLD"), CommandClass.TAKEOVER)
        self.assertEqual(classify("MODE GUIDED"), CommandClass.IDEMPOTENT)
        self.assertEqual(classify("TAKEOFF"), CommandClass.IDEMPOTENT)
        self.assertEqual(classify("GOTO"), CommandClass.REPLACE_CURRENT)
        self.assertEqual(classify("GO ALT 20m"), CommandClass.REPLACE_CURRENT)
        self.assertEqual(classify("STOP ALL"), CommandClass.CANCEL_CURRENT)
        self.assertEqual(classify("KILL"), CommandClass.EMERGENCY)
        self.assertEqual(classify("E-STOP"), CommandClass.EMERGENCY)
        self.assertEqual(classify("SERVO A"), CommandClass.NON_IDEMPOTENT)
        self.assertEqual(classify("wat"), CommandClass.NON_IDEMPOTENT)

    def test_family_stability(self):
        self.assertEqual(command_family("GO ALT 20m"), command_family("GO ALT 35m"))
        self.assertEqual(command_family("MODE GUIDED"), command_family("MODE LOITER"))


class Correlation(unittest.TestCase):
    def test_ids_present_and_unique_attempts(self):
        led = CommandLedger()
        t1 = led.begin("ARM", [1], operation_id="op-A")
        led.end(t1, ok=True)
        t2 = led.begin("ARM", [1], operation_id="op-A")
        self.assertEqual(t1.operation_id, "op-A")
        self.assertEqual(t1.command_id, t2.command_id)      # same logical command
        self.assertNotEqual(t1.attempt_id, t2.attempt_id)   # distinct attempts
        self.assertEqual(t2.attempt_index, 2)

    def test_targets_distinguish_command_id(self):
        led = CommandLedger()
        a = led.begin("ARM", [1])
        b = led.begin("ARM", [2])
        self.assertNotEqual(a.command_id, b.command_id)


class DuplicateObservationAndDedup(unittest.TestCase):
    def test_idempotent_duplicate_in_flight_is_suppressed(self):
        led = CommandLedger()
        first = led.begin("ARM", [1])            # in flight, not ended
        second = led.begin("ARM", [1])           # duplicate while in flight
        self.assertTrue(second.duplicate)
        self.assertTrue(second.suppressed)
        self.assertIn(first.command_id, led.inflight_ids())
        # observation records the duplicate
        self.assertIn(second, led.duplicates())

    def test_replace_current_duplicate_observed_but_not_suppressed(self):
        led = CommandLedger()
        led.begin("GOTO", [1])
        dup = led.begin("GOTO", [1])
        self.assertTrue(dup.duplicate)
        self.assertFalse(dup.suppressed)         # replace-current always dispatches

    def test_emergency_duplicate_never_suppressed(self):
        led = CommandLedger()
        led.begin("KILL", [1])
        dup = led.begin("KILL", [1])
        self.assertTrue(dup.duplicate)
        self.assertFalse(dup.suppressed)         # emergency must always reach the boundary

    def test_takeover_duplicate_never_suppressed(self):
        for command in ("HOLD", "LAND", "RTL", "DISARM"):
            with self.subTest(command=command):
                led = CommandLedger()
                led.begin(command, [1])
                dup = led.begin(command, [1])
                self.assertTrue(dup.duplicate)
                self.assertFalse(dup.suppressed)  # takeover must always reach Core

    def test_sequential_idempotent_not_suppressed(self):
        led = CommandLedger()
        a = led.begin("ARM", [1])
        led.end(a, ok=True)                      # completes → clears in-flight
        b = led.begin("ARM", [1])
        self.assertFalse(b.suppressed)           # not overlapping → dispatches


class SemanticArgumentFingerprint(unittest.TestCase):
    """Regression for the reviewer-reproduced bugs: same family but different
    semantic parameters must NOT collide into one command_id."""

    def test_mode_guided_vs_loiter_not_deduped(self):
        led = CommandLedger()
        guided = led.begin("MODE GUIDED", [1])          # in flight
        loiter = led.begin("MODE LOITER", [1])          # distinct semantic arg
        self.assertNotEqual(guided.command_id, loiter.command_id)
        self.assertFalse(loiter.suppressed)

    def test_takeoff_20_vs_30_not_deduped(self):
        led = CommandLedger()
        a = led.begin("TAKEOFF 20m", [1])               # in flight
        b = led.begin("TAKEOFF 30m", [1])               # different altitude
        self.assertNotEqual(a.command_id, b.command_id)
        self.assertFalse(b.suppressed)

    def test_identical_semantic_command_still_deduped(self):
        led = CommandLedger()
        led.begin("MODE GUIDED", [1])
        dup = led.begin("MODE GUIDED", [1])             # true double-click
        self.assertTrue(dup.suppressed)

    def test_explicit_dedup_key_overrides_label(self):
        led = CommandLedger()
        a = led.begin("ARM", [1], dedup_key="ARM:force=0")
        b = led.begin("ARM", [1], dedup_key="ARM:force=1")
        self.assertNotEqual(a.command_id, b.command_id)  # force distinguishes
        self.assertFalse(b.suppressed)

    def test_ui_tablet_suffix_does_not_change_identity(self):
        # A UI-only "[tablet]" tag must not make the same command a different one.
        led = CommandLedger()
        cockpit = led.begin("TAKEOFF 20m", [1])
        tablet = led.begin("TAKEOFF 20m [tablet]", [1])
        self.assertEqual(cockpit.command_id, tablet.command_id)
        self.assertTrue(tablet.suppressed)               # genuine double-click across UIs

    def test_takeoff_same_rounded_label_different_actual_altitude(self):
        # Same displayed "20m" but different actual altitude must NOT collide when
        # the caller passes the exact argument as dedup_key.
        led = CommandLedger()
        a = led.begin("TAKEOFF 20m", [1], dedup_key="TAKEOFF|alt=20.0|confirmed=True")
        b = led.begin("TAKEOFF 20m", [1], dedup_key="TAKEOFF|alt=20.4|confirmed=True")
        self.assertNotEqual(a.command_id, b.command_id)
        self.assertFalse(b.suppressed)

    def test_arm_force_false_vs_true_do_not_collide(self):
        led = CommandLedger()
        a = led.begin("ARM", [1], dedup_key="ARM|force=False")
        b = led.begin("ARM", [1], dedup_key="ARM|force=True")
        self.assertNotEqual(a.command_id, b.command_id)
        self.assertFalse(b.suppressed)


class GatewayDedupIntegration(unittest.TestCase):
    # gateway path: MODE GUIDED in-flight then MODE LOITER must both dispatch
    def test_gateway_mode_change_not_deduped(self):
        from swarmgod_gui.core.command_gateway import CommandGateway, DedupedResult
        client = _Client()
        client.mode = lambda ids, m: client.calls.append(("mode", m)) or _Result()
        gw = CommandGateway(lambda: client)
        release = threading.Event()
        entered = threading.Event()

        def slow_guided():
            entered.set()
            release.wait(2.0)
            return client.mode([1], "GUIDED")

        t = threading.Thread(target=lambda: gw.dispatch(
            "MODE GUIDED", slow_guided, source="cockpit", targets=[1]))
        t.start()
        self.assertTrue(entered.wait(2.0))
        second = gw.dispatch("MODE LOITER", lambda: client.mode([1], "LOITER"),
                             source="cockpit", targets=[1])
        self.assertNotIsInstance(second, DedupedResult)   # distinct mode must send
        release.set()
        t.join(2.0)
        self.assertEqual(client.calls, [("mode", "LOITER"), ("mode", "GUIDED")]
                         if client.calls[0][1] == "LOITER" else
                         [("mode", "GUIDED"), ("mode", "LOITER")])

    # double-click: two overlapping idempotent dispatches → one client call
    def test_double_click_dedup_one_client_call(self):
        client = _Client()
        gw = CommandGateway(lambda: client)
        release = threading.Event()
        entered = threading.Event()

        def slow_arm():
            entered.set()
            release.wait(2.0)
            return client.arm([1])

        results = {}
        t = threading.Thread(target=lambda: results.__setitem__(
            "first", gw.dispatch("ARM", slow_arm, source="cockpit", targets=[1])))
        t.start()
        self.assertTrue(entered.wait(2.0))       # first is in flight inside invoke
        second = gw.dispatch("ARM", lambda: client.arm([1]), source="cockpit", targets=[1])
        self.assertIsInstance(second, DedupedResult)
        # A suppressed duplicate is neither success nor failure: the identical
        # command is still in progress. ok is unknown (None) + in_progress flag.
        self.assertIsNone(second.ok)
        self.assertTrue(second.in_progress)
        self.assertTrue(second.deduped)
        release.set()
        t.join(2.0)
        self.assertEqual(client.calls, [("arm", (1,))])   # exactly one real arm

    # emergency double-tap: both reach the client (never deduped)
    def test_emergency_double_tap_both_dispatch(self):
        client = _Client()
        gw = CommandGateway(lambda: client)
        release = threading.Event()
        entered = threading.Event()

        def slow_kill():
            entered.set()
            release.wait(2.0)
            return client.kill([1])

        t = threading.Thread(target=lambda: gw.dispatch(
            "KILL", slow_kill, source="cockpit", targets=[1]))
        t.start()
        self.assertTrue(entered.wait(2.0))
        second = gw.dispatch("KILL", lambda: client.kill([1]), source="cockpit", targets=[1])
        self.assertNotIsInstance(second, DedupedResult)
        release.set()
        t.join(2.0)
        self.assertEqual(len(client.calls), 2)   # emergency never suppressed

    def test_takeover_double_tap_both_dispatch(self):
        for command in ("HOLD", "LAND", "RTL", "DISARM"):
            with self.subTest(command=command):
                client = _Client()
                gw = CommandGateway(lambda: client)
                release = threading.Event()
                entered = threading.Event()
                calls = []

                def slow_takeover():
                    entered.set()
                    release.wait(2.0)
                    calls.append("first")
                    return _Result()

                t = threading.Thread(target=lambda: gw.dispatch(
                    command, slow_takeover, source="cockpit", targets=[1]))
                t.start()
                self.assertTrue(entered.wait(2.0))
                second = gw.dispatch(command, lambda: calls.append("second") or _Result(),
                                     source="cockpit", targets=[1])
                self.assertNotIsInstance(second, DedupedResult)
                release.set()
                t.join(2.0)
                self.assertEqual(sorted(calls), ["first", "second"])

    # timeout / late reply: a failed/slow attempt still clears in-flight so a
    # legitimate retry is NOT wrongly deduped
    def test_timeout_clears_inflight_so_retry_dispatches(self):
        client = _Client()
        gw = CommandGateway(lambda: client)

        def boom():
            raise TimeoutError("no ACK")

        with self.assertRaises(TimeoutError):
            gw.dispatch("ARM", boom, targets=[1])
        self.assertEqual(gw.ledger.inflight_ids(), set())   # cleared despite failure
        gw.dispatch("ARM", lambda: client.arm([1]), targets=[1])   # retry works
        self.assertEqual(client.calls, [("arm", (1,))])

    # reconnect: after completion the command_id is free; re-issuing dispatches
    def test_reconnect_reissue_dispatches(self):
        client = _Client()
        gw = CommandGateway(lambda: client)
        gw.dispatch("HOLD", lambda: _Result(), targets=[1])
        r = gw.dispatch("HOLD", lambda: _Result(), targets=[1])
        self.assertNotIsInstance(r, DedupedResult)          # no stale in-flight block

    def test_internal_orchestrator_can_disable_frontend_dedup_without_losing_observability(self):
        client = _Client()
        gw = CommandGateway(lambda: client)
        release = threading.Event()
        entered = threading.Event()

        def slow_arm():
            entered.set()
            release.wait(2.0)
            return client.arm([1])

        t = threading.Thread(target=lambda: gw.dispatch(
            "ARM [orchestrator]", slow_arm, source="orchestrator", targets=[1],
            enforce_dedup=False))
        t.start()
        self.assertTrue(entered.wait(2.0))
        second = gw.dispatch(
            "ARM [orchestrator]", lambda: client.arm([1]),
            source="orchestrator", targets=[1], enforce_dedup=False)
        self.assertNotIsInstance(second, DedupedResult)
        release.set()
        t.join(2.0)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(gw.recent()), 2)

    # stale callback: a suppressed duplicate's end() must not clear the real
    # in-flight winner's slot
    def test_suppressed_duplicate_end_does_not_clear_winner(self):
        led = CommandLedger()
        winner = led.begin("ARM", [1])
        dup = led.begin("ARM", [1])
        self.assertTrue(dup.suppressed)
        led.end(dup, ok=True)                                # stale duplicate callback
        self.assertIn(winner.command_id, led.inflight_ids()) # winner still in flight
        led.end(winner, ok=True)
        self.assertEqual(led.inflight_ids(), set())


if __name__ == "__main__":
    unittest.main()
