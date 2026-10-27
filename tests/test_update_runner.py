import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from update_runner import UpdateRunner  # noqa: E402


class UpdateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = Path(self.tmp) / "u.log"

    def _wait(self, r, secs=10):
        t = time.time()
        while r.running() and time.time() - t < secs:
            time.sleep(0.05)

    def test_runs_in_separate_process_and_logs(self):
        r = UpdateRunner([sys.executable, "-c",
                          "print('[1/5] Fetching'); print('[2/5] Backfilling')"],
                         cwd=self.tmp, log_path=self.log)
        self.assertTrue(r.start())
        self._wait(r)
        self.assertFalse(r.running())
        self.assertEqual(r.returncode(), 0)
        self.assertEqual(r.progress(), "[2/5] Backfilling")
        self.assertIn("Fetching", r.tail())

    def test_cannot_start_twice_while_running(self):
        r = UpdateRunner([sys.executable, "-c", "import time; time.sleep(2)"],
                         cwd=self.tmp, log_path=self.log)
        self.assertTrue(r.start())
        self.assertFalse(r.start())
        r.proc.kill()
        self._wait(r)

    def test_just_finished_fires_once(self):
        r = UpdateRunner([sys.executable, "-c", "pass"], cwd=self.tmp,
                         log_path=self.log)
        r.start()
        self._wait(r)
        self.assertTrue(r.just_finished())
        self.assertFalse(r.just_finished())

    def test_fail_fast_flag_not_inherited(self):
        os.environ["BDL_FAIL_FAST"] = "1"
        try:
            r = UpdateRunner([sys.executable, "-c",
                              "import os; print('FF=' + str(os.getenv('BDL_FAIL_FAST')))"],
                             cwd=self.tmp, log_path=self.log)
            r.start()
            self._wait(r)
            self.assertIn("FF=None", r.tail())
        finally:
            os.environ.pop("BDL_FAIL_FAST", None)


if __name__ == "__main__":
    unittest.main()
