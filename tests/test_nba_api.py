import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collect import nba_api as n  # noqa: E402

PAYLOAD = {"scoreboard": {"gameDate": "2026-10-23", "games": [
    {"gameStatus": 1, "gameTimeUTC": "2026-10-23T23:30:00Z",
     "homeTeam": {"teamTricode": "SAS", "score": 0},
     "awayTeam": {"teamTricode": "HOU", "score": 0}},
    {"gameStatus": 2, "gameStatusText": "Q2 5:30",
     "homeTeam": {"teamTricode": "NYK", "score": 50},
     "awayTeam": {"teamTricode": "BOS", "score": 48}},
    {"gameStatus": 3, "gameStatusText": "Final",
     "homeTeam": {"teamTricode": "UTA", "score": 110},
     "awayTeam": {"teamTricode": "NOP", "score": 99}}]}}


class NbaApiTests(unittest.TestCase):
    def test_schedule_statuses(self):
        with mock.patch.object(n, "scoreboard", return_value=PAYLOAD):
            s = n.schedule("2026-10-23")
        self.assertEqual(s["HOU @ SAS"], "2026-10-23T23:30:00Z")
        self.assertEqual(s["BOS @ NYK"], "Q2 5:30")
        self.assertEqual(s["NOP @ UTA"], "Final")

    def test_scores_skip_unstarted(self):
        with mock.patch.object(n, "scoreboard", return_value=PAYLOAD):
            sc = n.scores("2026-10-23")
        self.assertNotIn("HOU @ SAS", sc)
        self.assertEqual(sc["NOP @ UTA"], {"home": 110, "away": 99, "final": True})
        self.assertFalse(sc["BOS @ NYK"]["final"])

    def test_other_date_returns_nothing(self):
        with mock.patch.object(n, "scoreboard", return_value=PAYLOAD):
            self.assertEqual(n.schedule("2026-10-24"), {})

    def test_failure_returns_empty(self):
        with mock.patch.object(n, "scoreboard", side_effect=OSError("down")):
            self.assertEqual(n.scores("2026-10-23"), {})


ODDS = {"games": [{"gameId": "0022600001", "markets": [
    {"name": "2way", "books": [
        {"countryCode": "US", "outcomes": [{"type": "home", "odds": "1.625"},
                                           {"type": "away", "odds": "2.350"}]},
        {"countryCode": "AUS", "outcomes": [{"type": "home", "odds": "9.0"},
                                            {"type": "away", "odds": "1.1"}]}]},
    {"name": "spread", "books": [
        {"countryCode": "US", "outcomes": [{"type": "home", "spread": "-3.5"},
                                           {"type": "away", "spread": "3.5"}]}]}]}]}
SB = {"scoreboard": {"gameDate": "2026-10-23", "games": [
    {"gameId": "0022600001", "gameStatus": 1, "gameTimeUTC": "x",
     "homeTeam": {"teamTricode": "SAS", "score": 0},
     "awayTeam": {"teamTricode": "HOU", "score": 0}}]}}


class OddsTests(unittest.TestCase):
    def test_decimal_to_american(self):
        self.assertEqual(n.decimal_to_american("2.350"), 135)
        self.assertEqual(n.decimal_to_american("1.625"), -160)
        self.assertIsNone(n.decimal_to_american("0.9"))

    def test_game_lines_uses_us_books_and_joins_scoreboard(self):
        with mock.patch.object(n, "scoreboard", return_value=SB), \
                mock.patch.object(n, "odds_payload", return_value=ODDS):
            m = n.game_lines("2026-10-23")
        self.assertEqual(m["SAS"]["moneyline"], -160)
        self.assertEqual(m["HOU"]["moneyline"], 135)
        self.assertEqual(m["SAS"]["spread"], -3.5)
        self.assertEqual(m["HOU"]["spread"], 3.5)
        self.assertEqual(m["SAS"]["source"], "nba.com")

    def test_unexpected_shape_returns_empty(self):
        with mock.patch.object(n, "scoreboard", return_value=SB), \
                mock.patch.object(n, "odds_payload", return_value={"x": 1}):
            self.assertEqual(n.game_lines("2026-10-23"), {})


if __name__ == "__main__":
    unittest.main()
