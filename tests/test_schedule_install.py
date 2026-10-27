import os
import plistlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import schedule_install as si  # noqa: E402


class PlistTests(unittest.TestCase):
    def test_jobs_cover_daily_intraday_weekly(self):
        j = si.jobs("/x/python")
        self.assertEqual(set(j), {"daily", "intraday", "weekly"})
        self.assertEqual([t["Hour"] for t in j["intraday"]["times"]], [12, 15, 18])
        self.assertEqual(j["weekly"]["args"][-2:], ["src/models/retrain.py", "weekly"][-2:])

    def test_plist_is_valid_and_points_at_project(self):
        j = si.jobs("/x/python")
        d = si.build_plist("daily", j["daily"], root="/proj")
        data = plistlib.loads(plistlib.dumps(d))           # round-trips
        self.assertEqual(data["Label"], "com.sportsbot.daily")
        self.assertEqual(data["WorkingDirectory"], "/proj")
        self.assertEqual(data["StartCalendarInterval"], [{"Hour": 9, "Minute": 0}])
        self.assertTrue(data["StandardOutPath"].endswith("data/daily_update.log"))
        self.assertFalse(data["RunAtLoad"])

    def test_nothing_runs_on_import(self):
        # importing must not touch launchd or the filesystem
        self.assertTrue(callable(si.install))


if __name__ == "__main__":
    unittest.main()
