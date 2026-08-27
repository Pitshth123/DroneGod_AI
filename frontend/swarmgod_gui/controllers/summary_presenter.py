"""Presentation-only orchestration for the pre-flight/execution summary.

This controller deliberately has no command client and owns no flight state.  It
translates legacy summary keys into stable plan rows, forwards audit events to
the mission log, and renders the already-decided plan/run state.
"""


class SummaryPresenter:
    """Adapter between the plan model, summary widget, and mission log."""

    PLAN_KEYS = {
        "sel": ("target", "TARGET"),
        "head": ("head", "HEAD"),
        "to_mode": ("takeoff", "TAKEOFF"),
        "takeoff_plan": ("takeoff", "TAKEOFF"),
        "swarm": ("swarm", "SWARM"),
        "wp_route": ("waypoint", "WAYPOINT"),
        "wp_actions": ("payload", "PAYLOAD A/B"),
        "wp_wait": ("wait", "WAIT"),
        "wave": ("wave", "WAVE"),
        "rtlcfg": ("return_plan", "RETURN PLAN"),
        "fence": ("geofence", "GEOFENCE"),
    }

    def __init__(self, plan, *, request_render, log_command, summary_box,
                 record_render=None):
        self._plan = plan
        self._request_render = request_render
        self._log_command = log_command
        self._summary_box = summary_box
        self._record_render = record_render

    def set(self, legacy_key, _legacy_label, value, *, render=True):
        """Set one meaningful plan row; transient legacy rows are ignored."""
        mapping = self.PLAN_KEYS.get(legacy_key)
        if mapping is None:
            return False
        key, label = mapping
        self._plan.set(key, label, value)
        if render:
            self._request_render()
        return True

    def remove(self, legacy_key, *, render=True):
        mapping = self.PLAN_KEYS.get(legacy_key)
        if mapping is None:
            return False
        self._plan.remove(mapping[0])
        if render:
            self._request_render()
        return True

    def event(self, label, value, *, cancelled=False):
        """Send command history to the audit log, never to the flight plan."""
        self._log_command(
            "%s: %s" % (label, value),
            "WARNING" if cancelled else "INFO",
        )

    def render(self, run):
        """Render current model state without deciding or changing flight state."""
        if self._record_render is not None:
            self._record_render()
        box = self._summary_box()
        if box is not None:
            box.render_rows(self._plan.rows())
            box.render_timeline(run)

    def snapshot(self):
        """Return a plain plan snapshot for a new immutable FlightRun."""
        return {label: value for label, value in self._plan.rows()}

