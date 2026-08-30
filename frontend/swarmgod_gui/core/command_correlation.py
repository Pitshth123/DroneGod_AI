"""command_correlation.py — V3-S08: command identity, correlation, class dedup.

S07 gave us one observable command boundary (the CommandGateway).  S08 adds the
system-wide identity model on top of it:

    operator_operation_id            (one operator intent)
            ↓
    command_id / attempt_id          (one logical command / one wire attempt)
            ↓
    request_id                       (existing per-call UUID → Core idempotency)
            ↓
    Core audit/result

Responsibilities, in the order the roadmap requires them:

1. **Correlation only — no behaviour change.**  Every dispatch is tagged with an
   operation_id, a stable command_id, and a unique attempt_id, and recorded.
2. **Observe duplicate candidates.**  A command_id already in flight (or seen
   again immediately) is flagged as a duplicate — observation, not action.
3. **Classify command semantics.**  Each command family maps to a class:
   idempotent / takeover / non-idempotent / replace-current / cancel-current / emergency.
4. **Enforce dedup only per proven class.**  Only an *idempotent* command whose
   command_id is already in flight is suppressed (the classic double-click).
   Takeover, replace-current, cancel-current, emergency, and non-idempotent
   commands are NEVER suppressed — a takeover/emergency must always reach Core.

The ledger is transport-free and Qt-free; it decides nothing about flight
authority or safety.  Suppression only avoids a redundant *second copy* of an
idempotent command that is already being sent.
"""
import collections
import contextvars
import enum
import re
import threading
import time
import uuid

# UI-only presentation suffixes (e.g. " [tablet]", " [remote]") must NOT define
# flight semantics: the same command from the cockpit and the tablet is the same
# command. Strip a trailing bracketed tag before fingerprinting.
_UI_SUFFIX_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def semantic_label(command):
    """Normalized semantic label for fingerprinting: drop trailing UI-only tags
    like ``[tablet]`` and collapse whitespace/case. This is the default dedup key
    when the caller does not pass an explicit ``dedup_key`` with actual args."""
    c = _UI_SUFFIX_RE.sub("", (command or "").strip())
    return " ".join(c.upper().split())


# Per-dispatch correlation context.  CommandGateway binds the current ticket only
# around the synchronous CoreClient call; the gRPC client interceptor reads it
# and emits the IDs as wire metadata.  contextvars keeps concurrent worker
# threads/tasks isolated and makes the binding nest-safe in tests.
_CURRENT_COMMAND_TICKET = contextvars.ContextVar(
    "swarmgod_current_command_ticket", default=None)


def bind_command_ticket(ticket):
    """Bind ``ticket`` for the current dispatch and return a reset token."""
    return _CURRENT_COMMAND_TICKET.set(ticket)


def reset_command_ticket(token):
    """Restore the previous dispatch correlation context."""
    _CURRENT_COMMAND_TICKET.reset(token)


def current_command_correlation():
    """Return the currently bound wire correlation IDs, or ``None``.

    Kept deliberately transport-neutral; ``grpc_client`` decides how to encode
    these fields on the wire.
    """
    ticket = _CURRENT_COMMAND_TICKET.get()
    if ticket is None:
        return None
    return {
        "operation_id": str(ticket.operation_id),
        "command_id": str(ticket.command_id),
        "attempt_id": str(ticket.attempt_id),
    }


class CommandClass(enum.Enum):
    IDEMPOTENT = "idempotent"            # ARM/TAKEOFF/MODE/EQUALIZE — safe double-click suppression
    TAKEOVER = "takeover"                # DISARM/LAND/RTL/HOLD — must always reach Core
    REPLACE_CURRENT = "replace_current"  # GOTO / CHANGE ALT — a new one supersedes
    CANCEL_CURRENT = "cancel_current"    # STOP ALL / CANCEL — cancels the current activity
    EMERGENCY = "emergency"              # KILL / E-STOP — never deduped/suppressed
    NON_IDEMPOTENT = "non_idempotent"    # SERVO / unknown — no dedup enforcement


# Longest/most specific prefixes first.  Matching is on the normalized command
# label the gateway already passes (e.g. "MODE GUIDED", "GO ALT 20m").
_PREFIX_TABLE = (
    ("E-STOP", CommandClass.EMERGENCY),
    ("ESTOP", CommandClass.EMERGENCY),
    ("KILL", CommandClass.EMERGENCY),
    ("STOP ALL", CommandClass.CANCEL_CURRENT),
    ("STOPALL", CommandClass.CANCEL_CURRENT),
    ("CANCEL", CommandClass.CANCEL_CURRENT),
    ("GO ALT", CommandClass.REPLACE_CURRENT),
    ("GOALT", CommandClass.REPLACE_CURRENT),
    ("CHANGE ALT", CommandClass.REPLACE_CURRENT),
    ("CHANGEALT", CommandClass.REPLACE_CURRENT),
    ("GOTO", CommandClass.REPLACE_CURRENT),
    ("RC", CommandClass.REPLACE_CURRENT),
    ("SERVO", CommandClass.NON_IDEMPOTENT),
    ("ARM", CommandClass.IDEMPOTENT),
    ("DISARM", CommandClass.TAKEOVER),
    ("LAND", CommandClass.TAKEOVER),
    ("RTL", CommandClass.TAKEOVER),
    ("HOLD", CommandClass.TAKEOVER),
    ("TAKEOFF", CommandClass.IDEMPOTENT),
    ("MODE", CommandClass.IDEMPOTENT),
    ("EQUALIZE", CommandClass.IDEMPOTENT),
)


def command_family(command):
    """Normalized family key used for classification and command_id stability."""
    c = (command or "").strip().upper()
    for prefix, _ in _PREFIX_TABLE:
        if c == prefix or c.startswith(prefix + " ") or c.startswith(prefix):
            return prefix
    return c or "UNKNOWN"


def classify(command):
    """Return the :class:`CommandClass` for a command label."""
    c = (command or "").strip().upper()
    for prefix, klass in _PREFIX_TABLE:
        if c == prefix or c.startswith(prefix + " ") or c.startswith(prefix):
            return klass
    return CommandClass.NON_IDEMPOTENT


def _command_id(fingerprint, targets):
    tset = ",".join(str(int(t)) for t in sorted(targets or ()))
    return f"{fingerprint}#{tset}"


class CommandTicket:
    """One dispatch attempt's identity + correlation + dedup decision."""

    __slots__ = ("operation_id", "command", "family", "klass", "command_id",
                 "attempt_id", "attempt_index", "targets", "duplicate",
                 "suppressed", "start", "end", "ok", "error")

    def __init__(self, operation_id, command, family, klass, command_id,
                 attempt_index, targets, duplicate, suppressed, start):
        self.operation_id = operation_id
        self.command = command
        self.family = family
        self.klass = klass
        self.command_id = command_id
        self.attempt_id = uuid.uuid4().hex
        self.attempt_index = attempt_index
        self.targets = tuple(int(t) for t in (targets or ()))
        self.duplicate = duplicate
        self.suppressed = suppressed
        self.start = start
        self.end = None
        self.ok = None
        self.error = None


class CommandLedger:
    """Thread-safe command identity / correlation / duplicate-observation store.

    ``begin`` returns a :class:`CommandTicket`; the caller runs the command
    unless ``ticket.suppressed`` is set, then calls ``end``.
    """

    def __init__(self, clock=None, retain=256):
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._inflight = {}                     # command_id -> CommandTicket (winner)
        self._counts = collections.Counter()    # command_id -> attempts seen
        self._recent = collections.deque(maxlen=retain)

    def begin(self, command, targets=(), operation_id=None, dedup_key=None,
              enforce_dedup=True):
        # The dedup fingerprint MUST include the command's semantic arguments, not
        # just its family — otherwise MODE GUIDED vs MODE LOITER, or TAKEOFF 20m
        # vs TAKEOFF 30m, would collide and the second (distinct) command would be
        # wrongly suppressed.  Default to the full normalized command label (which
        # already carries the args, e.g. "TAKEOFF 20M"); callers may pass an
        # explicit dedup_key when the label does not fully capture the semantics.
        family = command_family(command)
        klass = classify(command)
        # Prefer an explicit dedup_key (actual semantic args: force/altitude/mode/
        # confirmed). Otherwise fall back to the normalized semantic label with
        # UI-only suffixes stripped.
        fingerprint = dedup_key if dedup_key is not None else semantic_label(command)
        cid = _command_id(fingerprint, targets)
        with self._lock:
            self._counts[cid] += 1
            attempt_index = self._counts[cid]
            inflight = self._inflight.get(cid)
            duplicate = inflight is not None
            # Class-specific dedup: ONLY suppress an idempotent duplicate that is
            # already in flight. Every other class always dispatches.
            suppressed = bool(enforce_dedup) and duplicate and klass == CommandClass.IDEMPOTENT
            ticket = CommandTicket(
                operation_id or uuid.uuid4().hex, command, family, klass, cid,
                attempt_index, targets, duplicate, suppressed, self._clock())
            if not suppressed:
                self._inflight[cid] = ticket    # this attempt owns the in-flight slot
            self._recent.append(ticket)
            return ticket

    def end(self, ticket, ok=None, error=None):
        if ticket is None:
            return
        with self._lock:
            ticket.ok = ok
            ticket.error = error
            ticket.end = self._clock()
            # Only the in-flight winner clears the slot (a suppressed duplicate
            # must never clear the real attempt still in flight).
            if self._inflight.get(ticket.command_id) is ticket:
                del self._inflight[ticket.command_id]

    # ── observation ──
    def inflight_ids(self):
        with self._lock:
            return set(self._inflight)

    def recent(self, n=None):
        with self._lock:
            items = list(self._recent)
        return items if n is None else items[-n:]

    def duplicates(self):
        with self._lock:
            return [t for t in self._recent if t.duplicate]
