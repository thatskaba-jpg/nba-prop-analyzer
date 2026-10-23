"""
Profile scoreboard: a game must be graded exactly once, even when the live feed
(int ids) and the DB fallback (text ids) alternate. It used to count the same
game twice, so every profile showed 17 games when only 14 had been played.
"""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts src/ on sys.path)
from models import profile_scoreboard as ps

RATINGS = {"AAA": {"off": 118.0, "def": 108.0, "pace": 100.0},
           "BBB": {"off": 108.0, "def": 118.0, "pace": 100.0}}
# (game_id, date, home, away, home_pts, away_pts)
LIVE_GAMES = [(21717855, "2026-10-20", "AAA", "BBB", 110, 100),
              (21717856, "2026-10-20", "BBB", "AAA", 99, 105)]
DB_GAMES = [(str(g[0]),) + tuple(g[1:]) for g in LIVE_GAMES]   # ids come back as text


class ScoreboardIdTests(unittest.TestCase):
    def test_same_games_from_live_feed_then_db_fallback_are_graded_once(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            def patch(name, **kw):
                return stack.enter_context(mock.patch.object(ps, name, **kw))

            patch("_SCORE_PATH", new=Path(tmp) / "board.json")
            patch("_blend_ratings", return_value=RATINGS)
            patch("_finished_games", return_value=DB_GAMES)
            live = patch("_finished_games_live", return_value=LIVE_GAMES)
            patch("_positional_for_date", return_value={})
            patch("_four_factors_for_date", return_value={})
            patch("_form_for_date", return_value={})
            patch("_market_for_game", return_value=(None, 0))
            patch("_home_edges", return_value={})
            patch("_record_strength", return_value={})
            patch("_team_api", return_value={})

            ps.update_scoreboard(2026)               # graded from the live feed
            first = ps._load()
            live.return_value = []                   # feed down -> DB fallback
            ps.update_scoreboard(2026)
            second = ps._load()

        self.assertTrue(second["profiles"])
        for name, rec in second["profiles"].items():
            self.assertEqual(rec["total"], 2, name)
        self.assertEqual(len(second["graded_game_ids"]), 2)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
