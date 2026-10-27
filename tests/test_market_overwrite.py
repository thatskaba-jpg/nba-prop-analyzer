import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import profile_scoreboard as ps  # noqa: E402


class OverwriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "snap.json"
        self.p = mock.patch.object(ps, "_MKT_PATH", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_latest_line_wins_when_overwrite(self):
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.50, 14, overwrite=True)
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.56, 14, overwrite=True)
        self.assertEqual(ps._market_for_game("2026-10-23", "MIA", "DET")[0], 0.56)

    def test_first_write_wins_without_overwrite(self):
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.50, 14)
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.56, 14)
        self.assertEqual(ps._market_for_game("2026-10-23", "MIA", "DET")[0], 0.50)

    def test_unchanged_line_does_not_rewrite_file(self):
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.50, 14, overwrite=True)
        m1 = self.tmp.stat().st_mtime_ns
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.50, 14, overwrite=True)
        self.assertEqual(self.tmp.stat().st_mtime_ns, m1)

    def test_extra_fields_stored(self):
        ps.capture_market_prob("2026-10-23", "MIA", "DET", 0.50, 14,
                               extra={"spread_home": -1.5}, overwrite=True)
        self.assertEqual(ps._mkt_load()["2026-10-23|DET @ MIA"]["spread_home"], -1.5)


if __name__ == "__main__":
    unittest.main()
