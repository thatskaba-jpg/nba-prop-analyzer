import io
import os
import sys
import unittest
import zipfile
from xml.dom import minidom

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import xlsx_min as x  # noqa: E402
import workbooks as w  # noqa: E402
import guard_rails as g  # noqa: E402


def parts(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    for n in z.namelist():
        minidom.parseString(z.read(n))       # raises if not well-formed
    return z


class XlsxTests(unittest.TestCase):
    def test_col_letters(self):
        self.assertEqual([x.col_letter(i) for i in (0, 25, 26, 27, 701, 702)],
                         ["A", "Z", "AA", "AB", "ZZ", "AAA"])

    def test_sheet_names_cleaned_and_unique(self):
        used = set()
        self.assertEqual(x.clean_sheet_name("10/23: a", used), "10-23- a")
        self.assertEqual(x.clean_sheet_name("10/23: a", used), "10-23- a (2)")
        self.assertEqual(len(x.clean_sheet_name("x" * 50, set())), 31)

    def test_workbook_is_valid_zip_with_tabs_filters_and_frozen_header(self):
        b = x.write_xlsx([
            {"name": "Summary", "rows": [{"Model": "A & <B>", "Right": 3, "Pct": 0.5}]},
            {"name": "2026-10-23", "rows": [{"Game": "Heat ✅", "Pick": None}]},
            {"name": "Empty", "rows": []}])
        z = parts(b)
        self.assertIn("xl/worksheets/sheet3.xml", z.namelist())
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('<autoFilter ref="A1:C2"/>', s1)
        self.assertIn('showGridLines="0"', s1)
        self.assertIn('state="frozen"', s1)
        self.assertIn("A &amp; &lt;B&gt;", s1)
        self.assertIn("<v>3</v>", s1)                       # numbers stay numeric
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('<sheet name="2026-10-23"', wb)
        self.assertIn("_xlnm._FilterDatabase", wb)

    def test_title_rows_move_the_filter_and_freeze_pane(self):
        z = parts(x.write_xlsx([{"name": "S", "title": "T", "subtitle": "sub",
                                 "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}]))
        s1 = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('<autoFilter ref="A3:B5"/>', s1)
        self.assertIn('ySplit="3"', s1)
        self.assertIn('<mergeCell ref="A1:B1"/>', s1)
        self.assertIn("$A$3:$B$5", z.read("xl/workbook.xml").decode())

    def test_status_cells_are_color_coded_and_percent_formatted(self):
        z = parts(x.write_xlsx([{"name": "S", "percent": ["Pct"], "rows": [
            {"Res": "✅ Covered", "Pct": 0.714}, {"Res": "❌ Missed", "Pct": 0.5},
            {"Res": "⏳ Pending", "Pct": 0.25}]}]))
        st = z.read("xl/styles.xml").decode()
        for color in ("FFDDF0DD", "FFF9D6D5", "FFFFF1C9"):
            self.assertIn(color, st)
        self.assertIn('formatCode="0.0%"', st)
        self.assertIn('numFmtId="164"', st)
        # every style index used in a sheet exists in cellXfs
        import re
        n_xf = int(re.search(r'<cellXfs count="(\d+)"', st).group(1))
        used = {int(m) for m in re.findall(r' s="(\d+)"', z.read("xl/worksheets/sheet1.xml").decode())}
        self.assertTrue(used and max(used) < n_xf)

    def test_control_characters_are_stripped(self):
        z = parts(x.write_xlsx([{"name": "S", "rows": [{"a": "bad\x01char"}]}]))
        self.assertIn("badchar", z.read("xl/worksheets/sheet1.xml").decode())


ML = [
    {"Date": "2026-10-20", "Game": "BOS @ DET", "Model": "Balanced", "Model pick": "BOS by 0.4",
     "Actual result": "DET won by 8 (121-113)", "Hit?": "❌ LOSS"},
    {"Date": "2026-10-21", "Game": "OKC @ SAS", "Model": "Balanced", "Model pick": "OKC by 5.4",
     "Actual result": "OKC won by 12 (116-104)", "Hit?": "✅ WIN"},
]
ATS = [{"game_id": "CLE @ PHI|2026-10-22", "home": "PHI", "away": "CLE", "spread_home": -3.5,
        "our_side": "home", "proj_margin": 9.2, "actual_margin": 8, "covered_side": "home", "result": "win"},
       {"game_id": "HOU @ SAS|2026-10-23", "home": "SAS", "away": "HOU", "spread_home": -5.5,
        "our_side": "away", "proj_margin": 3.9, "actual_margin": None, "covered_side": None, "result": "pending"}]


class WorkbookTests(unittest.TestCase):
    def test_ml_workbook_tabs(self):
        z = parts(w.ml_workbook(ML))
        wb = z.read("xl/workbook.xml").decode()
        for name in ("Summary", "All games", "2026-10-21", "2026-10-20"):
            self.assertIn(f'name="{name}"', wb)
        summ = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Balanced", summ)

    def test_ats_workbook_tabs_and_summary(self):
        z = parts(w.ats_workbook(ATS))
        wb = z.read("xl/workbook.xml").decode()
        for name in ("Summary", "All", "2026-10-22", "2026-10-23"):
            self.assertIn(f'name="{name}"', wb)

    def test_empty_inputs_still_produce_a_valid_file(self):
        parts(w.ml_workbook([]))
        parts(w.ats_workbook([]))
        parts(w.gamelines_workbook([], [], [], []))


class GuardTests(unittest.TestCase):
    NOW = 1_000_000.0

    def test_all_fresh_no_warnings(self):
        self.assertEqual(g.warnings(self.NOW, self.NOW - 600, self.NOW - 30,
                                    self.NOW - 100, 2, 0, 0), [])

    def test_stale_things_are_flagged(self):
        w = g.warnings(self.NOW, self.NOW - 3 * 3600, self.NOW - 900, None, 3, 3, 0)
        txt = " ".join(w)
        self.assertIn("Lines are 15 min old", txt)
        self.assertIn("Injuries are 3 h old", txt)
        self.assertIn("haven't checked lineups", txt)

    def test_missing_inputs(self):
        w = g.warnings(self.NOW, None, None, None, 0, 0, 0)
        self.assertEqual(len(w), 2)

    def test_lineups_checked_but_not_posted(self):
        w = g.warnings(self.NOW, self.NOW, self.NOW, self.NOW - 60, 2, 2, 0)
        self.assertIn("no posted lineup yet", w[0])

    def test_proof_note_and_stake(self):
        self.assertIn("only 27 forward-test game(s)", g.proof_note(27))
        self.assertEqual(g.proof_note(300), "")
        self.assertEqual(g.max_stake(500), 5.0)
        self.assertEqual(g.max_stake(0), 0.0)
        self.assertEqual(g.max_stake("x"), 0.0)


if __name__ == "__main__":
    unittest.main()


class ScorecardWorkbookTests(unittest.TestCase):
    def test_scorecard_workbook_tabs_and_validity(self):
        z = parts(w.scorecard_workbook(ML, ATS))
        wb = z.read("xl/workbook.xml").decode()
        for name in ("Summary", "Agreement", "All games"):
            self.assertIn(f'name="{name}"', wb)
        self.assertIn("Spread: our side covered", z.read("xl/worksheets/sheet1.xml").decode())

    def test_empty(self):
        parts(w.scorecard_workbook([], []))
