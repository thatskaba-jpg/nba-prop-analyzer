import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collect.prev_starters import prev_starters  # noqa: E402


def row(team, first, last, starter):
    return {"team": {"abbreviation": team}, "starter": starter,
            "player": {"first_name": first, "last_name": last}}


GAMES = {
    "2026-10-22": [{"id": 2, "status": "Final",
                    "home_team": {"abbreviation": "BOS"},
                    "visitor_team": {"abbreviation": "NYK"}},
                   {"id": 9, "status": "7:00 pm ET",   # not final: ignored
                    "home_team": {"abbreviation": "LAL"},
                    "visitor_team": {"abbreviation": "LAC"}}],
    "2026-10-21": [{"id": 1, "status": "Final",
                    "home_team": {"abbreviation": "BOS"},        # older BOS game
                    "visitor_team": {"abbreviation": "LAL"}}],
}
LINEUPS = {
    2: [row("BOS", "Jayson", "Tatum", True), row("BOS", "Sam", "Hauser", False),
        row("NYK", "Jalen", "Brunson", True)],
    1: [row("BOS", "Old", "Guy", True), row("LAL", "Luka", "Doncic", True)],
}


class PrevStartersTests(unittest.TestCase):
    def run_it(self):
        return prev_starters("2026-10-23", lambda d: GAMES.get(d, []),
                             lambda g: LINEUPS.get(g, []))

    def test_uses_most_recent_final_game_per_team(self):
        out = self.run_it()
        self.assertEqual(out["BOS"], ["Jayson Tatum"])      # not "Old Guy"
        self.assertEqual(out["NYK"], ["Jalen Brunson"])
        self.assertEqual(out["LAL"], ["Luka Doncic"])       # only has the older game

    def test_ignores_unfinished_games_and_bench(self):
        out = self.run_it()
        self.assertNotIn("LAC", out)
        self.assertNotIn("Sam Hauser", out["BOS"])

    def test_fetch_errors_are_skipped(self):
        def boom(d):
            raise RuntimeError("429")
        self.assertEqual(prev_starters("2026-10-23", boom, lambda g: []), {})


if __name__ == "__main__":
    unittest.main()
