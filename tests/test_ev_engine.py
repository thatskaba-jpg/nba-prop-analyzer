"""EV and vig math: the numbers every value flag in the app is built on."""

import unittest

import helpers  # noqa: F401  (puts src/ on sys.path)
from odds.ev_engine import (american_to_decimal, decimal_to_american,
                            evaluate_prop, expected_value, implied_probability,
                            remove_vig)


class OddsConversionTests(unittest.TestCase):
    def test_american_to_decimal(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.0 + 100 / 110, places=6)
        self.assertAlmostEqual(american_to_decimal(150), 2.5, places=6)
        self.assertAlmostEqual(american_to_decimal(100), 2.0, places=6)

    def test_decimal_round_trip(self):
        for odds in (-300, -150, -110, 100, 120, 250):
            self.assertEqual(decimal_to_american(american_to_decimal(odds)), odds)

    def test_implied_probability(self):
        self.assertAlmostEqual(implied_probability(-110), 110 / 210, places=6)
        self.assertAlmostEqual(implied_probability(150), 0.4, places=6)


class VigTests(unittest.TestCase):
    def test_even_market_is_fifty_fifty(self):
        r = remove_vig(-110, -110)
        self.assertAlmostEqual(r["fair_over"], 0.5, places=4)
        self.assertAlmostEqual(r["fair_under"], 0.5, places=4)
        self.assertGreater(r["vig_pct"], 0)

    def test_fair_probabilities_sum_to_one(self):
        r = remove_vig(-130, 110)
        self.assertAlmostEqual(r["fair_over"] + r["fair_under"], 1.0, places=3)


class ExpectedValueTests(unittest.TestCase):
    def test_break_even_probability_has_zero_ev(self):
        self.assertAlmostEqual(expected_value(0.5, 100), 0.0, places=6)
        self.assertAlmostEqual(expected_value(110 / 210, -110), 0.0, places=3)

    def test_sign_follows_the_edge(self):
        self.assertGreater(expected_value(0.60, -110), 0)
        self.assertLess(expected_value(0.45, -110), 0)


class EvaluatePropTests(unittest.TestCase):
    def test_invalid_odds_are_never_evaluated(self):
        for over, under in ((0, 0), (-50, 120), (None, -110)):
            r = evaluate_prop(0.6, 0.4, over, under)
            self.assertFalse(r["is_value"])
            self.assertIsNone(r["recommended_side"])

    def test_clear_edge_is_flagged_as_value(self):
        r = evaluate_prop(0.62, 0.38, -110, -110)
        self.assertTrue(r["is_value"])
        self.assertEqual(r["recommended_side"], "over")

    def test_small_edge_is_not_value(self):
        r = evaluate_prop(0.52, 0.48, -110, -110)  # +2% edge, under the 3% bar
        self.assertFalse(r["is_value"])


if __name__ == "__main__":
    unittest.main()
