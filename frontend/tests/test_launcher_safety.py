import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from swarmgod_gui.launcher import Launcher


class TestRealDroneLauncherSafety(unittest.TestCase):
    def test_existing_binary_is_rebuilt_from_current_source(self):
        launcher = Launcher(use_sitl=True)
        launcher._go = "go"
        completed = SimpleNamespace(returncode=0, stderr="")
        with mock.patch("swarmgod_gui.launcher.subprocess.run", return_value=completed) as run, \
                mock.patch("swarmgod_gui.launcher.os.path.exists", return_value=True):
            launcher._step_build(2)
        run.assert_called_once()
        self.assertIn("build", run.call_args.args[0])

    def test_real_mode_without_home_enters_telemetry_only_setup(self):
        env = {
            "SWARMGOD_PROFILE": "hil",
            "SWARMGOD_MISSION_AUTHORITY": "core-single",
            "SWARMGOD_BENCH_CONFIRM": "PROPS-REMOVED-BENCH",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            got = Launcher(use_sitl=False)._core_runtime_env()
        self.assertEqual(got["SWARMGOD_PROFILE"], "setup")
        self.assertNotIn("SWARMGOD_MISSION_AUTHORITY", got)
        self.assertNotIn("SWARMGOD_BENCH_CONFIRM", got)

    def test_production_requires_session_token(self):
        env = {
            "SWARMGOD_PROFILE": "production",
            "SWARMGOD_HOME_LOC": "13.7563,100.5018,0,0",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SWARMGOD_TOKEN"):
                Launcher(use_sitl=False)._core_runtime_env()

    def test_production_forces_strict_signing(self):
        env = {
            "SWARMGOD_PROFILE": "production",
            "SWARMGOD_HOME_LOC": "13.7563,100.5018,0,0",
            "SWARMGOD_TOKEN": "session.token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            got = Launcher(use_sitl=False)._core_runtime_env()
        self.assertEqual(got["SWARMGOD_MAVLINK_STRICT"], "1")

    def test_sitl_profile_is_explicit(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            got = Launcher(use_sitl=True)._core_runtime_env()
        self.assertEqual(got["SWARMGOD_PROFILE"], "sitl")


if __name__ == "__main__":
    unittest.main()
