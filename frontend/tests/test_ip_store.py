import os
import tempfile
import unittest
from unittest import mock

from swarmgod_gui.core import ip_store


class IpStorePathTests(unittest.TestCase):
    def test_explicit_data_dir_wins(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"SWARMGOD_DATA_DIR": tmp, "SWARMGOD_NO_MAP": "1"}):
            self.assertEqual(ip_store._default_db_path(), os.path.join(tmp, "fleet_ips.db"))

    def test_headless_mode_never_uses_user_home(self):
        with mock.patch.dict(os.environ, {"SWARMGOD_NO_MAP": "1"}, clear=False):
            os.environ.pop("SWARMGOD_DATA_DIR", None)
            path = ip_store._default_db_path()
            self.assertTrue(path.startswith(tempfile.gettempdir()))
            self.assertNotIn(os.path.join(os.path.expanduser("~"), ".swarmgod"), path)


if __name__ == "__main__":
    unittest.main()
