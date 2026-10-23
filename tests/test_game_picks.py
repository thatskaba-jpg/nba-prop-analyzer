"""Elo baseline, game reconstruction, and the frozen-picks scoreboard."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from models import profile_scoreboard as ps
from models.elo import Elo, expected, games_from_logs, replay

RATINGS = {"AAA": {"off": 118.0, "def": 108.0, "pace": 100.0},
           "BBB": {"off": 108.0, "def": 118.0, "pace": 100.0}}


class EloTests(unittest.TestCase):
    def test_even_teams_favour_only_the_home_side(self):
        elo = Elo(hca=70)
        self.assertAlmostEqual(expected(0), 0.5)
        self.assertGreater(elo.p_home("AAA", "BBB"), 0.5)
        self.assertGreater(elo.margin("AAA", "BBB"), 0)

    def test_winning_raises_a_rating_and_losing_lowers_it(self):
        elo = Elo()
        for _ in range(5):
            elo.update("AAA", "BBB", 110, 100)
        self.assertGreater(elo.rating("AAA"), 1500)
        self.assertLess(elo.rating("BBB"), 1500)
        self.assertGreater(elo.p_home("AAA", "BBB"), elo.p_home("BBB", "AAA"))

    def test_a_bigger_win_moves_it_more(self):
        a, b = Elo(), Elo()
        a.update("AAA", "BBB", 105, 100)
        b.update("AAA", "BBB", 130, 100)
        self.assertGreater(b.rating("AAA"), a.rating("AAA"))

    def test_ratings_regress_toward_average_between_seasons(self):
        elo = Elo(carry=0.5)
        for _ in range(10):
            elo.update("AAA", "BBB", 120, 100, season="2025")
        before = elo.rating("AAA")
        elo.update("CCC", "DDD", 100, 99, season="2026")      # first game of a new season
        self.assertLess(abs(elo.rating("AAA") - 1500), abs(before - 1500))

    def test_predictions_are_made_before_the_game_is_learned(self):
        import pandas as pd
        games = pd.DataFrame([
            {"home_team": "AAA", "away_team": "BBB", "home_pts": 130, "away_pts": 90,
             "season": "2025"}] * 3)
        elo = Elo(hca=70)
        p, _ = replay(games, elo)
        self.assertAlmostEqual(p[0], expected(70))            # nothing learned yet
        self.assertGreater(p[2], p[0])                        # then it learns


class GamesFromLogsTests(helpers.TempDBTestCase):
    def insert(self, pid, home_side, team, opp, points, minutes=30):
        from database import get_connection
        conn = get_connection()
        matchup = f"{team} vs {opp}" if home_side else f"{team} @ {opp}"
        conn.execute(
            "INSERT INTO player_game_logs (player_id, player_name, game_id, game_date, "
            "season, matchup, is_home, opponent, minutes, points) VALUES "
            "(?,?,?,?,?,?,?,?,?,?)",
            (pid, f"P{pid}", "g1", "2026-01-01", "2025", matchup, int(home_side), opp,
             minutes, points))
        conn.commit()
        conn.close()

    def test_scores_are_summed_per_side_and_dnps_ignored(self):
        self.insert(1, True, "AAA", "BBB", 60)
        self.insert(2, True, "AAA", "BBB", 50)
        self.insert(3, True, "AAA", "BBB", 0, minutes=0)       # DNP
        self.insert(4, False, "BBB", "AAA", 100)
        g = games_from_logs()
        self.assertEqual(len(g), 1)
        r = g.iloc[0]
        self.assertEqual((r["home_team"], r["away_team"]), ("AAA", "BBB"))
        self.assertEqual((r["home_pts"], r["away_pts"], r["home_win"]), (110, 100, 1))


class FrozenPicksTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(ps, "_FWD_PATH", Path(tmp.name) / "fwd.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, date="2026-10-22", market=0.7):
        return ps.capture_forward_picks(date, "AAA", "BBB", RATINGS, {},
                                        market_p_home=market, elo=Elo())

    def test_captures_every_profile_and_the_baselines(self):
        self.assertTrue(self.capture())
        picks = ps._fwd_load()["pending"]["2026-10-22|BBB @ AAA"]["picks"]
        for name in ps.GAME_PROFILES:
            self.assertIn(name, picks)
        for name in (ps.BASELINE_HOME, ps.BASELINE_ELO, ps.BASELINE_MARKET):
            self.assertIn(name, picks)
        self.assertTrue(picks[ps.BASELINE_MARKET]["home"])      # 70% on the home side

    def test_first_capture_wins(self):
        self.assertTrue(self.capture(market=0.7))
        self.assertFalse(self.capture(market=0.2))              # a later look changes nothing
        picks = ps._fwd_load()["pending"]["2026-10-22|BBB @ AAA"]["picks"]
        self.assertEqual(picks[ps.BASELINE_MARKET]["p_home"], 0.7)

    def test_grading_scores_each_pick_and_keeps_the_game_locked_out(self):
        self.capture(market=0.7)
        final = [(1, "2026-10-22", "AAA", "BBB", 99, 110)]       # the AWAY team won
        self.assertEqual(ps.grade_forward(final), 1)
        rows = {r[0]: r for r in ps.get_forward_scoreboard()}
        home_row = rows[ps.BASELINE_HOME]
        self.assertEqual((home_row[1], home_row[2]), (0, 1))     # "always home" was wrong
        market = rows[ps.BASELINE_MARKET]
        self.assertEqual((market[1], market[2]), (0, 1))
        self.assertAlmostEqual(market[4], 0.49, places=2)        # Brier (0.7 - 0)^2
        self.assertEqual(ps.forward_pending_count(), 0)
        self.assertFalse(self.capture())                         # graded: can't be redone
        self.assertEqual(ps.grade_forward(final), 0)             # nor double counted

    def test_a_game_that_is_not_final_stays_pending(self):
        self.capture()
        self.assertEqual(ps.grade_forward([]), 0)
        self.assertEqual(ps.forward_pending_count(), 1)


if __name__ == "__main__":
    unittest.main()
