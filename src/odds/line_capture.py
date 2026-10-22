"""
line_capture.py — store REAL sportsbook prop lines so we can validate the model
against the actual market, not a season-average proxy.

WHY THIS EXISTS. The backtest (backtest/backtest.py) is honest about a big
limitation: we have no historical prop lines, so it tests against a season-avg
"proxy" line. That proxy flatters the model (it beats a static line far more
easily than it would beat a live, moving sportsbook line). balldontlie's player
props are LIVE ONLY — "we do not store historical data" — so every day we don't
capture today's lines is a day of real-line data lost forever.

This module fixes that GOING FORWARD:
  1. capture_today()   -> snapshot every live over/under prop on today's slate,
                          ONE ROW PER (game, player, prop, book), into the
                          historical_props table. Run it daily (wired into
                          daily_update). Idempotent per snapshot timestamp.
  2. backfill_opening()-> pull the opening-props endpoint for a date range and
                          store those real opening lines (a small head start —
                          this season, and last season where coverage reaches).
  3. grade_settled()   -> once a game is final, join the stored line to the
                          ACTUAL stat in player_game_logs (on player_id+game_id)
                          and mark each row won/lost/push. This is what turns
                          captured lines into a real-line results set.

HONESTY NOTES.
  - Opening lines are REAL market lines but move a lot before tip; closing lines
    would be stronger. We store a `snapshot` label ('opening' vs 'live') so a
    later analysis can separate them.
  - We store raw per-book rows (not a median) so grading is faithful and we can
    later study line movement and book disagreement.
  - Grading reads the actual result ONLY after the game is final — no leakage
    into any projection (projections never read this table).
"""

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

from database import get_connection  # noqa: E402
from features.features import COMBO_PARTS  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_KEY = os.getenv("BALLDONTLIE_API_KEY", "").strip()
_BASE = "https://api.balldontlie.io"

# balldontlie prop_type -> our prop_type (same map the props client uses).
_PROP_MAP = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "threes": "threes_made", "steals": "steals", "blocks": "blocks",
    "points_rebounds_assists": "pra", "points_rebounds": "pr",
    "points_assists": "pa", "rebounds_assists": "ra",
}

# Single-stat columns in player_game_logs (combos handled via COMBO_PARTS).
_STAT_COLS = {"points", "rebounds", "assists", "threes_made",
              "steals", "blocks", "turnovers"}


class LineCaptureError(Exception):
    pass


def has_key() -> bool:
    return bool(_KEY)


def _get(path, params):
    if not _KEY:
        raise LineCaptureError("No BALLDONTLIE_API_KEY in .env")
    r = requests.get(f"{_BASE}/{path}", params=params,
                     headers={"Authorization": _KEY}, timeout=30)
    if r.status_code == 429:
        raise LineCaptureError("rate limit")
    if r.status_code != 200:
        raise LineCaptureError(f"HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


def _get_all(path, params):
    """Return {"data": [all rows across every page]} by following next_cursor.

    balldontlie paginates (25/page on some v2 odds routes), so a single-page
    read silently drops most of the data. Returns the SAME {"data": [...]} shape
    callers expect, with every page merged in."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    rows = []
    for _ in range(100):  # hard cap against a bad cursor
        payload = _get(path, params)
        rows.extend(payload.get("data", []) or [])
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return {"data": rows}


def ensure_table():
    """Create historical_props if missing. One row per (game, player, prop,
    book, snapshot) — a UNIQUE key so re-running a day doesn't duplicate."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_props (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at   TEXT NOT NULL,
            game_date     TEXT NOT NULL,
            game_id       INTEGER NOT NULL,
            player_id     INTEGER NOT NULL,
            prop_type     TEXT NOT NULL,
            book          TEXT,
            snapshot      TEXT NOT NULL,          -- 'live' | 'opening'
            line          REAL NOT NULL,
            odds_over     INTEGER,
            odds_under    INTEGER,
            actual        REAL,                   -- filled by grade_settled()
            hit_over      INTEGER,                -- 1/0/NULL(push or ungraded)
            graded_at     TEXT,
            UNIQUE(game_id, player_id, prop_type, book, snapshot, line)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hp_game "
                 "ON historical_props(game_id, player_id, prop_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hp_ungraded "
                 "ON historical_props(graded_at)")
    conn.commit()
    conn.close()


def _games_on(day_iso: str):
    """[(game_id, game_date)] for a date, from balldontlie (all pages)."""
    data = _get_all("v1/games", {"dates[]": day_iso, "per_page": 100})
    return [(g["id"], g.get("date", day_iso)) for g in data.get("data", [])]


def _store_rows(rows):
    """Insert rows; INSERT OR IGNORE so the UNIQUE key dedupes. Returns count
    actually inserted."""
    if not rows:
        return 0
    conn = get_connection()
    before = conn.total_changes
    conn.executemany("""
        INSERT OR IGNORE INTO historical_props
          (captured_at, game_date, game_id, player_id, prop_type, book,
           snapshot, line, odds_over, odds_under)
        VALUES (:captured_at,:game_date,:game_id,:player_id,:prop_type,:book,
                :snapshot,:line,:odds_over,:odds_under)
    """, rows)
    conn.commit()
    inserted = conn.total_changes - before
    conn.close()
    return inserted


def _rows_from_props(payload, game_id, game_date, snapshot, now):
    """Turn a player_props API payload into storable per-book rows."""
    out = []
    for p in payload.get("data", []):
        prop = _PROP_MAP.get(p.get("prop_type"))
        if not prop:
            continue
        mkt = p.get("market") or {}
        if mkt.get("type") != "over_under":
            continue  # milestone markets are a different bet
        pid = p.get("player_id")
        if pid is None:
            continue
        try:
            line = float(p["line_value"])
        except (TypeError, ValueError):
            continue
        out.append({
            "captured_at": now, "game_date": game_date, "game_id": game_id,
            "player_id": pid, "prop_type": prop, "book": p.get("vendor"),
            "snapshot": snapshot, "line": line,
            "odds_over": mkt.get("over_odds"),
            "odds_under": mkt.get("under_odds"),
        })
    return out


def capture_today():
    """Snapshot every live over/under prop on today's slate. Returns a summary
    dict. Safe to run repeatedly; the UNIQUE key keeps identical lines from
    duplicating (a moved line IS a new row, which is what we want)."""
    ensure_table()
    if not _KEY:
        return {"captured": 0, "note": "no API key"}
    now = datetime.utcnow().isoformat(timespec="seconds")
    today = date.today().isoformat()
    inserted = seen = 0
    for gid, gdate in _games_on(today):
        payload = _get_all("v2/odds/player_props", {"game_id": gid})
        rows = _rows_from_props(payload, gid, gdate, "live", now)
        seen += len(rows)
        inserted += _store_rows(rows)
    return {"snapshot": "live", "date": today,
            "rows_seen": seen, "rows_inserted": inserted}


def backfill_opening(start_date: str, end_date: str):
    """Pull the opening-props endpoint for each date in [start,end] and store
    the opening lines. A real-line head start for games already played this
    season (and last season where coverage reaches). GOAT tier."""
    ensure_table()
    if not _KEY:
        return {"captured": 0, "note": "no API key"}
    now = datetime.utcnow().isoformat(timespec="seconds")
    d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    inserted = seen = days = 0
    day = d0
    while day <= d1:
        diso = day.isoformat()
        try:
            games = _games_on(diso)
        except LineCaptureError:
            day += timedelta(days=1)
            continue
        for gid, gdate in games:
            try:
                payload = _get_all("nba/v2/odds/player_props/opening",
                                   {"game_id": gid})
            except LineCaptureError:
                continue  # no opening coverage for this game — skip
            rows = _rows_from_props(payload, gid, gdate, "opening", now)
            seen += len(rows)
            inserted += _store_rows(rows)
        days += 1
        day += timedelta(days=1)
    return {"snapshot": "opening", "window": f"{start_date}..{end_date}",
            "days": days, "rows_seen": seen, "rows_inserted": inserted}


def _actual_stat(conn, player_id, game_id, prop_type):
    """The player's actual stat for a final game, or None if not logged yet."""
    row = conn.execute(
        "SELECT * FROM player_game_logs WHERE player_id=? AND game_id=?",
        (player_id, game_id)).fetchone()
    if not row:
        return None
    if prop_type in COMBO_PARTS:
        try:
            return float(sum(row[c] for c in COMBO_PARTS[prop_type]))
        except (KeyError, TypeError):
            return None
    if prop_type in _STAT_COLS:
        v = row[prop_type]
        return float(v) if v is not None else None
    return None


def grade_settled():
    """For every ungraded stored line whose game now has a logged result, fill
    actual + hit_over (1 over / 0 under / NULL push). Returns a summary."""
    ensure_table()
    conn = get_connection()
    ungraded = conn.execute(
        "SELECT id, player_id, game_id, prop_type, line "
        "FROM historical_props WHERE graded_at IS NULL").fetchall()
    now = datetime.utcnow().isoformat(timespec="seconds")
    graded = pushes = still_pending = 0
    for r in ungraded:
        actual = _actual_stat(conn, r["player_id"], r["game_id"], r["prop_type"])
        if actual is None:
            still_pending += 1
            continue
        if actual == r["line"]:
            hit = None  # push
            pushes += 1
        else:
            hit = 1 if actual > r["line"] else 0
        conn.execute(
            "UPDATE historical_props SET actual=?, hit_over=?, graded_at=? "
            "WHERE id=?", (actual, hit, now, r["id"]))
        graded += 1
    conn.commit()
    conn.close()
    return {"graded": graded, "pushes": pushes, "still_pending": still_pending}


def coverage():
    """Quick read of how much real-line data we have accumulated."""
    ensure_table()
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) c FROM historical_props").fetchone()["c"]
    graded = conn.execute(
        "SELECT COUNT(*) c FROM historical_props WHERE graded_at IS NOT NULL "
        "AND hit_over IS NOT NULL").fetchone()["c"]
    by_snap = dict(conn.execute(
        "SELECT snapshot, COUNT(*) FROM historical_props "
        "GROUP BY snapshot").fetchall())
    games = conn.execute(
        "SELECT COUNT(DISTINCT game_id) c FROM historical_props").fetchone()["c"]
    conn.close()
    return {"rows_total": total, "rows_graded": graded,
            "distinct_games": games, "by_snapshot": by_snap}


if __name__ == "__main__":
    import json
    if not has_key():
        print("No BALLDONTLIE_API_KEY in .env")
        sys.exit(0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "capture"
    if cmd == "capture":
        print(json.dumps(capture_today(), indent=2))
    elif cmd == "opening":
        s = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
        e = sys.argv[3] if len(sys.argv) > 3 else s
        print(json.dumps(backfill_opening(s, e), indent=2))
    elif cmd == "grade":
        print(json.dumps(grade_settled(), indent=2))
    elif cmd == "coverage":
        print(json.dumps(coverage(), indent=2))
    else:
        print("usage: line_capture.py [capture|opening S E|grade|coverage]")
