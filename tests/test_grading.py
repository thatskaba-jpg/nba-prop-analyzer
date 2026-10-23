"""
Grading: a player who didn't play (DNP) must be VOIDED, never graded as an
under; pushes aren't wins or losses; a game that isn't logged yet stays pending.
These guard the bug that graded ~4% of all stored lines as unders.
"""

import unittest

import helpers
from grading import FINAL, PENDING, VOID, did_play, settle_stat

COMBOS = {"pra": ("points", "rebounds", "assists")}
SINGLES = {"points", "rebounds", "assists"}


class SettleStatTests(unittest.TestCase):
    def test_missing_row_is_pending(self):
        self.assertEqual(settle_stat(None, "points", COMBOS, SINGLES),
                         (PENDING, None))

    def test_zero_minutes_is_void_not_a_zero_stat(self):
        row = {"minutes": 0, "points": 0, "rebounds": 0, "assists": 0}
        self.assertEqual(settle_stat(row, "points", COMBOS, SINGLES), (VOID, None))

    def test_missing_minutes_is_void(self):
        row = {"minutes": None, "points": 0, "rebounds": 0, "assists": 0}
        self.assertEqual(settle_stat(row, "points", COMBOS, SINGLES), (VOID, None))

    def test_played_game_returns_the_stat(self):
        row = {"minutes": 31.5, "points": 22, "rebounds": 5, "assists": 4}
        self.assertEqual(settle_stat(row, "points", COMBOS, SINGLES), (FINAL, 22.0))

    def test_combo_sums_its_parts(self):
        row = {"minutes": 31.5, "points": 20, "rebounds": 5, "assists": 4}
        self.assertEqual(settle_stat(row, "pra", COMBOS, SINGLES), (FINAL, 29.0))

    def test_blank_stat_on_a_played_game_stays_pending(self):
        row = {"minutes": 30, "points": None, "rebounds": 5, "assists": 4}
        self.assertEqual(settle_stat(row, "points", COMBOS, SINGLES),
                         (PENDING, None))

    def test_did_play(self):
        self.assertTrue(did_play(0.5))
        for not_played in (0, 0.0, None, "0", "not a number"):
            self.assertFalse(did_play(not_played), not_played)


class StoredLineGradingTests(helpers.TempDBTestCase):
    def setUp(self):
        super().setUp()
        from odds import line_capture
        self.lc = line_capture
        self.lc.ensure_table()

    def add_prop(self, player_id, game_id, line, actual=None, hit_over=None,
                 graded_at=None):
        from database import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO historical_props (captured_at, game_date, game_id, "
            "player_id, prop_type, book, snapshot, line, odds_over, odds_under, "
            "actual, hit_over, graded_at) VALUES ('2025-11-01T00:00:00', "
            "'2025-11-02', ?, ?, 'points', 'fanduel', 'opening', ?, -110, -110, "
            "?, ?, ?)",
            (game_id, player_id, line, actual, hit_over, graded_at))
        conn.commit()
        conn.close()

    def prop_row(self, player_id, line):
        return self.fetch_all(
            "SELECT actual, hit_over, graded_at FROM historical_props "
            "WHERE player_id=? AND line=?", (player_id, line))[0]

    def test_dnp_is_voided_not_graded_as_an_under(self):
        self.add_log(1, 100, "2025-11-02", minutes=0, points=0)
        self.add_prop(1, 100, 9.5)
        summary = self.lc.grade_settled()
        row = self.prop_row(1, 9.5)
        self.assertIsNone(row["actual"])
        self.assertIsNone(row["hit_over"])
        self.assertIsNotNone(row["graded_at"])   # processed: not re-checked daily
        self.assertEqual(summary["voided"], 1)
        self.assertEqual(summary["graded"], 0)

    def test_played_game_grades_over_under_and_push(self):
        self.add_log(2, 100, "2025-11-02", minutes=30, points=25)
        for line in (24.5, 25.5, 25.0):
            self.add_prop(2, 100, line)
        self.lc.grade_settled()
        self.assertEqual(self.prop_row(2, 24.5)["hit_over"], 1)
        self.assertEqual(self.prop_row(2, 25.5)["hit_over"], 0)
        push = self.prop_row(2, 25.0)
        self.assertIsNone(push["hit_over"])
        self.assertEqual(push["actual"], 25.0)

    def test_unlogged_game_stays_pending(self):
        self.add_prop(3, 100, 20.5)
        summary = self.lc.grade_settled()
        self.assertIsNone(self.prop_row(3, 20.5)["graded_at"])
        self.assertEqual(summary["still_pending"], 1)

    def test_repairs_dnps_that_were_graded_as_unders(self):
        # Graded the OLD way: actual=0 -> under, for a player who never played.
        self.add_log(4, 100, "2025-11-02", minutes=0, points=0)
        self.add_prop(4, 100, 8.5, actual=0.0, hit_over=0,
                      graded_at="2025-11-03T00:00:00")
        # A real, correctly graded line for a player who did play: untouched.
        self.add_log(5, 100, "2025-11-02", minutes=28, points=12)
        self.add_prop(5, 100, 10.5, actual=12.0, hit_over=1,
                      graded_at="2025-11-03T00:00:00")
        self.assertEqual(self.lc.void_dnp_props(), 1)
        bad = self.prop_row(4, 8.5)
        self.assertIsNone(bad["hit_over"])
        self.assertIsNone(bad["actual"])
        good = self.prop_row(5, 10.5)
        self.assertEqual(good["hit_over"], 1)
        self.assertEqual(good["actual"], 12.0)
        self.assertEqual(self.lc.void_dnp_props(), 0)   # idempotent

    def test_coverage_reports_voids_separately(self):
        self.add_log(1, 100, "2025-11-02", minutes=0)
        self.add_log(2, 100, "2025-11-02", minutes=30, points=25)
        self.add_prop(1, 100, 9.5)
        self.add_prop(2, 100, 24.5)
        self.lc.grade_settled()
        cov = self.lc.coverage()
        self.assertEqual(cov["rows_graded"], 1)
        self.assertEqual(cov["rows_voided"], 1)


class PredictionTrackerTests(helpers.TempDBTestCase):
    def log(self, **kw):
        from models.prediction_tracker import log_prediction
        args = dict(player_id=1, player_name="A", game_date="2025-11-02",
                    prop_type="points", line=24.5, projection=25.0,
                    pick="over", prob=0.6, model_name="m")
        args.update(kw)
        return log_prediction(**args)

    def test_first_write_wins(self):
        self.assertTrue(self.log(pick="over", prob=0.6))
        self.assertFalse(self.log(pick="under", prob=0.9, projection=10.0))
        row = self.fetch_all("SELECT pick, prob, projection FROM predictions")[0]
        self.assertEqual((row["pick"], row["prob"], row["projection"]),
                         ("over", 0.6, 25.0))

    def test_dnp_prediction_is_voided_not_a_win(self):
        from models.prediction_tracker import grade_pending, tracking_summary
        self.add_log(1, 100, "2025-11-02", minutes=0)
        self.log(line=9.5, pick="under")
        self.assertEqual(grade_pending(), 0)
        s = tracking_summary("m")
        self.assertEqual((s["graded"], s["pending"], s["void"]), (0, 0, 1))

    def test_grades_win_loss_and_push(self):
        from models.prediction_tracker import grade_pending, tracking_summary
        self.add_log(1, 100, "2025-11-02", minutes=30, points=25)
        self.log(model_name="m1", line=24.5, pick="over")     # 25 > 24.5: win
        self.log(model_name="m2", line=24.5, pick="under")    # loss
        self.log(model_name="m3", line=25.0, pick="over")     # push
        self.assertEqual(grade_pending(), 3)
        rows = {r["model_name"]: r for r in self.fetch_all(
            "SELECT model_name, correct, result FROM predictions")}
        self.assertEqual(rows["m1"]["correct"], 1)
        self.assertEqual(rows["m2"]["correct"], 0)
        self.assertIsNone(rows["m3"]["correct"])
        self.assertEqual(rows["m3"]["result"], 25.0)
        s = tracking_summary()
        self.assertEqual((s["graded"], s["accuracy"], s["pending"], s["void"]),
                         (2, 0.5, 0, 0))

    def test_ungraded_game_stays_pending(self):
        from models.prediction_tracker import grade_pending, tracking_summary
        self.log()
        self.assertEqual(grade_pending(), 0)
        self.assertEqual(tracking_summary()["pending"], 1)

    def test_old_pending_predictions_are_flagged_as_stale(self):
        import datetime
        from models.prediction_tracker import tracking_summary
        today = datetime.date.today().isoformat()
        self.log(game_date="2020-01-01", model_name="old")   # a date with no game
        self.log(game_date=today, model_name="new")          # tonight: just pending
        s = tracking_summary()
        self.assertEqual((s["pending"], s["stale"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
