"""Game-line model: symmetry, and regressions for bugs that were found in it."""

import unittest

import helpers  # noqa: F401  (puts src/ on sys.path)
from models.game_model import _star_tax_points, project_game

EVEN = {"AAA": {"off": 114.0, "def": 112.0, "pace": 100.0},
        "BBB": {"off": 114.0, "def": 112.0, "pace": 100.0}}
# AAA (home) is far better than BBB: a ~12 point raw margin.
LOPSIDED = {"AAA": {"off": 120.0, "def": 108.0, "pace": 100.0},
            "BBB": {"off": 108.0, "def": 120.0, "pace": 100.0}}
NO_HOME_COURT = {"home_edge": 0.0}


class SymmetryTests(unittest.TestCase):
    def test_identical_teams_without_home_court_is_a_coin_flip(self):
        g = project_game("AAA", "BBB", EVEN, NO_HOME_COURT)
        self.assertAlmostEqual(g["exp_margin"], 0.0, places=6)
        self.assertAlmostEqual(g["p_home_win"], 0.5, places=6)

    def test_missing_ratings_return_none(self):
        self.assertIsNone(project_game("AAA", "ZZZ", EVEN))


class RegressionTests(unittest.TestCase):
    def test_turnover_edge_is_counted_once(self):
        # Home turns it over 2 pts of TOV% less than away. _tov_battle values
        # that at ~0.6 margin pts per point (1.2). It was ALSO added in the
        # four-factors block, which made the edge worth ~2.2.
        ff = {"AAA": {"tov_pct": 0.12}, "BBB": {"tov_pct": 0.14}}
        base = project_game("AAA", "BBB", EVEN, NO_HOME_COURT)
        g = project_game("AAA", "BBB", EVEN, NO_HOME_COURT, four_factors=ff)
        self.assertAlmostEqual(g["exp_margin"] - base["exp_margin"], 1.2, places=1)
        self.assertEqual(g["four_factor_pts"], 0.0)

    def test_deep_bench_absence_costs_a_fraction_of_a_point(self):
        pts, _ = _star_tax_points([("Bench Guy", "out", 2.0)])
        self.assertLess(pts, 0.5)

    def test_a_star_absence_still_costs_real_points(self):
        pts, _ = _star_tax_points([("Star", "out", 25.0)])
        self.assertAlmostEqual(pts, 5.0, places=1)

    def test_market_anchor_counts_games_per_team_not_league_wide(self):
        # `games_played` is the LEAGUE-wide finished-game count. 14 league games
        # is under one game per team, so the market should still dominate: the
        # blend must stay close to the 50% market price, not jump to the model.
        early = project_game("AAA", "BBB", LOPSIDED, NO_HOME_COURT,
                             market_p_home=0.5, games_played=14)
        self.assertTrue(early["market_used"])
        self.assertGreater(early["p_home_win"], 0.5)
        self.assertLess(early["p_home_win"], 0.55)

    def test_model_earns_more_weight_as_the_season_fills_in(self):
        early = project_game("AAA", "BBB", LOPSIDED, NO_HOME_COURT,
                             market_p_home=0.5, games_played=14)
        late = project_game("AAA", "BBB", LOPSIDED, NO_HOME_COURT,
                            market_p_home=0.5, games_played=450)
        self.assertGreater(late["p_home_win"], early["p_home_win"])


if __name__ == "__main__":
    unittest.main()
