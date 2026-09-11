"""V3-S08 wire-correlation and gateway coverage regressions."""
import ast
import os
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core.command_correlation import (  # noqa: E402
    CommandLedger, bind_command_ticket, reset_command_ticket)
from swarmgod_gui.core.grpc_client import (  # noqa: E402
    _ClientCallDetails, _CommandCorrelationInterceptor)


class WireCorrelation(unittest.TestCase):
    def _details(self, metadata=()):
        return _ClientCallDetails(
            "/swarmgod.v1.SwarmGodService/Arm", 5.0, list(metadata),
            None, None, None)

    def test_interceptor_attaches_exact_bound_ids_and_replaces_stale_values(self):
        ticket = CommandLedger().begin("ARM", [1], operation_id="op-exact")
        token = bind_command_ticket(ticket)
        try:
            seen = {}

            def continuation(details, request):
                seen["metadata"] = list(details.metadata or ())
                seen["request"] = request
                return "sentinel"

            request = object()
            result = _CommandCorrelationInterceptor().intercept_unary_unary(
                continuation,
                self._details([
                    ("existing", "keep"),
                    ("x-swarmgod-operation-id", "stale"),
                    ("x-swarmgod-command-id", "stale"),
                    ("x-swarmgod-attempt-id", "stale"),
                ]),
                request,
            )
        finally:
            reset_command_ticket(token)

        self.assertEqual(result, "sentinel")
        self.assertIs(seen["request"], request)
        md = dict(seen["metadata"])
        self.assertEqual(md["existing"], "keep")
        self.assertEqual(md["x-swarmgod-operation-id"], ticket.operation_id)
        self.assertEqual(md["x-swarmgod-command-id"], ticket.command_id)
        self.assertEqual(md["x-swarmgod-attempt-id"], ticket.attempt_id)
        self.assertEqual(sum(k == "x-swarmgod-operation-id" for k, _ in seen["metadata"]), 1)

    def test_interceptor_without_gateway_context_is_noop(self):
        details = self._details([("existing", "keep")])
        seen = {}

        def continuation(got, request):
            seen["details"] = got
            return request

        request = object()
        result = _CommandCorrelationInterceptor().intercept_unary_unary(
            continuation, details, request)
        self.assertIs(result, request)
        self.assertIs(seen["details"], details)

    def test_outside_gateway_strips_stale_reserved_headers(self):
        details = self._details([
            ("existing", "keep"),
            ("x-swarmgod-operation-id", "stale-op"),
            ("x-swarmgod-command-id", "stale-command"),
            ("x-swarmgod-attempt-id", "stale-attempt"),
            ("X-SwarmGod-Attempt-Id", "stale-case-variant"),
        ])
        seen = {}

        def continuation(got, request):
            seen["metadata"] = list(got.metadata or ())
            return request

        request = object()
        result = _CommandCorrelationInterceptor().intercept_unary_unary(
            continuation, details, request)
        self.assertIs(result, request)
        self.assertEqual(seen["metadata"], [("existing", "keep")])

    def test_context_is_isolated_between_concurrent_threads(self):
        barrier = threading.Barrier(2)
        seen = {}
        lock = threading.Lock()

        def run(name):
            ticket = CommandLedger().begin("ARM", [1], operation_id="op-" + name)
            token = bind_command_ticket(ticket)
            try:
                barrier.wait(timeout=2)

                def continuation(details, request):
                    with lock:
                        seen[name] = dict(details.metadata or ())
                    return request

                _CommandCorrelationInterceptor().intercept_unary_unary(
                    continuation, self._details(), object())
            finally:
                reset_command_ticket(token)

        threads = [threading.Thread(target=run, args=(name,)) for name in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        self.assertEqual(seen["A"]["x-swarmgod-operation-id"], "op-A")
        self.assertEqual(seen["B"]["x-swarmgod-operation-id"], "op-B")
        self.assertNotEqual(
            seen["A"]["x-swarmgod-attempt-id"],
            seen["B"]["x-swarmgod-attempt-id"])


class SystemWideGatewayCoverage(unittest.TestCase):
    MUTATING_CLIENT_METHODS = {
        "arm", "disarm", "kill", "takeoff", "land", "rtl", "hold",
        "set_mode", "goto", "rc_move", "stop_all", "change_alt",
        "servo_set", "servo_release", "servo_reset",
        "swarm_start", "swarm_stop", "swarm_return", "swarm_config",
        "swarm_take_control", "swarm_rejoin",
        "set_leader", "set_geofence", "param_set",
        "start_mission", "cancel_mission",
    }
    ALLOWED_BOUNDARIES = {"_dispatch_core", "_run_cmd", "_card_cmd"}
    NON_MUTATING_CORECLIENT_METHODS = {
        "close", "connect_drone", "disconnect_drone", "swarm_state",
        "get_mission_state",
    }

    def test_mutating_inventory_tracks_every_public_coreclient_method(self):
        """A newly added CoreClient mutation cannot silently evade this scan."""
        path = os.path.join(
            _FRONTEND, "swarmgod_gui", "core", "grpc_client.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        core = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "CoreClient")
        public = {
            node.name for node in core.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        discovered_mutations = public - self.NON_MUTATING_CORECLIENT_METHODS
        self.assertEqual(
            self.MUTATING_CLIENT_METHODS, discovered_mutations,
            "classify every new public CoreClient method as mutating or explicitly read/lifecycle")

    @staticmethod
    def _self_attr_call_name(call):
        f = getattr(call, "func", None)
        if not isinstance(f, ast.Attribute):
            return None
        owner = f.value
        if not (isinstance(owner, ast.Attribute)
                and owner.attr == "client"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"):
            return None
        return f.attr

    @staticmethod
    def _boundary_name(call):
        f = getattr(call, "func", None)
        if (isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "self"):
            return f.attr
        return None

    def test_every_mutating_app_coreclient_call_is_under_gateway_boundary(self):
        app_path = os.path.join(_FRONTEND, "swarmgod_gui", "app.py")
        with open(app_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=app_path)
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        unwrapped = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method = self._self_attr_call_name(node)
            if method not in self.MUTATING_CLIENT_METHODS:
                continue
            cur = parents.get(node)
            wrapped = False
            while cur is not None:
                if isinstance(cur, ast.Call) and self._boundary_name(cur) in self.ALLOWED_BOUNDARIES:
                    wrapped = True
                    break
                cur = parents.get(cur)
            if not wrapped:
                unwrapped.append((getattr(node, "lineno", -1), method))

        self.assertEqual(
            unwrapped, [],
            "flight/control-mutating CoreClient calls bypass CommandGateway: %r" % unwrapped)

    def test_indirect_mission_mutations_use_gateway_helper(self):
        """Catch getattr aliases that the literal self.client AST scan cannot.

        mission_shadow intentionally resolves optional RPCs through ``getattr``;
        every invocation of its mutating aliases must be nested under the one
        helper that routes GroundStation through ``_dispatch_core``.
        """
        path = os.path.join(
            _FRONTEND, "swarmgod_gui", "core", "mission_shadow.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        unwrapped = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"start_call", "cancel_call"}):
                continue
            cur = parents.get(node)
            while cur is not None:
                if (isinstance(cur, ast.Call)
                        and isinstance(cur.func, ast.Name)
                        and cur.func.id == "_dispatch_mutation"):
                    break
                cur = parents.get(cur)
            if cur is None:
                unwrapped.append((node.lineno, node.func.id))
        self.assertEqual(unwrapped, [], "mission mutation bypasses gateway helper: %r" % unwrapped)

    def test_preflight_mutations_use_supplied_dispatch_boundary(self):
        path = os.path.join(
            _FRONTEND, "swarmgod_gui", "widgets", "preflight_dialog.py")
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        unwrapped = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "cl"
                    and node.func.attr in self.MUTATING_CLIENT_METHODS):
                continue
            cur = parents.get(node)
            while cur is not None:
                if (isinstance(cur, ast.Call)
                        and isinstance(cur.func, ast.Attribute)
                        and cur.func.attr == "_dispatch_live"):
                    break
                cur = parents.get(cur)
            if cur is None:
                unwrapped.append((node.lineno, node.func.attr))
        self.assertEqual(unwrapped, [], "preflight mutation bypasses supplied gateway: %r" % unwrapped)


if __name__ == "__main__":
    unittest.main()
