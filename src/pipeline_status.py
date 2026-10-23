"""
pipeline_status.py — remember whether each nightly step actually worked.

The daily update used to swallow every failure and keep going, so a dead API key
or a broken download left the app quietly running on stale data. Each step now
records its outcome here (data/pipeline_status.json), and the dashboard shows a
warning when the data is old or a critical step failed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "data" / "pipeline_status.json"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def read():
    """The saved status dict ({'steps': {...}}), or {} if nothing was recorded."""
    try:
        return json.loads(_PATH.read_text())
    except Exception:
        return {}


def record(step, ok, detail=""):
    """Record one step's outcome. Never raises: bookkeeping must not break a run."""
    try:
        data = read() or {}
        data.setdefault("steps", {})[step] = {
            "ok": bool(ok), "at": _now(), "detail": str(detail)[:300]}
        data["updated_at"] = _now()
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def run_step(name, fn):
    """Run fn(), record whether it worked, and NEVER raise: one broken step must
    not stop the rest of the update. Returns (ok, result)."""
    try:
        result = fn()
        record(name, True, "" if result is None else str(result))
        return True, result
    except Exception as e:                      # noqa: BLE001 - by design
        print(f"  {name} did not complete: {type(e).__name__}: {e}")
        record(name, False, f"{type(e).__name__}: {e}")
        return False, None


def failed_steps(data=None):
    """Names of steps whose LAST run failed."""
    data = read() if data is None else data
    return sorted(k for k, v in (data.get("steps") or {}).items()
                  if not v.get("ok"))


def freshness(last_game_date, today, max_gap_days=3):
    """(is_stale, message) for the box-score data.

    last_game_date  newest game_date in player_game_logs ('YYYY-MM-DD') or None
    today           the NBA 'today' ('YYYY-MM-DD')
    The NBA has multi-day gaps (All-Star break, offseason), so a gap only counts
    as stale inside the season window; callers pass max_gap_days to tune it."""
    if not last_game_date:
        return True, "No games in the database yet."
    try:
        gap = (datetime.fromisoformat(today)
               - datetime.fromisoformat(last_game_date)).days
    except ValueError:
        return False, ""
    if gap > max_gap_days:
        return True, (f"Latest games in the database are from {last_game_date} "
                      f"({gap} days ago). The nightly update may not be running.")
    return False, ""
