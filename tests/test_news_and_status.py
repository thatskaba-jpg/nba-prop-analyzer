"""Timestamped news snapshots and the pipeline health record."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
import pipeline_status as ps
from collect import news_capture as nc


class InjurySnapshotTests(helpers.TempDBTestCase):
    def setUp(self):
        super().setUp()
        from database import get_connection
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS injuries (player_id INTEGER PRIMARY KEY, "
                     "player_name TEXT, status TEXT, description TEXT, fetched_at TEXT)")
        conn.commit()
        conn.close()

    def set_injury(self, pid, status, desc="knee"):
        from database import get_connection
        conn = get_connection()
        conn.execute("INSERT OR REPLACE INTO injuries VALUES (?,?,?,?, 'now')",
                     (pid, f"P{pid}", status, desc))
        conn.commit()
        conn.close()

    def test_only_changes_are_saved(self):
        self.set_injury(1, "Questionable")
        self.assertEqual(nc.snapshot_injuries(now="2026-10-22T10:00:00"), 1)
        self.assertEqual(nc.snapshot_injuries(now="2026-10-22T11:00:00"), 0)   # no news
        self.set_injury(1, "Out")                                              # news
        self.set_injury(2, "Doubtful")
        self.assertEqual(nc.snapshot_injuries(now="2026-10-22T12:00:00"), 2)
        rows = self.fetch_all("SELECT status FROM injury_snapshots WHERE player_id=1 "
                              "ORDER BY captured_at")
        self.assertEqual([r["status"] for r in rows], ["Questionable", "Out"])

    def test_changed_injuries_is_pure(self):
        cur = [{"player_id": 1, "player_name": "A", "status": "Out", "description": "x"}]
        self.assertEqual(nc.changed_injuries(cur, {1: ("Out", "x")}), [])
        self.assertEqual(len(nc.changed_injuries(cur, {1: ("Questionable", "x")})), 1)
        self.assertEqual(len(nc.changed_injuries(cur, {})), 1)


class LineupSnapshotTests(helpers.TempDBTestCase):
    def lineup(self, starters):
        return [{"team": "AAA", "player_id": i, "player_name": f"P{i}",
                 "starter": i in starters} for i in range(1, 6)]

    def test_saved_when_posted_or_changed_only(self):
        feeds = {1: []}
        fetch = lambda gid: feeds[gid]
        self.assertEqual(nc.snapshot_lineups([1], fetch=fetch, now="t1"), 0)      # not posted
        feeds[1] = self.lineup({1, 2, 3})
        self.assertEqual(nc.snapshot_lineups([1], fetch=fetch, now="t2"), 1)      # posted
        self.assertEqual(nc.snapshot_lineups([1], fetch=fetch, now="t3"), 0)      # same
        feeds[1] = self.lineup({1, 2, 4})                                         # swapped
        self.assertEqual(nc.snapshot_lineups([1], fetch=fetch, now="t4"), 1)
        n = self.fetch_all("SELECT COUNT(*) c FROM lineup_snapshots")[0]["c"]
        self.assertEqual(n, 10)

    def test_signature_ignores_order(self):
        a = self.lineup({1, 2})
        self.assertEqual(nc.lineup_signature(a), nc.lineup_signature(list(reversed(a))))


class PipelineStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(ps, "_PATH", Path(tmp.name) / "status.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_run_step_records_success_and_swallows_failure(self):
        ok, result = ps.run_step("good", lambda: "done")
        self.assertEqual((ok, result), (True, "done"))

        def boom():
            raise RuntimeError("api key expired")
        ok, result = ps.run_step("bad", boom)
        self.assertFalse(ok)
        self.assertEqual(ps.failed_steps(), ["bad"])
        self.assertIn("api key expired", ps.read()["steps"]["bad"]["detail"])

    def test_a_step_that_later_succeeds_is_no_longer_failed(self):
        ps.run_step("flaky", lambda: 1 / 0)
        self.assertEqual(ps.failed_steps(), ["flaky"])
        ps.run_step("flaky", lambda: "fine now")
        self.assertEqual(ps.failed_steps(), [])

    def test_freshness(self):
        self.assertEqual(ps.freshness("2026-10-21", "2026-10-22"), (False, ""))
        stale, msg = ps.freshness("2026-10-10", "2026-10-22")
        self.assertTrue(stale)
        self.assertIn("2026-10-10", msg)
        self.assertTrue(ps.freshness(None, "2026-10-22")[0])


if __name__ == "__main__":
    unittest.main()
