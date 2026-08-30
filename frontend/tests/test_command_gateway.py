"""V3-S07 Part A — CommandGateway pass-through + observability tests.

Prove the gateway changes NO command semantics: exact arguments, exact return
object, exact exception propagation, exactly one client call per action, no
retry, and correct behaviour when the client is swapped after construction
(provider pattern).  Also prove it is not an authority/policy layer.
"""
import os
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core.command_gateway import CommandGateway, CommandRecord  # noqa: E402


class _Result:
    def __init__(self, ok=True, message=""):
        self.ok = ok
        self.message = message


class _Client:
    def __init__(self, name="real"):
        self.name = name
        self.calls = []
        self.ret = _Result(ok=True, message="done")

    def arm(self, ids, force=False):
        self.calls.append(("arm", tuple(ids), force))
        return self.ret

    def boom(self):
        self.calls.append(("boom",))
        raise RuntimeError("kaboom")


class GatewayPassThrough(unittest.TestCase):

    def test_requires_callable_provider(self):
        with self.assertRaises(TypeError):
            CommandGateway(client_provider=object())

    def test_returns_exact_result_object(self):
        c = _Client()
        gw = CommandGateway(lambda: c)
        r = gw.dispatch("ARM", lambda: c.arm([1, 2]), source="cockpit", targets=[1, 2])
        self.assertIs(r, c.ret)                       # identical object, not a copy
        self.assertEqual(c.calls, [("arm", (1, 2), False)])

    def test_exactly_one_call_no_retry(self):
        c = _Client()
        gw = CommandGateway(lambda: c)
        gw.dispatch("ARM", lambda: c.arm([1]), targets=[1])
        self.assertEqual(len(c.calls), 1)             # one action → one client call

    def test_exception_propagates_unchanged_and_is_recorded(self):
        c = _Client()
        gw = CommandGateway(lambda: c)
        with self.assertRaises(RuntimeError) as ctx:
            gw.dispatch("BOOM", lambda: c.boom(), source="cockpit")
        self.assertEqual(str(ctx.exception), "kaboom")
        self.assertEqual(len(c.calls), 1)             # no retry after failure
        rec = gw.last()
        self.assertFalse(rec.ok is True)              # not marked success
        self.assertIn("kaboom", rec.error)

    def test_provider_uses_replaced_client(self):
        real = _Client("real")
        holder = {"client": real}
        gw = CommandGateway(lambda: holder["client"])
        fake = _Client("fake")
        holder["client"] = fake                       # swap like win.client = FakeClient()
        self.assertIs(gw.client, fake)
        gw.call("ARM", "arm", [7], source="card", targets=[7])
        self.assertEqual(fake.calls, [("arm", (7,), False)])
        self.assertEqual(real.calls, [])              # original client never touched

    def test_records_observability_fields(self):
        ticks = iter([100.0, 100.25])
        c = _Client()
        gw = CommandGateway(lambda: c, clock=lambda: next(ticks))
        gw.dispatch("ARM", lambda: c.arm([3]), source="cockpit", targets=[3],
                    metadata={"note": "x"})
        rec = gw.last()
        self.assertIsInstance(rec, CommandRecord)
        self.assertEqual(rec.command, "ARM")
        self.assertEqual(rec.source, "cockpit")
        self.assertEqual(rec.targets, (3,))
        self.assertEqual(rec.duration, 0.25)
        self.assertTrue(rec.ok)
        self.assertIsNone(rec.error)
        self.assertEqual(rec.metadata, {"note": "x"})

    def test_ok_reflects_result_ok_flag(self):
        c = _Client()
        c.ret = _Result(ok=False, message="rejected")
        gw = CommandGateway(lambda: c)
        gw.dispatch("GOTO", lambda: c.arm([1]), targets=[1])
        self.assertFalse(gw.last().ok)                # gateway observes, does not override

    def test_sink_exception_never_breaks_dispatch(self):
        c = _Client()

        def bad_sink(_rec):
            raise ValueError("sink blew up")

        gw = CommandGateway(lambda: c, sink=bad_sink)
        r = gw.dispatch("ARM", lambda: c.arm([1]), targets=[1])
        self.assertIs(r, c.ret)                       # result still returned

    def test_metadata_hooks_do_not_alter_result(self):
        c = _Client()
        seen = []
        gw = CommandGateway(lambda: c, sink=seen.append)
        r = gw.dispatch("ARM", lambda: c.arm([1]), targets=[1])
        self.assertIs(r, c.ret)
        self.assertEqual(len(seen), 1)

    def test_thread_safe_concurrent_dispatch(self):
        c = _Client()
        lock = threading.Lock()

        def invoke():
            with lock:                                # serialize the fake's list append
                return c.arm([1])

        gw = CommandGateway(lambda: c)
        threads = [threading.Thread(target=lambda: gw.dispatch(
            "ARM", invoke, targets=[1])) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(c.calls), 20)
        self.assertEqual(len(gw.recent()), 20)        # every dispatch recorded

    def test_not_an_authority_layer(self):
        # The gateway has no method that inspects/blocks/authorizes commands.
        gw = CommandGateway(lambda: _Client())
        for forbidden in ("authorize", "allow", "block", "retry", "dedup",
                          "suppress", "reorder", "queue"):
            self.assertFalse(hasattr(gw, forbidden),
                             f"gateway must not expose policy method {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
