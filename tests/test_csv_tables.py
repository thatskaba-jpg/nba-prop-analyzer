import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import csv_tables as c  # noqa: E402


def r(date, game, model, pick, result, hit):
    return {"Date": date, "Game": game, "Model": model, "Model pick": pick,
            "Actual result": result, "Hit?": hit}


ROWS = [
    r("2026-10-20", "BOS @ DET", "Balanced", "BOS by 0.4", "DET won by 8 (121-113)", "❌ LOSS"),
    r("2026-10-20", "BOS @ DET", "Trust the ratings (bold)", "DET by 0.1", "DET won by 8 (121-113)", "✅ WIN"),
    r("2026-10-21", "OKC @ SAS", "Balanced", "OKC by 5.4", "OKC won by 12 (116-104)", "✅ WIN"),
    r("2026-10-21", "OKC @ SAS", "Trust the ratings (bold)", "OKC by 6.5", "OKC won by 12 (116-104)", "✅ WIN"),
]


class MlWideTests(unittest.TestCase):
    def test_one_row_per_game_newest_first_plus_record(self):
        out = c.ml_picks_wide(ROWS)
        self.assertEqual(len(out), 4)                     # 2 games + RECORD + WIN %
        self.assertEqual(out[0]["Game"], "Oklahoma City Thunder @ San Antonio Spurs")
        self.assertEqual(out[1]["Game"], "Boston Celtics @ Detroit Pistons")
        self.assertEqual(out[2]["Game"], "RECORD (right-wrong)")
        self.assertEqual(out[3]["Game"], "WIN %")

    def test_cells_and_columns(self):
        out = c.ml_picks_wide(ROWS)
        g = out[1]
        self.assertEqual(g["Balanced"], "BOS ❌")
        self.assertEqual(g["Trust ratings"], "DET ✅")
        self.assertNotIn("Models right", g)               # totals go under the table
        self.assertEqual(g["Final"], "Detroit Pistons won 121-113")
        self.assertEqual(list(g)[:3], ["Date", "Game", "Final"])

    def test_record_row(self):
        out = c.ml_picks_wide(ROWS)
        rec, pct = out[-2], out[-1]
        self.assertEqual(rec["Balanced"], "1-1")
        self.assertEqual(rec["Trust ratings"], "2-0")
        self.assertEqual(pct["Balanced"], "50%")
        self.assertEqual(pct["Trust ratings"], "100%")

    def test_empty(self):
        self.assertEqual(c.ml_picks_wide([]), [])


class AtsTests(unittest.TestCase):
    def test_graded_and_pending(self):
        rows = [
            {"game_id": "CLE @ PHI|2026-10-22", "home": "PHI", "away": "CLE",
             "spread_home": -3.5, "our_side": "home", "proj_margin": 9.2,
             "actual_margin": 8, "covered_side": "home", "result": "win"},
            {"game_id": "HOU @ SAS|2026-10-23", "home": "SAS", "away": "HOU",
             "spread_home": -5.5, "our_side": "away", "proj_margin": 3.9,
             "actual_margin": None, "covered_side": None, "result": "pending"},
        ]
        out = c.ats_picks_table(rows)
        self.assertEqual(out[0]["Result"], "⏳ Pending")          # pending first
        self.assertEqual(out[0]["Our pick"], "Houston Rockets +5.5")
        self.assertEqual(out[0]["Book line"], "SAS -5.5")
        self.assertEqual(out[0]["Our margin"], "SAS by 3.9")
        self.assertEqual(out[1]["Result"], "✅ Covered")
        self.assertEqual(out[1]["Final"], "PHI won by 8")
        self.assertEqual(out[1]["Our pick"], "Philadelphia 76ers -3.5")


if __name__ == "__main__":
    unittest.main()


class ScorecardTests(unittest.TestCase):
    ATS = [
        {"game_id": "BOS @ DET|2026-10-20", "home": "DET", "away": "BOS", "spread_home": -1.5,
         "our_side": "away", "proj_margin": -0.4, "actual_margin": 8, "result": "loss"},
        {"game_id": "CLE @ PHI|2026-10-22", "home": "PHI", "away": "CLE", "spread_home": -3.5,
         "our_side": "home", "proj_margin": 9.2, "actual_margin": 8, "result": "win"},
    ]

    def test_one_row_per_game_with_both_logs(self):
        games, foot = c.scorecard(ROWS, self.ATS)
        by_game = {r["Game"]: r for r in games}
        det = by_game["Boston Celtics @ Detroit Pistons"]
        self.assertEqual(det["Balanced"], "BOS ❌")
        self.assertEqual(det["Spread result"], "❌ Missed")
        self.assertEqual(det["Spread pick"], "Boston Celtics +1.5")
        # ATS-only game (no moneyline log) still appears
        phi = by_game["Cleveland Cavaliers @ Philadelphia 76ers"]
        self.assertEqual(phi["Spread result"], "✅ Covered")
        self.assertEqual(phi["Balanced"], "—")
        self.assertEqual(phi["Final"], "Philadelphia 76ers won by 8")
        # ML-only game
        okc = by_game["Oklahoma City Thunder @ San Antonio Spurs"]
        self.assertEqual(okc["Spread result"], "not tracked")

    def test_footer_has_ml_and_spread_totals(self):
        games, foot = c.scorecard(ROWS, self.ATS)
        rec, pct = foot
        self.assertEqual(rec["Spread result"], "1-1")
        self.assertEqual(pct["Spread result"], "50%")
        self.assertEqual(rec["Balanced"], "1-1")

    def test_agreement_counts_only_games_with_both(self):
        games, _ = c.scorecard(ROWS, self.ATS)
        ag = {a["Combination"]: a["Games"] for a in c.agreement(games, "Balanced")}
        self.assertEqual(sum(ag.values()), 1)              # only BOS @ DET has both
        self.assertEqual(ag["Winner wrong AND missed the spread"], 1)
