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


class GoodAltTests(unittest.TestCase):
    def test_picks_smallest_cushion_that_clears_threshold(self):
        from odds.alt_spreads import good_alt
        a = good_alt(2.5, -3.5, 12.0, cover_probability, "away")
        self.assertEqual(a["side"], "away")
        self.assertGreaterEqual(a["cover"], 0.65)
        # one point less cushion must NOT clear the threshold
        lower = cover_probability(2.5, -(a["line"] - 1), 12.0)[1]
        self.assertLess(lower, 0.65)

    def test_none_when_price_too_juiced(self):
        from odds.alt_spreads import good_alt
        self.assertIsNone(good_alt(2.5, -3.5, 12.0, cover_probability, "away",
                                   min_cover=0.9))


class RankAltsTests(unittest.TestCase):
    def test_ranked_best_edge_first_and_main_line_excluded(self):
        from odds.alt_spreads import rank_alts
        rows = rank_alts(2.5, -6.5, 12.0, cover_probability, "away")
        self.assertTrue(rows)
        edges = [r["est_edge"] for r in rows]
        self.assertEqual(edges, sorted(edges, reverse=True))
        self.assertTrue(rows[0]["star"])
        self.assertFalse(any(abs(r["line"] - 6.5) < 0.3 for r in rows))
        for r in rows:
            self.assertGreaterEqual(r["cover"], 0.55)

    def test_no_star_when_model_agrees_with_market(self):
        from odds.alt_spreads import rank_alts
        # model margin == market margin -> our chance < book price (hold) always
        rows = rank_alts(6.5, -6.5, 12.0, cover_probability, "home")
        self.assertTrue(all(not r["star"] for r in rows))


if __name__ == "__main__":
    unittest.main()
