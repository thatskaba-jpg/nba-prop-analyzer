import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collect import espn_api as e  # noqa: E402

# Trimmed from a real ESPN scoreboard response (shape verified live).
PRE = {"id": "1", "date": "2026-10-23T23:30Z",
       "status": {"type": {"state": "pre", "completed": False,
                           "shortDetail": "10/23 - 7:30 PM EDT"}},
       "competitions": [{
           "competitors": [
               {"homeAway": "home", "team": {"abbreviation": "SA"}, "score": "0"},
               {"homeAway": "away", "team": {"abbreviation": "HOU"}, "score": "0"}],
           "odds": [{
               "overUnder": 225.5, "spread": -3.5,
               "pointSpread": {"home": {"close": {"line": "-3.5", "odds": "-110"}},
                               "away": {"close": {"line": "+3.5", "odds": "-110"}}},
               "moneyline": {"home": {"close": {"odds": "-160"}},
                             "away": {"close": {"odds": "+135"}}},
               "total": {"over": {"close": {"line": "o225.5"}},
                         "under": {"close": {"line": "u225.5"}}}}]}]}
FINAL = {"id": "2", "date": "2026-10-22T23:00Z",
         "status": {"type": {"state": "post", "completed": True,
                             "shortDetail": "Final", "description": "Final"}},
         "competitions": [{
             "competitors": [
                 {"homeAway": "home", "team": {"abbreviation": "NY"}, "score": "114"},
                 {"homeAway": "away", "team": {"abbreviation": "WSH"}, "score": "106"}],
             "odds": None}]}
PAYLOAD = {"events": [PRE, FINAL]}


class EspnTests(unittest.TestCase):
    def test_abbreviations_mapped_to_bot_names(self):
        g = e.parse_games(PAYLOAD)
        self.assertEqual((g[0]["home"], g[0]["away"]), ("SAS", "HOU"))
        self.assertEqual((g[1]["home"], g[1]["away"]), ("NYK", "WAS"))

    def test_prestart_status_is_iso_with_seconds(self):
        g = e.parse_games(PAYLOAD)
        self.assertEqual(g[0]["status"], "2026-10-23T23:30:00Z")
        self.assertEqual(g[1]["status"], "Final")

    def test_game_lines_shape_and_signs(self):
        with mock.patch.object(e, "scoreboard", return_value=PAYLOAD):
            m = e.game_lines("2026-10-23")
        self.assertEqual(set(m), {"SAS", "HOU"})   # final game has no odds
        self.assertEqual(m["SAS"]["spread"], -3.5)
        self.assertEqual(m["HOU"]["spread"], 3.5)
        self.assertEqual(m["SAS"]["moneyline"], -160)
        self.assertEqual(m["HOU"]["moneyline"], 135)
        self.assertEqual(m["SAS"]["total"], 225.5)
        self.assertEqual(m["SAS"]["matchup"], "HOU @ SAS")
        self.assertTrue(m["SAS"]["is_home"])

    def test_flat_fields_fallback(self):
        odds = {"overUnder": 220.0, "spread": 2.0}
        self.assertEqual(e.parse_odds(odds), (2.0, -2.0, None, None, 220.0))

    def test_scores_and_schedule(self):
        with mock.patch.object(e, "scoreboard", return_value=PAYLOAD):
            sc = e.scores("d")
            sch = e.schedule("d")
        self.assertEqual(sc, {"WAS @ NYK": {"home": 114, "away": 106, "final": True}})
        self.assertIn("HOU @ SAS", sch)

    def test_failure_returns_empty(self):
        with mock.patch.object(e, "scoreboard", side_effect=OSError("down")):
            self.assertEqual(e.game_lines("d"), {})
            self.assertEqual(e.schedule("d"), {})


class FallbackTests(unittest.TestCase):
    def test_game_lines_falls_back_to_espn_when_balldontlie_empty(self):
        import odds.game_lines as gl
        with mock.patch.object(gl, "_from_balldontlie", return_value={}), \
                mock.patch.object(e, "scoreboard", return_value=PAYLOAD):
            m = gl.build_game_lines_map("2026-10-23")
        self.assertEqual(m["SAS"]["source"], "espn")

    def test_balldontlie_wins_when_it_has_data(self):
        import odds.game_lines as gl
        bdl = {"SAS": {"source": "balldontlie"}}
        with mock.patch.object(gl, "_from_balldontlie", return_value=bdl):
            self.assertIs(gl.build_game_lines_map("2026-10-23"), bdl)


class FailFastTests(unittest.TestCase):
    def test_429_raises_immediately_in_fail_fast_mode(self):
        import collect.balldontlie_client as bc
        resp = mock.Mock(status_code=429)
        with mock.patch.dict(os.environ, {"BDL_FAIL_FAST": "1"}), \
                mock.patch.object(bc, "API_KEY", "k"), \
                mock.patch.object(bc, "_throttle"), \
                mock.patch.object(bc.requests, "get", return_value=resp) as g, \
                mock.patch.object(bc.time, "sleep") as sl:
            with self.assertRaises(bc.BallDontLieError):
                bc._get("games")
            # cooling down: second call must not even hit the network
            with self.assertRaises(bc.BallDontLieError):
                bc._get("games")
            self.assertEqual(g.call_count, 1)
            sl.assert_not_called()
        bc._cooldown_until[0] = 0.0


if __name__ == "__main__":
    unittest.main()
