"""V1 Phase 3 — pure frontend TelemetryStore/read-model tests."""
from types import SimpleNamespace

from swarmgod_gui.core.telemetry_store import TelemetryRenderGate, TelemetryStore


class FakeClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += float(seconds)


def telem(drone_id=1, **overrides):
    position = overrides.pop("position", SimpleNamespace(
        lat=14.0, lon=100.0, alt_rel=20.0, alt_abs=250.0))
    velocity = overrides.pop("velocity", SimpleNamespace(x=1.0, y=2.0, z=-0.5))
    values = dict(
        drone_id=drone_id,
        name=f"Drone {drone_id}",
        status=7,
        mode=5,
        armed=True,
        position=position,
        velocity=velocity,
        ground_speed=3.2,
        heading=91.0,
        roll=1.0,
        pitch=2.0,
        battery_pct=77.0,
        voltage=15.8,
        current=4.1,
        gps_fix=3,
        sat_count=14,
        total_distance=123.0,
        flight_time=45.0,
        connect_elapsed=60.0,
        telemetry_verified=True,
        host="10.0.0.1",
        port=5760,
        timestamp_ms=123456,
        rssi=-63,
        rssi_valid=True,
        link_quality=92,
        drop_rate=1,
        servo_ch7_pwm=1100,
        servo_ch8_pwm=1900,
        servo_valid=True,
        rc_ch7_raw=1200,
        rc_ch8_raw=1800,
        rc_valid=True,
        ovr_ch7=False,
        ovr_ch8=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_update_freezes_primitive_snapshot_from_mutable_message():
    clk = FakeClock(10.0)
    store = TelemetryStore(clock=clk)
    msg = telem()
    view = store.update(msg)

    assert view.drone_id == 1
    assert view.position.lat == 14.0
    assert view.position.alt_rel == 20.0
    assert view.battery_pct == 77.0
    assert view.received_at == 10.0

    # Mutating the source object after ingest must not mutate the read model.
    msg.position.alt_rel = 99.0
    msg.battery_pct = 2.0
    assert store.get(1).position.alt_rel == 20.0
    assert store.get(1).battery_pct == 77.0


def test_latest_update_replaces_only_that_drone():
    store = TelemetryStore(clock=FakeClock())
    store.update(telem(1, battery_pct=80.0))
    store.update(telem(2, battery_pct=60.0))
    store.update(telem(1, battery_pct=79.0))

    snap = store.snapshot()
    assert set(snap) == {1, 2}
    assert snap[1].battery_pct == 79.0
    assert snap[2].battery_pct == 60.0


def test_snapshot_mapping_is_detached_from_internal_mapping():
    store = TelemetryStore(clock=FakeClock())
    store.update(telem(1))
    snap = store.snapshot()
    snap.clear()
    assert store.get(1) is not None


def test_forget_removes_latest_and_per_drone_counter():
    store = TelemetryStore(clock=FakeClock())
    store.update(telem(3))
    store.update(telem(3))
    store.forget(3)
    assert store.get(3) is None
    assert 3 not in store.stats()["per_drone_total"]


def test_ingest_stats_and_rate_are_measurable_without_waiting_real_time():
    clk = FakeClock()
    store = TelemetryStore(clock=clk)
    for _ in range(5):
        store.update(telem(1))
    clk.advance(1.0)
    assert store.ingest_rate() == 5.0
    assert store.stats()["ingest_total"] == 5
    assert store.stats()["per_drone_total"][1] == 5


def test_shadow_diff_matches_legacy_message_when_values_are_identical():
    store = TelemetryStore(clock=FakeClock())
    msg = telem(4)
    store.update(msg)
    assert store.diff_message(msg) == ()
    assert store.mismatches_against({4: msg}) == {}


def test_shadow_diff_names_changed_fields():
    store = TelemetryStore(clock=FakeClock())
    original = telem(5, battery_pct=70.0)
    store.update(original)
    changed = telem(5, battery_pct=42.0)
    diff = store.diff_message(changed)
    assert "battery_pct" in diff


def test_missing_optional_nested_objects_become_safe_zero_views():
    store = TelemetryStore(clock=FakeClock())
    msg = telem(6, position=None, velocity=None)
    view = store.update(msg)
    assert view.position.lat == 0.0
    assert view.position.alt_rel == 0.0
    assert view.velocity.x == 0.0


def test_render_gate_coalesces_only_continuous_burst_inside_interval():
    clk = FakeClock()
    store = TelemetryStore(clock=clk)
    gate = TelemetryRenderGate(clock=clk, min_interval_s=0.1)

    first = store.update(telem(1, heading=10.0))
    assert gate.should_render(first)

    # Only continuous fields changed and no render interval elapsed yet.
    clk.advance(0.02)
    burst = store.update(telem(1, heading=11.0, ground_speed=3.3,
                               position=SimpleNamespace(lat=14.00001, lon=100.0,
                                                        alt_rel=20.2, alt_abs=250.2)))
    assert not gate.should_render(burst)

    clk.advance(0.08)
    due = store.update(telem(1, heading=12.0))
    assert gate.should_render(due)
    assert gate.stats() == {"rendered": 2, "skipped": 1}


def test_render_gate_renders_discrete_status_or_battery_change_immediately():
    clk = FakeClock()
    store = TelemetryStore(clock=clk)
    gate = TelemetryRenderGate(clock=clk, min_interval_s=1.0)
    assert gate.should_render(store.update(telem(1, battery_pct=77.0, mode=5)))

    clk.advance(0.01)
    assert gate.should_render(store.update(telem(1, battery_pct=50.0, mode=5)))
    clk.advance(0.01)
    assert gate.should_render(store.update(telem(1, battery_pct=50.0, mode=7)))


def test_render_gate_forget_makes_next_packet_render_immediately():
    clk = FakeClock()
    store = TelemetryStore(clock=clk)
    gate = TelemetryRenderGate(clock=clk, min_interval_s=10.0)
    assert gate.should_render(store.update(telem(9)))
    assert not gate.should_render(store.update(telem(9, heading=95.0)))
    gate.forget(9)
    assert gate.should_render(store.update(telem(9, heading=96.0)))


def test_twenty_drone_backlog_burst_collapses_repaints_without_dropping_ingest():
    clk = FakeClock()
    store = TelemetryStore(clock=clk)
    gate = TelemetryRenderGate(clock=clk, min_interval_s=0.1)

    for did in range(1, 21):
        for seq in range(10):
            view = store.update(telem(did, heading=float(seq)))
            gate.should_render(view)

    # Every packet is retained by the ingest path, but a queued burst paints one
    # latest frame per drone instead of repainting the same widget ten times.
    assert store.stats()["ingest_total"] == 200
    assert gate.stats() == {"rendered": 20, "skipped": 180}

    clk.advance(0.1)
    for did in range(1, 21):
        assert gate.should_render(store.update(telem(did, heading=99.0)))
    assert store.stats()["ingest_total"] == 220
    assert gate.stats()["rendered"] == 40


def test_store_module_has_no_flight_authority_dependencies_or_methods():
    import swarmgod_gui.core.telemetry_store as mod

    with open(mod.__file__, "r", encoding="utf-8") as f:
        text = f.read()
    assert "import PyQt" not in text
    assert "from PyQt" not in text
    assert "import grpc_client" not in text
    assert "from .grpc_client" not in text
    assert "CoreClient(" not in text
    for token in (".takeoff(", ".goto(", ".rtl(", ".hold(", ".land(", ".stop_all("):
        assert token not in text

    store = TelemetryStore()
    for name in ("takeoff", "goto", "rtl", "hold", "land", "stop_all", "arm"):
        assert not hasattr(store, name)
