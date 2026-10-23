"""
balldontlie_client.py — a thin, safe wrapper around the balldontlie NBA API.

Why this file exists:
- Keeps the API key OUT of every other file (read once from .env here).
- Enforces the trial rate limit (5 requests/minute) so we never get blocked.
- Handles pagination (the API returns data in pages via a "cursor").
- Retries politely on 429 (rate limit) and transient errors.

Docs verified: base URL https://api.balldontlie.io/v1/ , auth header is
"Authorization: <API_KEY>" (NO "Bearer" prefix).

The key lives in .env as:
    BALLDONTLIE_API_KEY=your-key-here
"""

import os
import threading
import time
from pathlib import Path

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

BASE_URL = "https://api.balldontlie.io/v1"
API_KEY = os.getenv("BALLDONTLIE_API_KEY", "").strip()

# Rate-limit pacing. balldontlie's tiers: trial/free = 5 req/min, ALL-STAR =
# 60/min, GOAT = 600/min. We space calls so we stay safely under the tier's
# limit. Set your tier in .env so the pull runs at the right speed:
#     BALLDONTLIE_TIER=goat     (or all-star, or trial)
# or override the delay directly:
#     BALLDONTLIE_MIN_DELAY=0.2
_TIER_DELAYS = {"trial": 13.0, "free": 13.0, "all-star": 1.1, "allstar": 1.1,
                "goat": 0.12}
_tier = os.getenv("BALLDONTLIE_TIER", "trial").strip().lower()
_explicit = os.getenv("BALLDONTLIE_MIN_DELAY", "").strip()
if _explicit:
    MIN_SECONDS_BETWEEN_CALLS = float(_explicit)
else:
    MIN_SECONDS_BETWEEN_CALLS = _TIER_DELAYS.get(_tier, 13.0)
RATE_NOTE = (f"~{60.0 / MIN_SECONDS_BETWEEN_CALLS:.0f} requests/min "
             f"(tier: {_tier})")
_last_call_time = [0.0]  # list so the inner function can mutate it
_cooldown_until = [0.0]  # fail-fast mode: no calls until this time after a 429


class BallDontLieError(Exception):
    pass


_throttle_lock = threading.Lock()


def _throttle():
    """Sleep just enough to respect the rate limit. Thread-safe: parallel
    callers are spaced MIN_SECONDS_BETWEEN_CALLS apart (the request itself runs
    outside the lock, so several can be in flight at once)."""
    with _throttle_lock:
        elapsed = time.time() - _last_call_time[0]
        wait = MIN_SECONDS_BETWEEN_CALLS - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_call_time[0] = time.time()


def _get(path, params=None):
    """Make one authenticated GET request and return the parsed JSON.

    Default base is the v1 API. Pass an absolute path starting with '/v2' or
    '/nba' to hit a different base (e.g. the v2 odds endpoints)."""
    if not API_KEY:
        raise BallDontLieError(
            "No API key found. Add BALLDONTLIE_API_KEY=your-key to the .env "
            "file in the project root, then try again."
        )
    p = path.lstrip("/")
    if p.startswith(("v2/", "nba/")):
        url = f"https://api.balldontlie.io/{p}"
    else:
        url = f"{BASE_URL}/{p}"
    headers = {"Authorization": API_KEY}

    # Interactive callers (the dashboard) set BDL_FAIL_FAST=1: never sleep through
    # 429/5xx retries (up to ~2.5 min per call) -- raise at once so the caller can
    # use a backup source. After a 429 we also refuse calls for 60s so one rate
    # limit doesn't turn into dozens of slow failures.
    fast = os.getenv("BDL_FAIL_FAST") == "1"
    if fast and time.time() < _cooldown_until[0]:
        raise BallDontLieError("429 rate limited (cooling down)")

    for attempt in range(1, 5):
        _throttle()
        resp = requests.get(url, headers=headers, params=params,
                            timeout=10 if fast else 30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            raise BallDontLieError("401 Unauthorized — the API key is wrong or the trial expired.")
        if resp.status_code == 429 and fast:
            _cooldown_until[0] = time.time() + 60
            raise BallDontLieError("429 rate limited")
        if resp.status_code in (500, 502, 503, 504) and fast:
            raise BallDontLieError(f"{resp.status_code} server error")
        if resp.status_code == 429:
            wait = attempt * 15
            print(f"  Rate limited (429). Waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504):
            wait = attempt * 5
            print(f"  Server error {resp.status_code}. Retrying in {wait}s...")
            time.sleep(wait)
            continue
        raise BallDontLieError(f"{resp.status_code} error: {resp.text[:300]}")
    raise BallDontLieError("Gave up after repeated rate-limit/server errors.")


def get_all(path, params=None):
    """Fetch EVERY page of a list endpoint, following the cursor.

    balldontlie paginates with meta.next_cursor. We keep asking for the next
    page until there isn't one, collecting all rows into a single list.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)  # max page size
    rows = []
    while True:
        payload = _get(path, params)
        rows.extend(payload.get("data", []))
        meta = payload.get("meta", {}) or {}
        next_cursor = meta.get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor
        print(f"  ...fetched {len(rows)} rows so far, getting next page")
    return rows


def iter_pages(path, params=None, start_cursor=None):
    """Yield (rows, next_cursor) one page at a time, following the cursor.

    Unlike get_all (which collects everything in memory and can't resume), this
    hands back each page AND the cursor for the NEXT page. The caller can save
    that cursor after storing a page, so a crash/stop can resume from it by
    passing it back as start_cursor.
    """
    params = dict(params or {})
    params.setdefault("per_page", 100)
    if start_cursor:
        params["cursor"] = start_cursor
    while True:
        payload = _get(path, params)
        rows = payload.get("data", [])
        meta = payload.get("meta", {}) or {}
        next_cursor = meta.get("next_cursor")
        yield rows, next_cursor
        if not next_cursor:
            break
        params["cursor"] = next_cursor


def check_key():
    """Quick, cheap call to confirm the key works. Uses the free /teams endpoint."""
    data = _get("teams")
    n = len(data.get("data", []))
    print(f"API key works. /teams returned {n} teams.")
    return n > 0


if __name__ == "__main__":
    check_key()
