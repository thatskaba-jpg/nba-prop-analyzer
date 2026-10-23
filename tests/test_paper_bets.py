"""The forward paper-trading record: selection rules, logging, grading, CLV."""

import unittest
from unittest import mock

import helpers
from models import paper_bets as pb
from odds import market_anchor as ma

MARKET_ONLY = {"intercept": 0.0, "w_market": 1.0, "w_model": 0.0}


def row(book, over, under, line=24.5, at="2026-10-22T15:00:00", game_id=1,
        player_id=7, prop="points"):
    return {"game_id": game_id, "player_id": player_id, "prop_type": prop,
            "book": book, "line": line, "odds_over": over, "odds_under": under,
            "captured_at": at}


def fake_model(p_over=0.5, push=0.0):
    return lambda pid, prop, line: {"p_over": p_over, "p_under": 1 - p_over - push,
                                    "p_over_raw": p_over, "push": push}


def by_rule(bets, rule):
    return [b for b in bets if b["rule"] == rule]


class SelectionTests(unittest.TestCase):
    def test_a_stale_under_at_one_book_is_found(self):
        rows = [row("a", -110, -110), row("b", -110, -110), row("c", -110, -110),
                row("d", -150, 120)]                    # d's under is the outlier
        bets = pb.plan_bets({(1, 7, "points"): rows}, fake_model(), weights=MARKET_ONLY)
        anchor = by_rule(bets, pb.RULE_ANCHOR_SHOP)
        self.assertEqual(len(anchor), 1)
        self.assertEqual((anchor[0]["book"], anchor[0]["side"], anchor[0]["odds"]),
                         ("d", "under", 120))
        self.assertGreaterEqual(anchor[0]["ev"], pb.ANCHOR_MIN_EV)

    def test_no_bet_when_every_book_agrees(self):
        rows = [row(b, -110, -110) for b in "abcd"]
        bets = pb.plan_bets({(1, 7, "points"): rows}, fake_model(), weights=MARKET_ONLY)
        self.assertEqual(by_rule(bets, pb.RULE_ANCHOR_SHOP), [])

    def test_a_consensus_needs_enough_books(self):
        rows = [row("a", -110, -110), row("d", -150, 120)]
        bets = pb.plan_bets({(1, 7, "points"): rows}, fake_model(), weights=MARKET_ONLY)
        self.assertEqual(by_rule(bets, pb.RULE_ANCHOR_SHOP), [])

    def test_the_old_rule_flags_a_confident_model(self):
        rows = [row(b, -110, -110) for b in "ab"]
        bets = pb.plan_bets({(1, 7, "points"): rows}, fake_model(0.70), weights=MARKET_ONLY)
        old = by_rule(bets, pb.RULE_MODEL_ONLY)
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["side"], "over")

    def test_invalid_prices_are_ignored(self):
        rows = [row("a", 0, 0), row("b", None, -110)]
        self.assertEqual(pb.plan_bets({(1, 7, "points"): rows}, fake_model(0.9),
                                      weights=MARKET_ONLY), [])

    def test_best_price_accounts_for_a_push(self):
        # 10% push: the push refunds the stake, so it must not count as a loss.
        best = pb.choose_best_price([row("a", -110, -110)], 0.45, 0.45,
                                    push=0.10, min_ev=-1.0)
        self.assertAlmostEqual(best["ev"], 0.45 * (100 / 110) - 0.45, places=6)


class LoggingAndGradingTests(helpers.TempDBTestCase):
    GAMES = [{"id": 1, "home": "AAA", "away": "BBB", "open": True}]
    ROWS = [row("a", -110, -110), row("b", -110, -110), row("c", -110, -110),
            row("d", -150, 120)]          # book d's under is priced well above the rest

    def setUp(self):
        super().setUp()
        from odds import line_capture
        line_capture.ensure_table()
        pb.ensure_tables()

    def log(self, games=None, rows=None):
        # Pin the anchor weights so the test doesn't depend on the saved file.
        pinned = dict(MARKET_ONLY, fitted=None)
        with mock.patch.object(ma, "load_weights", return_value=pinned):
            return pb.log_paper_bets(
                "2026-10-22", games=games or self.GAMES, rows=rows or self.ROWS,
                names={7: "Player Seven"}, roster={"Player Seven": "AAA"},
                model_fn=fake_model(), fitted_fn=None)

    def bets(self):
        return self.fetch_all("SELECT * FROM paper_bets ORDER BY id")

    def anchor_bet(self):
        return by_rule(self.bets(), pb.RULE_ANCHOR_SHOP)[0]

    def test_logs_once_and_the_first_one_wins(self):
        self.assertEqual(self.log()["logged"], 2)       # one per rule
        self.assertEqual(self.log()["logged"], 0)       # second look changes nothing
        b = self.anchor_bet()
        self.assertEqual((b["side"], b["book"], b["odds"]), ("under", "d", 120))
        self.assertEqual(b["player_name"], "Player Seven")

    def test_games_that_have_started_are_never_logged(self):
        started = [dict(self.GAMES[0], open=False)]
        self.assertEqual(self.log(games=started)["logged"], 0)
        self.assertEqual(self.bets(), [])

    def test_a_win_pays_the_price_taken(self):
        self.log()                                              # under 24.5 at +120
        self.add_log(7, 1, "2026-10-22", minutes=30, points=20)  # under wins
        self.assertEqual(pb.grade_paper_bets()["graded"], 2)
        b = self.anchor_bet()
        self.assertEqual(b["won"], 1)
        self.assertAlmostEqual(b["profit"], 1.2, places=4)

    def test_a_loss_costs_one_unit(self):
        self.log()
        self.add_log(7, 1, "2026-10-22", minutes=30, points=30)  # went over
        pb.grade_paper_bets()
        b = self.anchor_bet()
        self.assertEqual((b["won"], b["profit"]), (0, -1.0))

    def test_a_push_returns_the_stake(self):
        self.log()
        self.add_log(7, 1, "2026-10-22", minutes=30, points=24.5)
        pb.grade_paper_bets()
        b = self.anchor_bet()
        self.assertIsNone(b["won"])
        self.assertEqual(b["profit"], 0.0)

    def test_a_dnp_is_voided(self):
        self.log()
        self.add_log(7, 1, "2026-10-22", minutes=0)
        out = pb.grade_paper_bets()
        self.assertEqual((out["graded"], out["voided"]), (0, 2))
        b = self.anchor_bet()
        self.assertIsNone(b["profit"])
        self.assertIsNotNone(b["graded_at"])

    def test_an_ungraded_game_stays_open(self):
        self.log()
        self.assertEqual(pb.grade_paper_bets(), {"graded": 0, "voided": 0})

    def add_close(self, over, under, line=24.5, at="2026-10-22T22:00:00"):
        from database import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO historical_props (captured_at, game_date, game_id, "
            "player_id, prop_type, book, snapshot, line, odds_over, odds_under) "
            "VALUES (?, '2026-10-22', 1, 7, 'points', 'd', 'live', ?, ?, ?)",
            (at, line, over, under))
        conn.commit()
        conn.close()

    def test_closing_line_value_is_positive_when_the_price_improved(self):
        self.log()                                     # took the under at +120 (45.5%)
        self.add_close(over=120, under=-140)           # it later closes at -140
        self.add_log(7, 1, "2026-10-22", minutes=30, points=20)
        pb.grade_paper_bets()
        b = self.anchor_bet()
        self.assertGreater(b["clv_prob"], 0.05)
        self.assertEqual(b["clv_line"], 0.0)

    def test_closing_line_value_in_points_when_the_line_moves(self):
        self.log()
        self.add_close(over=-110, under=-110, line=23.5)    # the line fell a point
        self.add_log(7, 1, "2026-10-22", minutes=30, points=20)
        pb.grade_paper_bets()
        b = self.anchor_bet()
        self.assertEqual(b["clv_line"], 1.0)           # we sold the under at 24.5
        self.assertIsNone(b["clv_prob"])               # different number: no price CLV

    def test_no_clv_without_a_later_snapshot(self):
        self.log()
        self.add_log(7, 1, "2026-10-22", minutes=30, points=20)
        pb.grade_paper_bets()
        b = self.anchor_bet()
        self.assertIsNone(b["clv_prob"])
        self.assertIsNone(b["clv_line"])

    def test_summary_reports_roi_and_clv(self):
        self.log()
        self.add_close(over=120, under=-140)
        self.add_log(7, 1, "2026-10-22", minutes=30, points=20)
        pb.grade_paper_bets()
        s = {r["rule"]: r for r in pb.summary()}
        anchor = s[pb.RULE_ANCHOR_SHOP]
        self.assertEqual((anchor["logged"], anchor["settled"]), (1, 1))
        self.assertAlmostEqual(anchor["roi"], 1.2, places=4)
        self.assertEqual(anchor["pct_beat_close"], 1.0)


if __name__ == "__main__":
    unittest.main()
