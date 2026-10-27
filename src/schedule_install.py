"""
schedule_install.py — run the bot's jobs automatically (macOS launchd).

    python3 src/schedule_install.py            # install / update the schedule
    python3 src/schedule_install.py --status   # what's installed + last runs
    python3 src/schedule_install.py --uninstall

Jobs (your Mac's local time):
  daily     09:00          box scores, injuries, rosters, advanced stats, prop
                           lines, gated model retrain, grading
  intraday  12:00 15:00 18:00   save the book's lines before tip, today's
                           projections, paper bets
  weekly    Sunday 03:30   refit calibration + market blend (old files backed up)

launchd runs a missed job when the Mac wakes up, but the Mac has to be on at
some point. Logs go to data/*.log. If this Mac is managed by an organization,
launchd agents may be blocked; the script says so instead of failing quietly.
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = Path.home() / "Library" / "LaunchAgents"
PREFIX = "com.sportsbot"


def jobs(python=None):
    py = str(python or ROOT / ".venv" / "bin" / "python")
    return {
        "daily": {"args": [py, "src/collect/daily_update.py"],
                  "times": [{"Hour": 9, "Minute": 0}], "log": "daily_update.log"},
        "intraday": {"args": [py, "src/collect/intraday.py"],
                     "times": [{"Hour": h, "Minute": 0} for h in (12, 15, 18)],
                     "log": "intraday.log"},
        "weekly": {"args": [py, "src/models/retrain.py", "weekly"],
                   "times": [{"Weekday": 0, "Hour": 3, "Minute": 30}],
                   "log": "weekly_refit.log"},
    }


def build_plist(name, job, root=ROOT):
    """The launchd definition for one job (a plain dict, easy to test)."""
    log = str(Path(root) / "data" / job["log"])
    return {
        "Label": f"{PREFIX}.{name}",
        "ProgramArguments": job["args"],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": job["times"],
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "RunAtLoad": False,
    }


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def install():
    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        print(f"Can't find {py}. Create the virtual environment first (see README).")
        return 1
    AGENTS.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    bad = 0
    for name, job in jobs().items():
        path = AGENTS / f"{PREFIX}.{name}.plist"
        path.write_bytes(plistlib.dumps(build_plist(name, job)))
        _run(["launchctl", "bootout", f"gui/{uid}", str(path)])      # replace old
        r = _run(["launchctl", "bootstrap", f"gui/{uid}", str(path)])
        if r.returncode != 0:
            bad += 1
            print(f"  ! {name}: launchctl refused ({r.stderr.strip()[:120]}). "
                  "A managed Mac may block background jobs.")
        else:
            t = ", ".join(f"{x.get('Hour', 0):02d}:{x.get('Minute', 0):02d}" for x in job["times"])
            print(f"  ✓ {name}: {t}")
    print("Done." if not bad else "Some jobs could not be installed.")
    return 1 if bad else 0


def uninstall():
    uid = os.getuid()
    for name in jobs():
        path = AGENTS / f"{PREFIX}.{name}.plist"
        _run(["launchctl", "bootout", f"gui/{uid}", str(path)])
        path.unlink(missing_ok=True)
        print(f"  removed {name}")
    return 0


def status():
    r = _run(["launchctl", "list"])
    rows = [l for l in r.stdout.splitlines() if PREFIX in l]
    print("\n".join(rows) if rows else "No sportsbot jobs are loaded.")
    for name, job in jobs().items():
        lg = ROOT / "data" / job["log"]
        print(f"  {name}: log " + (f"last written {lg.stat().st_mtime:.0f}" if lg.exists() else "not created yet"))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(uninstall() if "--uninstall" in a else status() if "--status" in a else install())
