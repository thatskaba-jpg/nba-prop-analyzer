import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import bet_text as b  # noqa: E402

ALTS = [{"team": "DAL", "line": 9.5, "cover": .66, "est_price": -126,
         "take_at": -171, "star": True},
        {"team": "DAL", "line": 8.5, "cover": .62, "est_price": -110,
         "take_at": -150, "star": False}]


class BetTextTests(unittest.TestCase):
    def test_fmt_line(self):
        self.assertEqual(b.fmt_line(6.5), "+6.5")
        self.assertEqual(b.fmt_line(-3), "-3")
        self.assertEqual(b.fmt_line(1.25), "+1 to +1.5")
        self.assertEqual(b.fmt_line(-3.75), "-3.5 to -4")

    def test_wins_if(self):
        self.assertIn("loses by 6 or fewer", b.wins_if("DAL", 6.5))
        self.assertIn("loses by 5 or fewer", b.wins_if("DAL", 5.75))
        self.assertIn("wins by 4 or more", b.wins_if("LAL", -3.5))

    def test_strength_labels(self):
        self.assertEqual(b.strength_label(.52), "toss-up")
        self.assertEqual(b.strength_label(.66), "solid")
        self.assertEqual(b.strength_label(.85), "heavy favorite")

    def test_title_always_leads_with_moneyline_pick(self):
        t = b.title_text(False, "IND", .66, None, None, None)
        self.assertEqual(t, " · 💰 ML: IND 66%")
        self.assertNotIn("no bet", t.lower())

    def test_title_with_everything(self):
        t = b.title_text(False, "IND", .66, {"team": "IND", "odds": -280},
                         {"team": "DAL", "line": 6.5, "cover": .57},
                         {"team": "DAL", "line": 9.5})
        self.assertEqual(t, " · 💰 ML: IND 66% ✅ good price · 📊 DAL +6.5 · 🔀 Alt: DAL +9.5")

    def test_title_prefixes(self):
        self.assertTrue(b.title_text(True, "LAL", .8, None, None, None)
                        .startswith(" · ⚠ book disagrees"))
        self.assertTrue(b.title_text(False, "A", .5, None, None, None, vs_market=True)
                        .startswith(" · ⚠ vs market"))

    def test_card_shows_ml_spread_and_ranked_alts(self):
        c = b.card_text(False, 0, "IND", .66, (-280, "DAL", 230), None,
                        {"team": "DAL", "line": 6.5, "cover": .57}, ALTS)
        self.assertIn("Moneyline pick: IND to win", c)
        self.assertIn("66% (solid)", c)
        self.assertIn("IND -280 / DAL +230", c)
        self.assertIn("Spread pick: DAL +6.5", c)
        self.assertIn("⭐ **1. DAL +9.5**", c)
        self.assertIn("**2. DAL +8.5**", c)
        self.assertIn("-171 or better", c)
        self.assertIn("can win the bet even if IND wins", c)

    def test_card_never_says_no_bet(self):
        c = b.card_text(False, 0, "BOS", .56, (-150, "NYK", 130), None, None, [])
        self.assertNotIn("No bet", c)
        self.assertIn("Moneyline pick: BOS to win", c)
        self.assertIn("no spread pick", c)
        self.assertIn("none look better than the main line", c)

    def test_alts_hidden_when_none_is_starred(self):
        alts = [dict(a, star=False) for a in ALTS]
        c = b.card_text(False, 0, "IND", .66, None, None, None, alts)
        self.assertIn("none look better than the main line", c)
        self.assertNotIn("DAL +9.5", c)

    def test_alt_rows_say_when_they_win(self):
        c = b.card_text(False, 0, "IND", .66, None, None, None, ALTS)
        self.assertIn("(wins if it wins or loses by 9 or fewer)", c)

    def test_summary_has_three_separate_rows(self):
        r = b.summary_rows(False, False, "IND", .66, None,
                           {"team": "DAL", "line": 6.5, "cover": .57},
                           {"team": "DAL", "line": 9.5})
        parts = r.split("  \n")
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[0].startswith("💰 **Moneyline:** IND to win"))
        self.assertEqual(parts[1], "📊 **Spread:** DAL +6.5 · 57% cover  ✅ value")
        self.assertEqual(parts[2], "🔀 **Alt spread:** DAL +9.5")

    def test_summary_dashes_and_warning(self):
        r = b.summary_rows(True, False, "LAL", .8, None, None, None)
        parts = r.split("  \n")
        self.assertEqual(len(parts), 4)
        self.assertIn("Book disagrees", parts[0])
        self.assertIn("no spread posted yet", parts[2])
        self.assertTrue(parts[3].endswith("—"))

    def test_ml_value_message(self):
        c = b.card_text(False, 0, "BOS", .6, (-150, "NYK", 130),
                        {"team": "NYK", "odds": 130}, None, [])
        self.assertIn("bet **NYK +130**", c)

    def test_skip_warns_but_still_shows_picks(self):
        c = b.card_text(True, 14, "LAL", .8, (-390, "LAC", 300), None, None, [])
        self.assertIn("Careful", c)
        self.assertIn("Moneyline pick: LAL to win", c)

    def test_quarter_line_warns(self):
        c = b.card_text(False, 0, "MIL", .52, None, None,
                        {"team": "MIL", "line": 1.25, "cover": .55}, [])
        self.assertIn("+1 to +1.5", c)
        self.assertIn("Use the one your book shows", c)


if __name__ == "__main__":
    unittest.main()


class TableTests(unittest.TestCase):
    def test_bets_table_rows(self):
        t = b.bets_table("IND", .66, (-280, "DAL", 230), None,
                         {"team": "DAL", "line": 6.5, "cover": .57}, ALTS)
        lines = t.split("\n")
        self.assertEqual(lines[0], "| Bet | Pick | Wins if | Price |")
        self.assertIn("| 💰 Moneyline | **IND** (66%, solid) | IND wins | -280 |", t)
        self.assertIn("| 📊 Spread | **DAL +6.5** | wins, or loses by 6 or less | -110 |", t)
        self.assertIn("| 🔀 Alt ⭐ | **DAL +9.5** | wins, or loses by 9 or less | -171 or better |", t)
        self.assertEqual(len(lines), 2 + 2 + 2)   # header+sep, ML, spread, 2 alts

    def test_bets_table_dashes_when_no_picks(self):
        t = b.bets_table("BOS", .56, None, None, None, [])
        self.assertIn("| 📊 Spread | no spread posted yet | | |", t)
        self.assertIn("| 🔀 Alt spread | — | | |", t)

    def test_matchup_table_is_symmetric(self):
        t = b.matchup_table("CHI", "MIL", .48, .52, -112, -105, 1.25,
                            113, 114, None, "−0.4 (Hukporti)", 237.5, 227)
        self.assertIn("| | **CHI** (away) | **MIL** (home) |", t)
        self.assertIn("| Win chance | 48% | 52% |", t)
        self.assertIn("| Spread | -1 to -1.5 | +1 to +1.5 |", t)
        self.assertIn("| Injuries | none | −0.4 (Hukporti) |", t)


class SpreadLeanTests(unittest.TestCase):
    LEAN = {"team": "MIL", "line": 1.5, "cover": .52}

    def test_summary_shows_lean_when_no_value_pick(self):
        r = b.summary_rows(False, False, "MIL", .54, None, None, None,
                           sp_lean=self.LEAN)
        self.assertIn("📊 **Spread:** MIL +1.5 · 52% cover (lean, no edge)", r)

    def test_value_pick_beats_lean(self):
        r = b.summary_rows(False, False, "MIL", .54, None,
                           {"team": "DAL", "line": 6.5, "cover": .57}, None,
                           sp_lean=self.LEAN)
        self.assertIn("DAL +6.5 · 57% cover  ✅ value", r)
        self.assertNotIn("lean, no edge", r)

    def test_bets_table_marks_lean_only(self):
        t = b.bets_table("MIL", .54, None, None, None, [], sp_lean=self.LEAN)
        self.assertIn("| 📊 Spread (lean only) | MIL +1.5 (52%) | wins, or loses by 1 or less | -110 |", t)


class UnderdogValueTests(unittest.TestCase):
    def test_title_does_not_call_favorite_price_good_when_value_is_underdog(self):
        t = b.title_text(False, "IND", .66, {"team": "DAL", "odds": 230},
                         None, None)
        self.assertNotIn("good price", t)
        self.assertIn("✅ value on DAL +230", t)

    def test_bets_table_has_separate_underdog_row(self):
        tab = b.bets_table("IND", .66, (-280, "DAL", 230),
                           {"team": "DAL", "odds": 230}, None, [])
        self.assertIn("| 💰 Moneyline value | **DAL** (underdog) | DAL wins | +230 ✅ |", tab)
        self.assertNotIn("✅ good", tab.split("\n")[2])   # favorite's row

    def test_favorite_value_still_good_price(self):
        t = b.title_text(False, "IND", .66, {"team": "IND", "odds": -280}, None, None)
        self.assertIn("✅ good price", t)
