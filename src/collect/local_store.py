"""
local_store.py — disk cache for STABLE data so we stop re-pulling it.

Some balldontlie data barely changes within a day: season averages, active
rosters, team strength. Re-fetching it on every page load wastes API calls
(and triggers 429 rate limits). This stores that data as JSON on disk and
serves it from there, only hitting the API when the stored copy is missing or
older than `max_age_hours`.

Live data (injuries, odds, scores, today's games) is NOT stored here — those
keep their short in-memory caches and refresh often. This is only for the
slow-moving stuff.

Usage:
    get_or_refresh("season_avgs_2026", max_age_hours=12, build=lambda: {...})
"""

import json
import time
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "data" / "store"


def _path(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return _DIR / f"{safe}.json"


def load(key: str, max_age_hours: float = 24.0):
    """Return stored value if present AND fresh enough, else None."""
    p = _path(key)
    try:
        if not p.exists():
            return None
        raw = json.loads(p.read_text())
        age_h = (time.time() - raw.get("_ts", 0)) / 3600.0
        if age_h > max_age_hours:
            return None
        return raw.get("value")
    except Exception:
        return None


def save(key: str, value) -> bool:
    """Persist a value with a timestamp. Best-effort."""
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        _path(key).write_text(json.dumps({"_ts": time.time(), "value": value}))
        return True
    except Exception:
        return False


def get_or_refresh(key: str, build, max_age_hours: float = 24.0):
    """Read from disk if fresh; otherwise call build(), store, and return it.
    `build` is a zero-arg callable that fetches the data. If build() fails and
    a STALE copy exists on disk, serve the stale copy rather than nothing."""
    cached = load(key, max_age_hours=max_age_hours)
    if cached is not None:
        return cached
    try:
        value = build()
    except Exception:
        value = None
    if value is not None and value != [] and value != {}:
        save(key, value)
        return value
    # Build failed — fall back to any stale copy we have.
    stale = load(key, max_age_hours=10**9)
    return stale if stale is not None else value
