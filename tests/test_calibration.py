"""Calibration map math, leak-free historical context, and the validation tally."""

import unittest
from unittest import mock

import helpers  # noqa: F401  (puts src/ on sys.path)
from backtest.real_line_validation import Tally
from models import calibration as cal


class IsotonicTests(unittest.TestCase):
    def test_pools_adjacent_violators(self):
        fitted = cal._isotonic([0.1, 0.2, 0.3], [0.5, 0.4, 0.6], [10, 10, 10])
        self.assertEqual([round(v, 6) for v in fitted], [0.45, 0.45, 0.6])

    def test_weights_pull_the_pooled_value(self):
        fitted = cal._isotonic([0.1, 0.2], [0.6, 0.4], [30, 10])
        self.assertEqual([round(v, 6) for v in fitted], [0.55, 0.55])

    def test_output_is_never_decreasing(self):
        ys = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3]
        fitted = cal._isotonic(list(range(6)), ys, [5] * 6)
        self.assertEqual(fitted, sorted(fitted))


class CalibrateTests(unittest.TestCase):
    FIT = {"pooled": {"x": [0.1, 0.9], "y": [0.4, 0.6]},
           "per_prop": {"points": {"x": [0.1, 0.9], "y": [0.3, 0.5]}}}

    def test_identity_without_a_fit(self):
        self.assertEqual(cal.calibrate_p_over(0.7, "points", fit_obj={}), 0.7)

    def test_none_passes_through(self):
        self.assertIsNone(cal.calibrate_p_over(None, "points", self.FIT))

    def test_interpolates_and_clamps_to_the_ends(self):
        self.assertAlmostEqual(cal.calibrate_p_over(0.5, "rebounds", self.FIT), 0.5)
        self.assertAlmostEqual(cal.calibrate_p_over(0.0, "rebounds", self.FIT), 0.4)
        self.assertAlmostEqual(cal.calibrate_p_over(1.0, "rebounds", self.FIT), 0.6)

    def test_per_prop_map_beats_the_pooled_one(self):
        self.assertAlmostEqual(cal.calibrate_p_over(0.5, "points", self.FIT), 0.4)


class HistoricalContextTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cal, "_defense_map_for",
                                    return_value={"BBB": 1.02})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blank_values_become_none(self):
        ctx = cal.historical_context("2026-01-05", float("nan"), float("nan"))
        self.assertIsNone(ctx["opponent_abbr"])
        self.assertIsNone(ctx["upcoming_is_home"])

    def test_real_values_pass_through(self):
        ctx = cal.historical_context("2026-01-05", "BBB", 1)
        self.assertEqual(ctx["opponent_abbr"], "BBB")
        self.assertIs(ctx["upcoming_is_home"], True)
        self.assertEqual(ctx["defense_factor_map"], {"BBB": 1.02})
        self.assertIs(cal.historical_context("2026-01-05", "BBB", 0)["upcoming_is_home"],
                      False)

    def test_full_season_team_ratings_are_never_passed(self):
        # Those snapshots include the very game (and everything after it).
        self.assertNotIn("team_ratings_map",
                         cal.historical_context("2026-01-05", "BBB", 1))

    def test_defense_map_is_cut_off_at_the_start_of_the_games_week(self):
        self.assertEqual(cal._week_start("2026-01-08"), "2026-01-05")  # Thu -> Mon
        self.assertEqual(cal._week_start("2026-01-05"), "2026-01-05")  # Mon -> Mon


class TallyTests(unittest.TestCase):
    def test_roi_and_break_even_at_the_price_paid(self):
        t = Tally()
        t.add(-110, True)
        t.add(-110, False)
        self.assertEqual(t.n, 2)
        self.assertAlmostEqual(t.hit(), 0.5)
        self.assertAlmostEqual(t.roi(), (100 / 110 - 1) / 2, places=6)
        self.assertAlmostEqual(t.break_even(), 110 / 210, places=6)

    def test_an_underdog_price_pays_more(self):
        t = Tally()
        t.add(200, True)
        self.assertAlmostEqual(t.profit, 2.0, places=6)

    def test_interval_needs_at_least_two_bets(self):
        t = Tally()
        t.add(-110, True)
        self.assertIsNone(t.roi_ci95())
        t.add(-110, False)
        self.assertGreater(t.roi_ci95(), 0)


if __name__ == "__main__":
    unittest.main()
