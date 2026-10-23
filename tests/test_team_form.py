"""Team form for the game model: the one-pass version must match the per-team one."""

import unittest

import helpers
from models import team_form as tf


class TeamFormTests(helpers.TempDBTestCase):
    def game(self, gid, date, home, away, home_pts, away_pts):
        from database import get_connection
        conn = get_connection()
        for pid, team, opp, is_home, pts in ((1, home, away, 1, home_pts),
                                             (2, away, home, 0, away_pts)):
            matchup = f"{team} vs {opp}" if is_home else f"{team} @ {opp}"
            conn.execute(
                "INSERT INTO player_game_logs (player_id, player_name, game_id, "
                "game_date, season, matchup, is_home, opponent, minutes, points) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid * 100 + int(gid[1:]), f"P{pid}{gid}", gid, date, "2025",
                 matchup, is_home, opp, 30, pts))
        conn.commit()
        conn.close()

    def setUp(self):
        super().setUp()
        self.game("g1", "2026-01-01", "AAA", "BBB", 100, 90)    # AAA +10
        self.game("g2", "2026-01-03", "BBB", "AAA", 105, 95)    # AAA -10
        self.game("g3", "2026-01-05", "AAA", "BBB", 110, 100)   # AAA +10
        self.game("g4", "2026-01-08", "AAA", "BBB", 150, 80)    # on the cutoff: excluded

    def test_margin_and_rest_for_every_team_in_one_pass(self):
        m = tf.team_form_map(("AAA", "BBB"), "2026-01-08")
        self.assertAlmostEqual(m["AAA"]["recent_margin"], 3.33, places=2)
        self.assertAlmostEqual(m["BBB"]["recent_margin"], -3.33, places=2)
        self.assertEqual(m["AAA"]["rest_days"], 3)

    def test_matches_the_single_team_functions(self):
        m = tf.team_form_map(("AAA", "BBB"), "2026-01-08")
        for team in ("AAA", "BBB"):
            self.assertEqual(m[team]["recent_margin"], tf.recent_margin(team, "2026-01-08"))
            self.assertEqual(m[team]["rest_days"], tf.rest_days(team, "2026-01-08"))

    def test_window_only_counts_the_last_n_games(self):
        m = tf.team_form_map(("AAA",), "2026-01-08", n=1)
        self.assertEqual(m["AAA"]["recent_margin"], 10.0)        # just g3

    def test_unknown_teams_have_no_form_not_an_error(self):
        m = tf.team_form_map(("ZZZ", "", None), "2026-01-08")
        self.assertEqual(m, {"ZZZ": {"recent_margin": None, "rest_days": None}})


if __name__ == "__main__":
    unittest.main()
