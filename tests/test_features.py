"""
The no-peeking rule: features for a game may only use games strictly BEFORE it.
If these fail, every projection, backtest and calibration number is suspect.
"""

import unittest

import pandas as pd

import helpers  # noqa: F401  (puts src/ on sys.path)
from features.features import pregame_features


def make_history(n=30):
    """n games, two days apart, one player; points climb 10, 11, 12, ..."""
    dates = list(pd.date_range("2025-11-01", periods=n, freq="2D")
                 .strftime("%Y-%m-%d"))
    return pd.DataFrame({
        "game_date": dates,
        "season": "2025",
        "is_home": [i % 2 for i in range(n)],
        "opponent": "BBB",
        "matchup": ["AAA vs BBB" if i % 2 else "AAA @ BBB" for i in range(n)],
        "game_id": [str(i) for i in range(n)],
        "minutes": 30.0,
        "points": [10 + i for i in range(n)],
        "rebounds": 5, "assists": 4, "threes_made": 2, "steals": 1,
        "blocks": 1, "turnovers": 2, "fouls": 2, "threes_att": 5,
        "fgm": 8, "fga": 16, "ftm": 3, "fta": 4,
    })


class NoLeakTests(unittest.TestCase):
    def test_future_rows_cannot_change_the_features(self):
        hist = make_history(30)
        cutoff = hist.loc[20, "game_date"]
        before = pregame_features(1, "points", cutoff, hist)

        tampered = hist.copy()
        future = tampered["game_date"] >= cutoff
        tampered.loc[future, "points"] = 999
        tampered.loc[future, "minutes"] = 48.0
        after = pregame_features(1, "points", cutoff, tampered)

        self.assertEqual(before, after)
        self.assertEqual(before["games_played_before"], 20)

    def test_a_game_on_the_cutoff_date_is_excluded(self):
        hist = make_history(10)
        cutoff = hist.loc[5, "game_date"]
        self.assertEqual(
            pregame_features(1, "points", cutoff, hist)["games_played_before"], 5)

    def test_no_prior_games(self):
        hist = make_history(10)
        self.assertEqual(pregame_features(1, "points", "2000-01-01", hist),
                         {"games_played_before": 0})

    def test_averages_come_only_from_prior_games(self):
        hist = make_history(30)
        cutoff = hist.loc[20, "game_date"]       # prior points are 10..29
        feats = pregame_features(1, "points", cutoff, hist)
        self.assertAlmostEqual(feats["season_avg"], 19.5, places=6)
        self.assertAlmostEqual(feats["avg_last_5"], 27.0, places=6)    # 25..29
        self.assertAlmostEqual(feats["avg_last_10"], 24.5, places=6)   # 20..29


if __name__ == "__main__":
    unittest.main()
