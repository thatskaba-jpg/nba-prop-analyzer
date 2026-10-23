import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from team_names import TEAM_NAMES, full, fullify  # noqa: E402


class TeamNameTests(unittest.TestCase):
    def test_all_30_teams(self):
        self.assertEqual(len(TEAM_NAMES), 30)

    def test_fullify_titles_and_picks(self):
        self.assertEqual(
            fullify("🏀 LAC @ LAL · ✅ DAL +5.75"),
            "🏀 LA Clippers @ Los Angeles Lakers · ✅ Dallas Mavericks +5.75")

    def test_does_not_touch_other_words(self):
        s = "Model: ML -160, 36 min, ISO 1.03 pts, PIE"
        self.assertEqual(fullify(s), s)

    def test_idempotent_and_unknown(self):
        t = fullify("LAL -3.5")
        self.assertEqual(fullify(t), t)
        self.assertEqual(full("XYZ"), "XYZ")


if __name__ == "__main__":
    unittest.main()
