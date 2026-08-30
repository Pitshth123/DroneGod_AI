"""command_gateway.py — V3-S07 Part A: one observable frontend command boundary.

The gateway is a **pure pass-through** in this stage.  It records what command
was dispatched, from where, to which targets, and how long it took / whether it
succeeded — and nothing else.  It deliberately does **not** implement retry,
dedup, suppression, coalescing, reordering, fallback, queueing, or any new
timeout/safety policy.  All flight authority, no-dual-authority, and safety
rules continue to live in the Go API / command.Service / safety.Envelope layer;
this boundary never becomes a policy or authority layer.

Client resolution uses a provider so tests that swap ``win.client = FakeClient()``
after construction keep working: the gateway never captures a CoreClient
reference — it calls ``client_provider()`` fresh each dispatch.

Streaming/telemetry and channel ownership are intentionally NOT routed here; the
gateway is for operator command dispatch only.
"""
import collections
import threading
import time

from .command_correlation import (
    CommandLedger, bind_command_ticket, reset_command_ticket)

# Immutable observability record for one dispatched command.  V3-S08 adds the
# correlation identity fields (operation/command/attempt) and the duplicate/
# suppressed observation flags; the S07 pass-through fields are unchanged.
CommandRecord = collections.namedtuple(
    "CommandRecord",
    "command source targets start end duration ok error metadata "
    "operation_id command_id attempt_id command_class duplicate suppressed")


class DedupedResult:
    """Returned when an idempotent command is suppressed as an in-flight
    duplicate (double-click).

    A suppressed copy is *not* an FC success: the winning command is still in
    progress and may later fail.  ``ok`` is therefore ``None`` and callers get
    an explicit ``in_progress`` flag instead of a false-success signal.
    """

    def __init__(self, command, ticket):
        self.ok = None
        self.deduped = True
        self.in_progress = True
        self.command = command
        self.operation_id = ticket.operation_id
        self.command_id = ticket.command_id
        self.attempt_id = ticket.attempt_id
        self.message = "identical command already in progress — duplicate suppressed"


class CommandGateway:
    """Observable pass-through boundary for operator command dispatch.

    Parameters
    ----------
    client_provider : callable
        Returns the current transport client (e.g. ``lambda: self.client``).
        Resolved fresh on every dispatch so a replaced client is honoured.
    sink : callable, optional
        Called with each :class:`CommandRecord` for external logging/metrics.
        Sink exceptions are swallowed and never affect the command result.
    clock : callable, optional
        Monotonic time source (defaults to ``time.monotonic``); injectable for
        deterministic tests.
    history : int
        Bounded number of recent records kept in memory for observability/tests.
    """

    def __init__(self, client_provider, *, sink=None, clock=None, history=256,
                 ledger=None):
        if not callable(client_provider):
            raise TypeError("client_provider must be callable")
        self._client_provider = client_provider
        self._sink = sink
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._records = collections.deque(maxlen=history)
        # V3-S08 command identity / correlation / class-specific dedup. The
        # ledger keeps its own clock so the gateway's injected clock is read only
        # for the record's start/end timing.
        self.ledger = ledger if ledger is not None else CommandLedger()

    @property
    def client(self):
        """The current transport client (resolved fresh; never captured)."""
        return self._client_provider()

    def dispatch(self, command, invoke, *, source=None, targets=None, metadata=None,
                 operation_id=None, dedup_key=None, enforce_dedup=True):
        """Run ``invoke()`` and record it; return its result unchanged.

        ``invoke`` is a zero-argument thunk that performs exactly one client
        call (e.g. ``lambda: self.client.arm(ids)``).  The result object is
        returned verbatim and exceptions propagate unchanged after being
        recorded — no retry, no swallowing, no reordering.

        V3-S08: the dispatch is correlated through the ledger.  An *idempotent*
        command whose identical command_id is already in flight is suppressed
        (double-click dedup) and returns a :class:`DedupedResult` without calling
        the client.  Every other command class always dispatches, so behaviour
        for distinct / replace-current / cancel-current / emergency commands is
        exactly the S07 pass-through.
        """
        start = self._clock()
        ticket = self.ledger.begin(
            command, targets or (), operation_id, dedup_key=dedup_key,
            enforce_dedup=enforce_dedup)
        if ticket.suppressed:
            # Suppression means "the identical winner is still in progress", not
            # "the FC accepted this copy".  Keep ok unknown all the way through
            # the ledger/record so UI and metrics cannot manufacture success.
            self.ledger.end(ticket, ok=None)
            self._emit(self._record(command, source, targets, ticket, start,
                                    self._clock(), None, None, metadata))
            return DedupedResult(command, ticket)

        ok = None
        error = None
        token = bind_command_ticket(ticket)
        try:
            result = invoke()
            ok = True if result is None else bool(getattr(result, "ok", True))
            return result
        except BaseException as e:  # record, then re-raise unchanged
            error = e
            raise
        finally:
            # The transport correlation context is valid only for this one
            # dispatch attempt.  Reset before callbacks/records can trigger any
            # unrelated RPC on the same worker thread.
            reset_command_ticket(token)
            end = self._clock()
            self.ledger.end(ticket, ok=ok, error=(repr(error) if error is not None else None))
            self._emit(self._record(command, source, targets, ticket, start,
                                    end, ok, error, metadata))

    def _record(self, command, source, targets, ticket, start, end, ok, error, metadata):
        return CommandRecord(
            command=str(command),
            source=source,
            targets=tuple(int(t) for t in (targets or ())),
            start=start,
            end=end,
            duration=end - start,
            ok=ok,
            error=(repr(error) if error is not None else None),
            metadata=dict(metadata or {}),
            operation_id=ticket.operation_id,
            command_id=ticket.command_id,
            attempt_id=ticket.attempt_id,
            command_class=ticket.klass.value,
            duplicate=ticket.duplicate,
            suppressed=ticket.suppressed,
        )

    def call(self, command, method, *args, source=None, targets=None,
             metadata=None, operation_id=None, dedup_key=None,
             enforce_dedup=True, **kwargs):
        """Convenience: resolve the current client and dispatch ``method(*args)``.

        Equivalent to ``dispatch(command, lambda: getattr(client, method)(...))``
        with the client resolved through the provider.  Used when migrating a
        direct ``self.client.<method>(...)`` call site behind the gateway.
        """
        client = self._client_provider()
        return self.dispatch(
            command, lambda: getattr(client, method)(*args, **kwargs),
            source=source, targets=targets, metadata=metadata,
            operation_id=operation_id, dedup_key=dedup_key,
            enforce_dedup=enforce_dedup)

    def _emit(self, record):
        with self._lock:
            self._records.append(record)
        if self._sink is not None:
            try:
                self._sink(record)
            except Exception:
                pass  # observability must never break command dispatch

    def recent(self, n=None):
        """Snapshot of recent command records (oldest→newest)."""
        with self._lock:
            items = list(self._records)
        return items if n is None else items[-n:]

    def last(self):
        """Most recent record, or ``None``."""
        with self._lock:
            return self._records[-1] if self._records else None
