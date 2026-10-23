import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from odds.spread_hint import spread_hint  # noqa: E402


class SpreadHintTests(unittest.TestCase):
    def test_underdog_quarter_line(self):
        t = spread_hint("DAL", 5.75)
        self.assertIn("5 or fewer", t)
        self.assertIn("blend", t)
        self.assertIn("5.5 or 6", t)

    def test_underdog_half_line(self):
        t = spread_hint("DAL", 5.5)
        self.assertIn("5 or fewer", t)
        self.assertNotIn("blend", t)

    def test_underdog_whole_number_has_push(self):
        t = spread_hint("DAL", 6)
        self.assertIn("5 or fewer", t)
        self.assertIn("push", t)

    def test_favorite_half_line(self):
        t = spread_hint("LAL", -9.5)
        self.assertIn("10 or more", t)

    def test_favorite_whole_number(self):
        t = spread_hint("LAL", -7)
        self.assertIn("8+", t)
        self.assertIn("push", t)

    def test_favorite_quarter_line(self):
        t = spread_hint("LAL", -3.25)
        self.assertIn("4 or more", t)
        self.assertIn("3 or 3.5", t)


if __name__ == "__main__":
    unittest.main()
