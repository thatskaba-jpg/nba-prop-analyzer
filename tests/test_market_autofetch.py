import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import profile_scoreboard as ps  # noqa: E402


class AutoFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "snap.json"
        self.p1 = mock.patch.object(ps, "_MKT_PATH", self.tmp)
        self.p1.start()
        ps._MKT_TRIED.clear()

    def tearDown(self):
        self.p1.stop()
        ps._MKT_TRIED.clear()

    def test_missing_line_is_fetched_from_api_then_used(self):
        def fake_backfill(day):
            ps.capture_market_prob(day, "MIA", "MIN", 0.52, 14)
            return 1
        with mock.patch("collect.backfill_game_lines.backfill_date_live",
                        side_effect=fake_backfill) as f:
            self.assertEqual(ps._market_for_game("2026-10-21", "MIA", "MIN"), (0.52, 14))
            ps._market_for_game("2026-10-21", "MIA", "MIN")
            self.assertEqual(f.call_count, 0 + 1)     # asked the API only once

    def test_api_has_nothing_returns_none(self):
        with mock.patch("collect.backfill_game_lines.backfill_date_live", return_value=0):
            self.assertEqual(ps._market_for_game("2026-10-20", "BOS", "DET"), (None, 0))

    def test_saved_line_never_calls_api(self):
        ps.capture_market_prob("2026-10-22", "PHI", "CLE", 0.55, 14)
        with mock.patch("collect.backfill_game_lines.backfill_date_live") as f:
            self.assertEqual(ps._market_for_game("2026-10-22", "PHI", "CLE"), (0.55, 14))
            f.assert_not_called()

    def test_api_error_is_swallowed(self):
        with mock.patch("collect.backfill_game_lines.backfill_date_live",
                        side_effect=RuntimeError("429")):
            self.assertEqual(ps._market_for_game("2026-10-19", "A", "B"), (None, 0))


if __name__ == "__main__":
    unittest.main()
