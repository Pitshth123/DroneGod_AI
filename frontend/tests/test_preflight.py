"""
tests สำหรับ "ทดสอบก่อนบินจริง" (PRE-FLIGHT)

รัน headless:
    cd frontend
    QT_QPA_PLATFORM=offscreen SWARMGOD_NO_MAP=1 python -m unittest tests.test_preflight -v

ครอบคลุม:
  1) core/preflight.py — เกณฑ์ pass/fail/warn ของแต่ละข้อ + สรุปผล
  2) PreflightState    — ready / missing / หมดอายุ / ป้ายบน topbar
  3) เช็คลิสต์ของ Codex — parse docs/REAL_FLIGHT_CHECKLIST.md + จำสถานะติ๊ก
  4) PreflightDialog   — รันจริงกับ fake client (รวมกรณี FC ไม่ตอบรับโหมด / bench)
  5) ด่านก่อน TAKEOFF  — ถามก่อน · ยกเลิกแล้วต้องไม่มีคำสั่งออกไป · เทสแล้วบินต่อ
"""
import os
import sys
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SWARMGOD_NO_MAP", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from PyQt5.QtWidgets import QApplication            # noqa: E402

from swarmgod_gui.core import preflight as pf       # noqa: E402
from swarmgod_gui.core.theme import T              # noqa: E402
from swarmgod_gui.widgets import preflight_dialog as pfd   # noqa: E402
from swarmgod_gui.app import GroundStation          # noqa: E402
from swarmgod_gui.widgets.fleet_item import FleetItem      # noqa: E402

_app = QApplication.instance() or QApplication([])


def _drone(did=1, **over):
    d = {"id": did, "name": f"Drone {did}", "age_s": 0.4, "link_quality": 90,
         "gps_fix": 3, "sats": 12, "batt_pct": 88.0, "armed": False, "alt": 0.0,
         "lat": 13.7, "lon": 100.5, "home_set": True, "mode": "GUIDED"}
    d.update(over)
    return d


def _snap(drones=None, **over):
    s = {"profile": "production", "token": True, "home_loc": True, "mtls": True,
         "ui_mode": True, "drones": drones if drones is not None else [_drone()],
         "close_pairs": [], "min_sep": 6.0, "takeoff_alt": 20.0, "rtl_base": 15.0,
         "rtl_gap": 5.0, "geofence_points": 4, "head_id": 0,
         "waypoint_planned": False, "waypoint_conflicts": []}
    s.update(over)
    return s


def _by_key(results):
    return {r.key: r for r in results}


def _pump(seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.02)


# ══════════════════════════════════════════════════════════════
#  1) เกณฑ์การตรวจ
# ══════════════════════════════════════════════════════════════
class TestStaticChecks(unittest.TestCase):
    def test_all_green_passes(self):
        res = pf.evaluate_static(_snap())
        s = pf.summarize(res)
        self.assertTrue(s.ok, [r.key for r in res if r.status == pf.FAIL])
        self.assertEqual(s.failed, 0)

    def test_no_drone_is_blocking(self):
        res = pf.evaluate_static(_snap(drones=[]))
        self.assertFalse(pf.summarize(res).ok)
        self.assertTrue(res[0].blocking)

    def test_stale_telemetry_blocks(self):
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(age_s=9.0)])))
        self.assertTrue(res["link.telemetry"].blocking)

    def test_remote_switch_blocks(self):
        res = _by_key(pf.evaluate_static(_snap(ui_mode=False)))
        self.assertTrue(res["link.control"].blocking)

    def test_gps_and_battery(self):
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(gps_fix=2, sats=3)])))
        self.assertTrue(res["ready.gps"].blocking)
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(batt_pct=22.0)])))
        self.assertTrue(res["ready.battery"].blocking)
        # แบตกลาง ๆ = เตือน ไม่บล็อก
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(batt_pct=48.0)])))
        self.assertEqual(res["ready.battery"].status, pf.WARN)
        self.assertFalse(res["ready.battery"].blocking)

    def test_armed_or_airborne_blocks(self):
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(armed=True)])))
        self.assertTrue(res["ready.grounded"].blocking)
        res = _by_key(pf.evaluate_static(_snap(drones=[_drone(alt=12.0)])))
        self.assertTrue(res["ready.grounded"].blocking)

    def test_missing_token_only_matters_in_real_flight(self):
        res = _by_key(pf.evaluate_static(_snap(token=False)))
        self.assertTrue(res["link.core"].blocking)
        res = _by_key(pf.evaluate_static(_snap(token=False, profile="sitl")))
        self.assertEqual(res["link.core"].status, pf.PASS)

    def test_close_pair_blocks_but_warn_pair_does_not(self):
        two = [_drone(1), _drone(2)]
        res = _by_key(pf.evaluate_static(
            _snap(drones=two, head_id=1, close_pairs=[(1, 2, 1.8, "critical")])))
        self.assertTrue(res["sep.spacing"].blocking)
        res = _by_key(pf.evaluate_static(
            _snap(drones=two, head_id=1, close_pairs=[(1, 2, 5.0, "warn")])))
        self.assertEqual(res["sep.spacing"].status, pf.WARN)

    def test_head_required_for_multi_drone(self):
        two = [_drone(1), _drone(2)]
        res = _by_key(pf.evaluate_static(_snap(drones=two, head_id=0)))
        self.assertTrue(res["cfg.head"].blocking)
        res = _by_key(pf.evaluate_static(_snap(drones=two, head_id=2)))
        self.assertEqual(res["cfg.head"].status, pf.PASS)
        # หัวขบวนที่ไม่ออนไลน์ก็ไม่ผ่าน
        stale = [_drone(1), _drone(2, age_s=20.0)]
        res = _by_key(pf.evaluate_static(_snap(drones=stale, head_id=2)))
        self.assertTrue(res["cfg.head"].blocking)

    def test_waypoint_conflicts_block(self):
        res = _by_key(pf.evaluate_static(
            _snap(waypoint_planned=True, waypoint_conflicts=["D1 x D2"])))
        self.assertTrue(res["cfg.waypoint"].blocking)

    def test_advisories_never_block(self):
        res = pf.evaluate_static(_snap(geofence_points=0, rtl_base=5, rtl_gap=1,
                                       drones=[_drone(home_set=False,
                                                      link_quality=10)]))
        self.assertTrue(pf.summarize(res).ok)
        self.assertGreaterEqual(pf.summarize(res).warned, 3)

    def test_bench_checks_hidden_unless_props_removed(self):
        keys = [c.key for c in pf.live_checks(bench=False)]
        self.assertNotIn("cmd.arm_disarm", keys)
        self.assertIn("cmd.mode_guided", keys)
        self.assertIn("cmd.arm_disarm", [c.key for c in pf.live_checks(bench=True)])

    def test_guided_is_the_last_live_check(self):
        """จบด้วย GUIDED เสมอ เพราะเป็นโหมดที่ TAKEOFF ต้องใช้"""
        mode_i = [c.key for c in pf.LIVE_CHECKS].index("cmd.mode_guided")
        hold_i = [c.key for c in pf.LIVE_CHECKS].index("cmd.hold")
        self.assertLess(hold_i, mode_i)


# ══════════════════════════════════════════════════════════════
#  2) สถานะ "รันไปแล้วหรือยัง"
# ══════════════════════════════════════════════════════════════
class TestPreflightState(unittest.TestCase):
    def test_needs_both_buttons(self):
        st = pf.PreflightState()
        self.assertFalse(st.ready())
        self.assertEqual(len(st.missing()), 2)
        st.mark_selftest(True)
        self.assertFalse(st.ready())
        self.assertEqual(len(st.missing()), 1)
        st.mark_checklist(True)
        self.assertTrue(st.ready())
        self.assertEqual(st.missing(), [])

    def test_failed_run_does_not_count(self):
        st = pf.PreflightState()
        st.mark_selftest(False)
        st.mark_checklist(True)
        self.assertFalse(st.ready())
        self.assertIn("ยังไม่ผ่าน", st.missing()[0])

    def test_result_lasts_the_whole_session_by_default(self):
        """ผลอยู่ตลอดการเปิดโปรแกรม 1 ครั้ง — ไม่หมดอายุตามเวลา"""
        st = pf.PreflightState()
        self.assertEqual(st.ttl_s, 0.0)
        now = 1000.0
        st.mark_selftest(True, now=now)
        st.mark_checklist(True, now=now)
        self.assertTrue(st.ready(now=now + 6 * 3600))

    def test_optional_ttl_still_expires(self):
        st = pf.PreflightState(ttl_s=60.0)
        now = 1000.0
        st.mark_selftest(True, now=now)
        st.mark_checklist(True, now=now)
        self.assertTrue(st.ready(now=now + 30))
        self.assertFalse(st.ready(now=now + 90))
        self.assertIn("หมดอายุ", " ".join(st.missing(now=now + 90)))

    def test_badge_levels(self):
        st = pf.PreflightState()
        self.assertEqual(st.badge(), ("⚠ PREFLIGHT", "bad"))
        st.mark_selftest(True)
        self.assertEqual(st.badge()[1], "warn")
        st.mark_checklist(True)
        self.assertEqual(st.badge()[1], "ok")


# ══════════════════════════════════════════════════════════════
#  3) เช็คลิสต์ของ Codex
# ══════════════════════════════════════════════════════════════
class TestChecklist(unittest.TestCase):
    MD = ("# หัวเรื่อง\n"
          "## A. ก่อนเปิดระบบ\n"
          "- [ ] ข้อหนึ่ง\n"
          "- [ ] ข้อสอง\n"
          "## B. Bench test\n"
          "- [ ] ข้อสาม\n"
          "## E. Sign-off\n"
          "- วันที่: ____\n")

    def test_parse_only_sections_with_boxes(self):
        secs = pf.parse_checklist(self.MD)
        self.assertEqual([t for t, _ in secs], ["A. ก่อนเปิดระบบ", "B. Bench test"])
        self.assertEqual(sum(len(i) for _, i in secs), 3)

    def test_real_document_parses(self):
        secs = pf.load_checklist()
        self.assertEqual(len(secs), 1)
        self.assertEqual(sum(len(i) for _, i in secs), 8)

    def test_dialog_opens_with_fresh_session_state(self):
        pfd.ChecklistDialog._session_checked = {}
        dlg = pfd.ChecklistDialog(sections=pf.load_checklist())
        self.assertEqual((dlg.done_n, dlg.total_n), (0, 8))
        self.assertEqual(len(dlg._boxes), 8)
        dlg.close()

    def test_progress_and_persistence(self):
        import tempfile
        secs = pf.parse_checklist(self.MD)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chk.json")
            checked = {pf.item_key("ข้อหนึ่ง"): time.time()}
            self.assertTrue(pf.save_checked(checked, path))
            self.assertEqual(pf.checklist_progress(secs, pf.load_checked(path)), (1, 3))
            for _t, items in secs:
                for it in items:
                    checked[pf.item_key(it)] = time.time()
            pf.save_checked(checked, path)
            self.assertEqual(pf.checklist_progress(secs, pf.load_checked(path)), (3, 3))

    def test_item_key_is_stable_and_unique(self):
        self.assertEqual(pf.item_key(" ข้อหนึ่ง "), pf.item_key("ข้อหนึ่ง"))
        self.assertNotEqual(pf.item_key("ข้อหนึ่ง"), pf.item_key("ข้อสอง"))


# ══════════════════════════════════════════════════════════════
#  4) popup ทดสอบระบบ (กับ fake client)
# ══════════════════════════════════════════════════════════════
class _R:
    def __init__(self, ok=True, message="ok"):
        self.ok, self.message = ok, message


class FakeClient:
    """โดรนจำลอง: จำคำสั่งที่ยิงมา + เปลี่ยนสถานะให้เหมือน FC ตอบรับ"""

    def __init__(self, obey=True, reject_unconfirmed=True):
        self.calls = []
        self.obey = obey                        # False = ตอบ ok แต่โหมดไม่เปลี่ยนจริง
        self.reject_unconfirmed = reject_unconfirmed
        self.state = {}                         # did -> {"mode_name","armed"}

    def _st(self, did):
        return self.state.setdefault(int(did), {"mode_name": "LOITER", "armed": False})

    def hold(self, ids):
        self.calls.append(("hold", tuple(ids)))
        return _R()

    def set_mode(self, ids, mode):
        self.calls.append(("set_mode", tuple(ids), int(mode)))
        if self.obey:
            for d in ids:
                self._st(d)["mode_name"] = "GUIDED"
        return _R()

    def takeoff(self, ids, alt, confirmed=False):
        self.calls.append(("takeoff", tuple(ids), float(alt), bool(confirmed)))
        if not confirmed and self.reject_unconfirmed:
            return _R(False, "takeoff ต้องยืนยันก่อน")
        return _R()

    def arm(self, ids, force=False):
        self.calls.append(("arm", tuple(ids)))
        for d in ids:
            self._st(d)["armed"] = True
        return _R()

    def disarm(self, ids, confirmed=False):
        self.calls.append(("disarm", tuple(ids)))
        for d in ids:
            self._st(d)["armed"] = False
        return _R()

    def kinds(self):
        return [c[0] for c in self.calls]


class TestPreflightDialog(unittest.TestCase):
    def _dlg(self, client=None, snap=None, bench=False):
        client = client or FakeClient()
        dlg = pfd.PreflightDialog(
            None, client=client, snapshot_fn=lambda: snap or _snap(),
            telem_fn=lambda d: client._st(d), target_ids=[1])
        dlg.cb_bench.setChecked(bench)
        return dlg, client

    def _run(self, dlg, seconds=6.0):
        dlg.run_tests()
        end = time.time() + seconds
        while time.time() < end and dlg._running:
            _app.processEvents()
            time.sleep(0.02)
        _app.processEvents()

    def test_happy_path_passes_and_ends_in_guided(self):
        dlg, cl = self._dlg()
        with mock.patch.object(pfd, "_confirm", return_value=True):
            self._run(dlg)
        self.assertTrue(dlg.passed, [r.key for r in dlg.results if r.status == pf.FAIL])
        self.assertIn("set_mode", cl.kinds())
        self.assertEqual(cl._st(1)["mode_name"], "GUIDED")
        # ไม่ติ๊ก bench → ต้องไม่มีการ ARM จริง
        self.assertNotIn("arm", cl.kinds())
        got = _by_key(dlg.results)
        self.assertEqual(got["cmd.arm_disarm"].status, pf.SKIP)

    def test_fc_not_confirming_mode_fails(self):
        dlg, cl = self._dlg(FakeClient(obey=False))
        self._run(dlg, seconds=12.0)
        self.assertFalse(dlg.passed)
        self.assertEqual(_by_key(dlg.results)["cmd.mode_guided"].status, pf.FAIL)

    def test_blocking_static_check_stops_before_touching_drone(self):
        dlg, cl = self._dlg(snap=_snap(drones=[_drone(armed=True)]))
        self._run(dlg)
        self.assertFalse(dlg.passed)
        self.assertEqual(cl.calls, [])          # ไม่ยิงคำสั่งใด ๆ ใส่โดรน
        self.assertEqual(_by_key(dlg.results)["cmd.mode_guided"].status, pf.SKIP)

    def test_bench_mode_runs_arm_and_safety_reject(self):
        dlg, cl = self._dlg(bench=True)
        with mock.patch.object(pfd, "_confirm", return_value=True):
            self._run(dlg, seconds=12.0)
        self.assertIn("arm", cl.kinds())
        self.assertIn("disarm", cl.kinds())
        got = _by_key(dlg.results)
        self.assertEqual(got["cmd.arm_disarm"].status, pf.PASS)
        self.assertEqual(got["cmd.reject_unconfirmed"].status, pf.PASS)
        self.assertTrue(dlg.passed)

    def test_core_accepting_unconfirmed_takeoff_fails_and_disarms(self):
        cl = FakeClient(reject_unconfirmed=False)
        dlg, _ = self._dlg(cl, bench=True)
        with mock.patch.object(pfd, "_confirm", return_value=True):
            self._run(dlg, seconds=12.0)
        got = _by_key(dlg.results)
        self.assertEqual(got["cmd.reject_unconfirmed"].status, pf.FAIL)
        self.assertFalse(dlg.passed)
        self.assertIn("disarm", cl.kinds())     # ดับมอเตอร์กลับทันที

    def test_live_commands_use_dispatcher_when_supplied(self):
        cl = FakeClient()
        seen = []

        def dispatch(label, invoke, targets):
            seen.append((label, tuple(targets)))
            return invoke()

        dlg = pfd.PreflightDialog(
            None, client=cl, snapshot_fn=lambda: _snap(),
            telem_fn=lambda d: cl._st(d), target_ids=[1], dispatch_fn=dispatch)
        with mock.patch.object(pfd, "_confirm", return_value=True):
            self._run(dlg)
        labels = [label for label, _targets in seen]
        self.assertIn("HOLD [preflight]", labels)
        self.assertIn("MODE GUIDED [preflight]", labels)
        self.assertTrue(all(targets == (1,) for _label, targets in seen))
        self.assertTrue(dlg.passed)

    def test_bench_needs_props_off_confirmation(self):
        dlg, cl = self._dlg(bench=True)
        with mock.patch.object(pfd, "_confirm", return_value=False):
            dlg.run_tests()
        _app.processEvents()
        self.assertEqual(cl.calls, [])
        self.assertFalse(dlg._running)


# ══════════════════════════════════════════════════════════════
#  5) ด่านก่อน TAKEOFF ใน cockpit
# ══════════════════════════════════════════════════════════════
class _Pos:
    def __init__(self, lat, lon, alt_rel=0.0):
        self.lat, self.lon, self.alt_rel, self.alt_abs = lat, lon, alt_rel, alt_rel


def _telem(did, lat=13.7, lon=100.5, alt=0.0, armed=False, batt=90.0):
    class _T:
        drone_id = did
        name = f"Drone {did}"
        status = 4
        mode = 5                    # FLIGHT_MODE_GUIDED
        position = _Pos(lat, lon, alt)
        battery_pct = batt
        voltage = 12.4
        ground_speed = 0.0
        heading = 0.0
        gps_fix = 3
        sat_count = 12
        link_quality = 95
        host = "127.0.0.1"
        port = 5760
        rssi = -60
    _T.armed = armed
    return _T


class TestTakeoffGate(unittest.TestCase):
    def setUp(self):
        self._c = mock.patch("swarmgod_gui.widgets.confirm.confirm", return_value=True)
        self._c.start()
        self.win = GroundStation("127.0.0.1:59999")
        for d in list(self.win.fleet_items):
            self.win._remove_fleet_item(d)
        self.fake = FakeClient()
        self.win.client = self.fake
        for d in (1, 2):
            it = FleetItem(d, f"Drone {d}", self.win._pixmap)
            self.win._wire_fleet_item(it)
            self.win.fleet_items[d] = it
            self.win.fleet_area.addWidget(it)
            self.win._last_seen[d] = time.monotonic()
            self.win._last_telem[d] = _telem(d, lon=100.5 + 0.001 * d)
            self.win._last_alt[d] = 0.0
        self.win._selected_ids = {1}
        self.win._selected_id = 1

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()
        self._c.stop()

    # ── snapshot ──
    def test_snapshot_reads_live_telemetry(self):
        snap = self.win._preflight_snapshot()
        self.assertEqual([d["id"] for d in snap["drones"]], [1])
        d = snap["drones"][0]
        self.assertEqual(d["sats"], 12)
        self.assertAlmostEqual(d["batt_pct"], 90.0)
        self.assertFalse(d["armed"])
        self.assertTrue(snap["ui_mode"])
        self.assertEqual(snap["head_id"], 0)

    def test_snapshot_flags_close_pair(self):
        # วางสองลำทับกัน → ต้องโผล่เป็นคู่เสี่ยงชน
        self.win._selected_ids = {1, 2}
        self.win._last_telem[1] = _telem(1, lat=13.7, lon=100.5)
        self.win._last_telem[2] = _telem(2, lat=13.7, lon=100.5)
        snap = self.win._preflight_snapshot()
        self.assertTrue(snap["close_pairs"])
        self.assertTrue(_by_key(pf.evaluate_static(snap))["sep.spacing"].blocking)

    # ── ด่าน ──
    def test_gate_passes_silently_when_ready(self):
        self.win._preflight.mark_selftest(True)
        self.win._preflight.mark_checklist(True)
        with mock.patch.object(pfd, "ask_before_takeoff") as ask:
            self.assertTrue(self.win._preflight_gate("TAKEOFF"))
        ask.assert_not_called()

    def test_gate_cancel_blocks_takeoff(self):
        with mock.patch("swarmgod_gui.app.ask_before_takeoff", return_value="cancel"):
            self.win._cmd_takeoff()
        _pump(0.3)
        self.assertNotIn("takeoff", self.fake.kinds())

    def test_gate_skip_flies_but_records_bypass(self):
        with mock.patch("swarmgod_gui.app.ask_before_takeoff", return_value="skip"):
            self.win._cmd_takeoff()
        _pump(0.5)
        self.assertIn("takeoff", self.fake.kinds())
        self.assertTrue(self.win._preflight.bypassed)
        self.assertEqual(self.win._preflight.bypass_count, 1)

    def test_gate_test_choice_runs_both_dialogs(self):
        calls = []

        def run_test():
            calls.append("test")
            self.win._preflight.mark_selftest(True)
            return True

        def run_list():
            calls.append("list")
            self.win._preflight.mark_checklist(True)
            return True

        with mock.patch("swarmgod_gui.app.ask_before_takeoff", return_value="test"), \
                mock.patch.object(self.win, "_open_preflight_test", run_test), \
                mock.patch.object(self.win, "_open_preflight_checklist", run_list):
            self.win._cmd_takeoff()
        _pump(0.5)
        self.assertEqual(calls, ["test", "list"])
        self.assertIn("takeoff", self.fake.kinds())     # เทสผ่านแล้วบินต่อให้เลย

    def test_panel_takeoff_and_swarm_takeoff_are_gated(self):
        for fn in (lambda: self.win._on_panel_takeoff("all"), self.win._swarm_takeoff):
            self.fake.calls.clear()
            with mock.patch("swarmgod_gui.app.ask_before_takeoff",
                            return_value="cancel"):
                fn()
            _pump(0.2)
            self.assertNotIn("takeoff", self.fake.kinds())

    def test_waypoint_auto_takeoff_is_gated(self):
        with mock.patch("swarmgod_gui.app.ask_before_takeoff", return_value="cancel"):
            started = self.win._wp_require_takeoff([1], "ทดสอบ", lambda: None)
        _pump(0.2)
        self.assertFalse(started)
        self.assertNotIn("takeoff", self.fake.kinds())

    # ── ป้ายเตือน ──
    def test_badge_turns_green_only_after_both(self):
        """ป้ายข้าง ONLINE ใช้ทรงเดียวกับ CORE — แดงมี ⚠ + เขียวเมื่อผ่านครบ"""
        self.win._refresh_preflight_ui()
        self.assertEqual(self.win.btn_preflight.text(), "⚠ PREFLIGHT")
        self.assertEqual(self.win._pf_badge_cache[1], "bad")
        self.assertIn(self.win._PF_WARN, self.win.banner.text())
        self.win._preflight.mark_selftest(True)
        self.win._refresh_preflight_ui()
        self.assertEqual(self.win._pf_badge_cache[1], "warn")
        self.win._preflight.mark_checklist(True)
        self.win._refresh_preflight_ui()
        self.assertEqual(self.win._pf_badge_cache[1], "ok")
        self.assertEqual(self.win.btn_preflight.text(), "● PREFLIGHT")
        self.assertIn(T("green").lstrip("#").lower(),
                      self.win.btn_preflight.styleSheet().lower())
        self.assertNotIn(self.win._PF_WARN, self.win.banner.text())

    def test_lan_button_moved_into_commands_panel(self):
        """LAN ไม่อยู่บน top bar แล้ว — ย้ายไปหัวแผง COMMANDS"""
        self.assertIsNot(self.win.btn_field.parentWidget(), self.win._topbar)
        self.assertIs(self.win.pill_core.parentWidget(), self.win._topbar)
        self.assertIs(self.win.pill_link.parentWidget(), self.win._topbar)

    def test_preflight_badge_sits_beside_online(self):
        """PREFLIGHT อยู่หัวแผง FLEET ข้าง ONLINE ไม่แย่งพื้นที่แถบโหมดบน topbar"""
        self.assertIs(self.win.btn_preflight.parentWidget(), self.win.left_col)
        self.assertIs(self.win.lbl_count.parentWidget(), self.win.left_col)
        self.assertIsNot(self.win.btn_preflight.parentWidget(), self.win._topbar)

    def test_arm_warns_but_is_not_blocked(self):
        self.win._cmd_arm()
        _pump(0.4)
        self.assertIn("arm", self.fake.kinds())


if __name__ == "__main__":
    unittest.main()
