import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "collect"))

import backfill_game_lines as b  # noqa: E402

LINES = {
    "MIA": {"is_home": True, "opp": "MIN", "moneyline": -110, "spread": -1.0, "total": 230.5, "source": "balldontlie"},
    "MIN": {"is_home": False, "opp": "MIA", "moneyline": -110, "spread": 1.0, "total": 230.5, "source": "balldontlie"},
    "SAS": {"is_home": True, "opp": "HOU", "moneyline": -200, "spread": -5.5, "total": 225.5, "source": "balldontlie"},
    "HOU": {"is_home": False, "opp": "SAS", "moneyline": 170, "spread": 5.5, "total": 225.5, "source": "balldontlie"},
    "PHI": {"is_home": True, "opp": "CLE", "moneyline": None, "spread": None, "total": None, "source": "x"},
    "CLE": {"is_home": False, "opp": "PHI", "moneyline": None, "spread": None, "total": None, "source": "x"},
}


class BackfillTests(unittest.TestCase):
    def test_devig(self):
        self.assertAlmostEqual(b.devig_home(-110, -110), 0.5, places=4)
        self.assertGreater(b.devig_home(-200, 170), 0.6)
        self.assertIsNone(b.devig_home(None, -110))

    def test_stores_only_games_with_prices(self):
        saved = []
        found, stored = b.backfill_day(
            "2026-10-21", lambda d: LINES, lambda d: 14,
            lambda *a: saved.append(a))
        self.assertEqual((found, stored), (3, 2))        # PHI has no moneyline
        day, home, away, p, gp, extra = saved[0]
        self.assertEqual((day, home, away, gp), ("2026-10-21", "MIA", "MIN", 14))
        self.assertAlmostEqual(p, 0.5, places=3)
        self.assertEqual(extra["spread_home"], -1.0)
        self.assertTrue(extra["backfilled"])

    def test_empty_feed(self):
        self.assertEqual(b.backfill_day("2026-10-20", lambda d: {}, lambda d: 0,
                                        lambda *a: None), (0, 0))


if __name__ == "__main__":
    unittest.main()
