import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collect import game_roster as gr  # noqa: E402


ROSTER = [
    {"name": "Guard One", "position": "PG"},
    {"name": "Guard Two", "position": "SG"},
    {"name": "Wing One", "position": "SF"},
    {"name": "Big One", "position": "C"},
    {"name": "Big Two", "position": "PF"},
    {"name": "Sixth Man", "position": "G"},
    {"name": "Hurt Star", "position": "F"},
    {"name": "Deep Bench", "position": "C"},
]
MINUTES = {"Guard One": 34, "Guard Two": 30, "Wing One": 32, "Big One": 29,
           "Big Two": 28, "Sixth Man": 22, "Hurt Star": 36, "Deep Bench": 4}
INJ = {"Hurt Star": "Out"}


class BuildRosterTests(unittest.TestCase):
    def test_projected_skips_out_player_and_lists_him_last(self):
        r = gr.build_roster([], ROSTER, MINUTES, INJ)
        self.assertFalse(r["confirmed"])
        names = [e["name"] for e in r["starters"]]
        self.assertEqual(set(names), {"Guard One", "Guard Two", "Wing One",
                                      "Big One", "Big Two"})
        self.assertNotIn("Hurt Star", names)
        self.assertEqual(r["bench"][-1]["name"], "Hurt Star")
        self.assertEqual(r["bench"][0]["name"], "Sixth Man")

    def test_starters_ordered_guards_to_bigs(self):
        r = gr.build_roster([], ROSTER, MINUTES, INJ)
        ranks = [gr._pos_rank(e["position"]) for e in r["starters"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_confirmed_uses_feed_starters_and_positions(self):
        feed = [
            {"name": "Sixth Man", "position": "G", "starter": True},
            {"name": "Big One", "position": "C", "starter": True},
            {"name": "Guard One", "position": "G", "starter": False},
            {"name": "Deep Bench", "position": "C", "starter": False},
            {"name": "Hurt Star", "position": "F", "starter": False},
        ]
        r = gr.build_roster(feed, ROSTER, MINUTES, INJ)
        self.assertTrue(r["confirmed"])
        self.assertEqual([e["name"] for e in r["starters"]],
                         ["Sixth Man", "Big One"])
        self.assertEqual([e["name"] for e in r["bench"]],
                         ["Guard One", "Deep Bench", "Hurt Star"])

    def test_name_normalization_matches_suffixes(self):
        self.assertEqual(gr.norm("Tarris Reed Jr."), gr.norm("Tarris Reed"))
        self.assertEqual(gr.norm("De'Aaron Fox"), gr.norm("DeAaron Fox"))

    def test_position_falls_back_to_db_when_feed_blank(self):
        feed = [{"name": "Big One", "position": "", "starter": True}]
        r = gr.build_roster(feed, ROSTER, MINUTES, {})
        self.assertEqual(r["starters"][0]["position"], "C")

    def test_lineup_rows_only_for_requested_team(self):
        raw = [
            {"team": {"abbreviation": "SAS"}, "starter": True, "position": "C",
             "player": {"first_name": "Victor", "last_name": "Wembanyama"}},
            {"team": {"abbreviation": "HOU"}, "starter": True, "position": "G",
             "player": {"first_name": "Other", "last_name": "Guy"}},
        ]
        rows = gr.lineup_rows_for_team(raw, "SAS")
        self.assertEqual([r["name"] for r in rows], ["Victor Wembanyama"])
        self.assertTrue(rows[0]["starter"])

    def test_markdown_labels_mode(self):
        txt = gr.roster_markdown("SAS", gr.build_roster([], ROSTER, MINUTES, INJ))
        self.assertIn("projected", txt)
        self.assertIn("Starters", txt)
        self.assertIn("Bench", txt)
        self.assertIn("Out", txt)


class PrevStartersTests(unittest.TestCase):
    def test_last_game_starters_used_when_healthy(self):
        # Sixth Man (22 min) started last game; the model by minutes would not.
        prev = ["Sixth Man", "Guard One", "Guard Two", "Wing One", "Big One"]
        r = gr.build_roster([], ROSTER, MINUTES, INJ, prev_starters=prev)
        self.assertEqual(r["basis"], "last game")
        self.assertEqual({e["name"] for e in r["starters"]}, set(prev))

    def test_out_starter_replaced_by_next_in_minutes(self):
        prev = ["Hurt Star", "Guard One", "Guard Two", "Wing One", "Big One"]
        r = gr.build_roster([], ROSTER, MINUTES, INJ, prev_starters=prev)
        names = {e["name"] for e in r["starters"]}
        self.assertNotIn("Hurt Star", names)
        self.assertEqual(len(names), 5)
        self.assertIn("Big Two", names)           # next-highest healthy minutes

    def test_no_prev_falls_back_to_minutes(self):
        r = gr.build_roster([], ROSTER, MINUTES, INJ)
        self.assertEqual(r["basis"], "minutes")

    def test_confirmed_wins_over_prev(self):
        feed = [{"name": "Big One", "position": "C", "starter": True}]
        r = gr.build_roster(feed, ROSTER, MINUTES, {}, prev_starters=["Guard One"])
        self.assertTrue(r["confirmed"])
        self.assertEqual([e["name"] for e in r["starters"]], ["Big One"])

    def test_table_labels_basis(self):
        r1 = gr.build_roster([], ROSTER, MINUTES, INJ, prev_starters=["Guard One"])
        r2 = gr.build_roster([], ROSTER, MINUTES, INJ)
        t = gr.lineup_table("A", "B", r1, r2)
        self.assertIn("projected (same as last game)", t)
        self.assertIn("projected (by minutes)", t)


class CompactTests(unittest.TestCase):
    def test_compact_shows_starters_and_notable_outs_only(self):
        r = gr.build_roster([], ROSTER, MINUTES, INJ)
        txt = gr.compact_markdown("SAS", r)
        self.assertIn("projected", txt)
        self.assertIn("One PG", txt)           # surname + position (Guard One)
        self.assertIn("Out: Star", txt)        # Hurt Star, 36 min, Out
        self.assertNotIn("Deep Bench", txt)    # bench players aren't listed
        self.assertEqual(txt.count("\n"), 1)   # two lines total

    def test_confirmed_has_no_projected_tag(self):
        feed = [{"name": "Big One", "position": "C", "starter": True}]
        txt = gr.compact_markdown("SAS", gr.build_roster(feed, ROSTER, MINUTES, {}))
        self.assertNotIn("projected", txt)


class LoadContextTests(unittest.TestCase):
    def _conn(self, with_pos=True):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE player_teams (player_name TEXT PRIMARY KEY, "
                  "team TEXT, fetched_at TEXT" +
                  (", position TEXT" if with_pos else "") + ")")
        c.execute("CREATE TABLE player_game_logs (player_name TEXT, "
                  "game_date TEXT, minutes REAL, rebounds INT, assists INT, "
                  "threes_made INT)")
        c.execute("CREATE TABLE injuries (player_id INT, player_name TEXT, "
                  "status TEXT)")
        return c

    def test_reads_minutes_injuries_and_estimates_missing_position(self):
        c = self._conn(with_pos=False)
        c.execute("INSERT INTO player_teams VALUES ('A Guard','SAS','x')")
        c.execute("INSERT INTO player_teams VALUES ('B Big','SAS','x')")
        c.execute("INSERT INTO player_teams VALUES ('C Other','HOU','x')")
        for d in ("2026-10-20", "2026-10-21"):
            c.execute("INSERT INTO player_game_logs VALUES "
                      "('A Guard',?,30,3,8,3)", (d,))
            c.execute("INSERT INTO player_game_logs VALUES "
                      "('B Big',?,28,11,1,0)", (d,))
        c.execute("INSERT INTO injuries VALUES (1,'B Big','Out')")
        roster, minutes, inj = gr.load_team_context(c, "SAS")
        self.assertEqual({r["name"] for r in roster}, {"A Guard", "B Big"})
        self.assertAlmostEqual(minutes["A Guard"], 30)
        self.assertEqual(inj, {"B Big": "Out"})
        pos = {r["name"]: r["position"] for r in roster}
        self.assertEqual(pos["A Guard"], "G~")
        self.assertEqual(pos["B Big"], "C~")

    def test_official_position_wins_over_estimate(self):
        c = self._conn(with_pos=True)
        c.execute("INSERT INTO player_teams VALUES ('A Guard','SAS','x','SF')")
        c.execute("INSERT INTO player_game_logs VALUES "
                  "('A Guard','2026-10-20',30,3,8,3)")
        roster, _, _ = gr.load_team_context(c, "SAS")
        self.assertEqual(roster[0]["position"], "SF")


if __name__ == "__main__":
    unittest.main()
