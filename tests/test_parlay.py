import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import parlay as p  # noqa: E402


def e(away, home, ph, mlh, mla, skip=False, tip="7:00 PM ET"):
    return {"away": away, "home": home, "p_home_win": ph, "p_away_win": 1 - ph,
            "ml_home": mlh, "ml_away": mla, "skip": skip, "tip": tip}


ENTRIES = [e("TOR", "WAS", .26, 300, -400), e("NYK", "BOS", .58, -140, 120),
           e("LAC", "LAL", .78, -400, 300, skip=True), e("HOU", "SAS", .62, -160, 135),
           e("PHI", "CLE", .57, None, None)]


class ParlayTests(unittest.TestCase):
    def test_decimal_american_roundtrip(self):
        self.assertAlmostEqual(p.to_decimal(-110), 1.9091, places=3)
        self.assertAlmostEqual(p.to_decimal(150), 2.5)
        self.assertEqual(p.to_american(2.5), 150)
        self.assertEqual(p.to_american(p.to_decimal(-200)), -200)

    def test_candidates_best_first_skip_and_missing_prices(self):
        legs = p.candidates(ENTRIES)
        self.assertEqual([l["abbr"] for l in legs], ["TOR", "SAS", "BOS"])
        self.assertEqual(legs[0]["odds"], -400)           # TOR is the road favorite
        self.assertEqual(legs[0]["team"], "Toronto Raptors")

    def test_heavy_favorite_filter(self):
        legs = p.candidates(ENTRIES, worst_price=-300)
        self.assertNotIn("TOR", [l["abbr"] for l in legs])

    def test_summary_math(self):
        legs = [{"p": .5, "odds": 100}, {"p": .5, "odds": 100}]
        s = p.summarize(legs, stake=100)
        self.assertAlmostEqual(s["model_chance"], .25)
        self.assertAlmostEqual(s["book_breakeven"], .25)
        self.assertEqual(s["odds"], 300)
        self.assertEqual(s["payout"], 300.0)

    def test_parlay_chance_is_lower_than_any_single_leg(self):
        legs = p.candidates(ENTRIES)
        s = p.summarize(legs)
        self.assertLess(s["model_chance"], min(l["p"] for l in legs))

    def test_empty(self):
        self.assertIsNone(p.summarize([]))
        self.assertEqual(p.candidates([]), [])


if __name__ == "__main__":
    unittest.main()
