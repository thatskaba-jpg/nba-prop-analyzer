import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import export_gamelines as ex  # noqa: E402

ENTRIES = [
    {"tip": "6:00 PM ET", "away": "NYK", "home": "BOS", "fav": "BOS",
     "margin": 5.1, "p_home_win": 0.62, "p_away_win": 0.38,
     "ml_pick": None, "sp_pick": None, "skip": False,
     "ml_home": -270, "ml_away": 220, "spread_home": -6.5, "total": 225.5},
    {"tip": "6:30 PM ET", "away": "HOU", "home": "SAS", "fav": "SAS",
     "margin": 2.5, "p_home_win": 0.58, "p_away_win": 0.42,
     "ml_pick": "HOU +135 (underdog value)", "sp_pick": "HOU +3.75",
     "skip": False, "ml_home": -160, "ml_away": 135, "spread_home": -3.75,
     "total": 225.5},
    {"tip": "7:00 PM ET", "away": "LAC", "home": "LAL", "fav": "LAL",
     "margin": 9.0, "p_home_win": 0.77, "p_away_win": 0.23,
     "ml_pick": "LAC +300", "sp_pick": None, "skip": True,
     "ml_home": -390, "ml_away": 300, "spread_home": -9.5, "total": 228.5},
]


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.rows = ex.build_rows(ENTRIES)

    def test_full_names_and_picks(self):
        r = self.rows[1]
        self.assertEqual(r["Away"], "Houston Rockets")
        self.assertEqual(r["Home"], "San Antonio Spurs")
        self.assertEqual(r["Spread pick"], "Houston Rockets +3.75")
        self.assertIn("Houston Rockets +135", r["Moneyline pick"])

    def test_no_edge_and_skip_wording(self):
        self.assertEqual(self.rows[0]["Spread pick"], "no edge")
        self.assertIn("Boston Celtics", self.rows[0]["Moneyline pick"])
        self.assertTrue(self.rows[2]["Moneyline pick"].startswith("skip"))

    def test_text_has_disclaimer_and_marks_picks(self):
        t = ex.to_text(self.rows, "Fri Oct 23")
        self.assertIn("1 pick(s) out of 3", t)
        self.assertIn("✅ 6:30 PM ET — Houston Rockets @ San Antonio Spurs", t)
        self.assertNotIn("✅ 6:00 PM", t)
        self.assertIn("NOT beaten the sportsbook", t)
        self.assertNotIn(" HOU ", t)       # no raw abbreviations left over

    def test_csv_roundtrip(self):
        out = list(csv.DictReader(io.StringIO(ex.to_csv(self.rows))))
        self.assertEqual(len(out), 3)
        self.assertEqual(out[1]["Home"], "San Antonio Spurs")

    def test_top_picks_ranked_by_edge_and_skip_excluded(self):
        es = [dict(e) for e in ENTRIES]
        es[1].update(ml_edge=3.0, ml_prob=0.45, sp_edge=6.5, sp_cover=0.57)
        es[2].update(ml_edge=9.0, ml_prob=0.5, skip=True)   # skipped game
        top = ex.top_picks(es)
        self.assertEqual([t["Type"] for t in top], ["Spread", "Moneyline"])
        self.assertEqual(top[0]["Confidence"], "Solid")
        self.assertEqual(top[0]["Chance"], "57%")
        self.assertEqual(top[0]["Pick"], "Houston Rockets +3.75")
        self.assertEqual(top[1]["Rank"], 2)

    def test_leans_skip_disagreeing_games_and_rank_by_win_chance(self):
        lean = ex.top_leans(ENTRIES)
        self.assertEqual(lean[0]["Lean"], "Boston Celtics")   # 62% (LAL game skipped)
        self.assertNotIn("Los Angeles Lakers", [l["Lean"] for l in lean])

    def test_text_includes_top_section(self):
        es = [dict(e) for e in ENTRIES]
        es[1].update(sp_edge=6.5, sp_cover=0.57, ml_edge=None)
        rows = ex.build_rows(es)
        t = ex.to_text(rows, "Fri", top=ex.top_picks(es))
        self.assertIn("Most confident picks", t)
        self.assertIn("1. Houston Rockets +3.75 (Spread)", t)

    def test_top_ml_ranks_by_win_chance_and_shows_price(self):
        es = [dict(e) for e in ENTRIES]
        es[1].update(ml_pick="SAS to win", ml_team="SAS")   # edge on the winner
        ml = ex.top_ml(es)
        self.assertEqual([m["Winner"] for m in ml],
                         ["Boston Celtics", "San Antonio Spurs"])  # LAL skipped
        self.assertEqual(ml[0]["Moneyline"], "-270")
        self.assertEqual(ml[0]["Win chance"], "62%")
        self.assertEqual(ml[0]["Price edge"], "none")
        self.assertEqual(ml[1]["Price edge"], "✅ value")

    def test_underdog_value_is_not_labelled_as_favorite_value(self):
        es = [dict(e) for e in ENTRIES]
        es[1].update(ml_pick="HOU +135 (underdog value)", ml_team="HOU")
        ml = ex.top_ml(es)
        row = [m for m in ml if m["Winner"] == "San Antonio Spurs"][0]
        self.assertEqual(row["Price edge"], "value on Houston Rockets")
        self.assertNotIn("good price", ex.to_text(ex.build_rows(es), "Fri", ml=ml)
                         .split("San Antonio Spurs -160")[1].split("\n")[0])

    def test_text_includes_ml_section(self):
        rows = ex.build_rows(ENTRIES)
        t = ex.to_text(rows, "Fri", ml=ex.top_ml(ENTRIES))
        self.assertIn("Moneyline: most likely winners", t)
        self.assertIn("1. Boston Celtics -270 — 62% to win", t)

    def test_top_alts(self):
        es = [dict(e) for e in ENTRIES]
        es[1]["alt"] = {"side": "away", "line": 7.5, "cover": 0.66, "take_at": -167}
        alts = ex.top_alts(es)
        self.assertEqual(alts[0]["Pick"], "Houston Rockets +7.5")
        self.assertEqual(alts[0]["Cover"], "66%")
        self.assertEqual(alts[0]["Take at"], "-167")
        t = ex.to_text(ex.build_rows(es), "Fri", alts=alts)
        self.assertIn("Alt spreads worth a look", t)

    def test_csv_rows_are_tidy(self):
        es = [dict(e) for e in ENTRIES]
        es[1].update(sp_pick="HOU +3.5 to +4", alt={"side": "away", "line": 4.5,
                                               "cover": .57, "take_at": -116})
        rows = ex.csv_rows(es)
        self.assertEqual(list(rows[1]), ["Time", "Game", "Moneyline pick", "Spread",
                                         "Alt spread", "Book lines"])
        self.assertEqual(rows[1]["Spread"], "Houston Rockets +3.5 to +4 (value)")
        self.assertEqual(rows[1]["Alt spread"], "Houston Rockets +4.5 (take at -116 or better)")
        self.assertEqual(rows[0]["Spread"], "no edge")
        self.assertEqual(rows[2]["Spread"], "skip (book disagrees)")
        self.assertIn("(62%)", rows[0]["Moneyline pick"])

    def test_empty(self):
        self.assertIn("No open games", ex.to_text([], "today"))
        self.assertEqual(ex.to_csv([]), "")


if __name__ == "__main__":
    unittest.main()
