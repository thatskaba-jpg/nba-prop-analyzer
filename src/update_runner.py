"""
update_runner.py — run the daily data update in a SEPARATE process, so the
dashboard button never freezes the page and the update can wait patiently on
API rate limits (the dashboard itself fails fast on 429s).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpdateRunner:
    def __init__(self, cmd=None, cwd=None, log_path=None):
        self.cmd = cmd or [sys.executable,
                           str(ROOT / "src" / "collect" / "daily_update.py")]
        self.cwd = str(cwd or ROOT)
        self.log_path = Path(log_path or ROOT / "data" / "daily_update_ui.log")
        self.proc = None
        self.started = None
        self.finished = None
        self._seen_finish = False

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        """Start an update unless one is already running. True if started."""
        if self.running():
            return False
        # The dashboard sets BDL_FAIL_FAST so pages never hang; the update must
        # NOT inherit it (it should wait out rate limits instead of failing).
        env = {k: v for k, v in os.environ.items() if k != "BDL_FAIL_FAST"}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w") as log:     # child keeps its own copy
            self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=env,
                                         stdout=log, stderr=subprocess.STDOUT)
        self.started, self.finished, self._seen_finish = time.time(), None, False
        return True

    def just_finished(self) -> bool:
        """True exactly once after a run ends (so callers can clear caches)."""
        if self.proc is not None and not self.running() and not self._seen_finish:
            self._seen_finish = True
            self.finished = time.time()
            return True
        return False

    def returncode(self):
        return None if self.proc is None else self.proc.poll()

    def tail(self, n=12) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def progress(self) -> str:
        """Last '[k/5] ...' step line from the log, e.g. '[2/5] Backfilling…'."""
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        steps = [l.strip() for l in lines if l.strip().startswith("[") and "/5]" in l]
        return steps[-1] if steps else ""
