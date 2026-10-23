import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from odds.alt_spreads import american_from_prob, ladder  # noqa: E402
from models.game_model import cover_probability  # noqa: E402


class AltSpreadTests(unittest.TestCase):
    def test_fair_odds(self):
        self.assertEqual(american_from_prob(0.5), -100)
        self.assertEqual(american_from_prob(0.6), -150)
        self.assertEqual(american_from_prob(0.4), 150)
        self.assertIsNone(american_from_prob(1.0))

    def test_ladder_is_monotonic_and_symmetric(self):
        rows = ladder(5.0, -3.5, 12.0, cover_probability)
        self.assertEqual(len(rows), 7)
        # More points for the home team -> home cover chance goes UP.
        ph = [r["p_home"] for r in rows]
        self.assertEqual(ph, sorted(ph))
        for r in rows:
            self.assertAlmostEqual(r["p_home"] + r["p_away"], 1.0, places=3)
            self.assertEqual(r["away_line"], -r["home_line"])
        self.assertEqual([r["home_line"] for r in rows][3], -3.5)

    def test_need_is_worse_than_fair(self):
        r = ladder(5.0, -3.5, 12.0, cover_probability)[3]
        # To have a 3-pt edge you need a better (higher-paying) price than fair.
        self.assertGreater(r["need_home"], r["fair_home"])


if __name__ == "__main__":
    unittest.main()
