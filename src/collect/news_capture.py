"""
news_capture.py — timestamped snapshots of injury and lineup news.

A star ruled out moves his teammates' props, and slower books lag the move. To
find out whether that is a real, repeatable edge we need history: WHEN each piece
of news appeared, next to the line snapshots (historical_props) taken through
the day. Neither the injuries table nor the lineups feed keeps history, so this
module saves a row every time something CHANGES:

  injury_snapshots  (captured_at, player, status, description)  on a status change
  lineup_snapshots  (captured_at, game, team, player, starter)  when a lineup posts
                    or changes

Run it from src/collect/intraday.py several times a day.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_tables():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS injury_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT,
            status TEXT,
            description TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inj_snap ON injury_snapshots"
                 "(player_id, captured_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lineup_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            game_id INTEGER NOT NULL,
            team TEXT,
            player_id INTEGER,
            player_name TEXT,
            starter INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lineup_snap ON lineup_snapshots"
                 "(game_id, captured_at)")
    conn.commit()
    conn.close()


def changed_injuries(current, latest):
    """Rows worth saving.

    current  [{player_id, player_name, status, description}, ...] right now
    latest   {player_id: (status, description)} from the newest saved snapshot
    A player is saved when he is new, or his status/description changed."""
    out = []
    for r in current:
        key = (r.get("status") or "", r.get("description") or "")
        if latest.get(r["player_id"]) != key:
            out.append(r)
    return out


def snapshot_injuries(now=None):
    """Save injury changes since the last snapshot. Returns how many rows."""
    ensure_tables()
    now = now or _now()
    conn = get_connection()
    current = [dict(r) for r in conn.execute(
        "SELECT player_id, player_name, status, description FROM injuries").fetchall()]
    latest = {}
    for r in conn.execute(
            "SELECT s.player_id, s.status, s.description FROM injury_snapshots s "
            "JOIN (SELECT player_id, MAX(captured_at) mc FROM injury_snapshots "
            "GROUP BY player_id) m ON s.player_id=m.player_id AND s.captured_at=m.mc"):
        latest[r["player_id"]] = (r["status"] or "", r["description"] or "")
    new = changed_injuries(current, latest)
    for r in new:
        conn.execute("INSERT INTO injury_snapshots (captured_at, player_id, "
                     "player_name, status, description) VALUES (?,?,?,?,?)",
                     (now, r["player_id"], r["player_name"], r["status"],
                      r["description"]))
    conn.commit()
    conn.close()
    return len(new)


def lineup_signature(rows):
    """An order-free fingerprint of a posted lineup, for change detection."""
    return frozenset((r.get("team"), r.get("player_id"), bool(r.get("starter")))
                     for r in rows)


def snapshot_lineups(game_ids, fetch=None, now=None):
    """Save each game's lineup when it first posts or changes. Returns the number
    of games saved. `fetch(game_id)` -> rows [{team, player_id, player_name,
    starter}]; the default reads balldontlie's lineups feed."""
    ensure_tables()
    now = now or _now()
    if fetch is None:
        from collect.goat_data import lineups

        def fetch(game_id):
            rows = []
            for r in lineups(game_id):
                p = r.get("player") or {}
                rows.append({
                    "team": (r.get("team") or {}).get("abbreviation"),
                    "player_id": p.get("id"),
                    "player_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                    "starter": bool(r.get("starter"))})
            return rows
    conn = get_connection()
    saved = 0
    for gid in game_ids:
        rows = fetch(gid)
        if not rows:
            continue                                   # not posted yet
        prev = conn.execute(
            "SELECT team, player_id, starter FROM lineup_snapshots WHERE game_id=? "
            "AND captured_at=(SELECT MAX(captured_at) FROM lineup_snapshots "
            "WHERE game_id=?)", (gid, gid)).fetchall()
        prev_sig = frozenset((r["team"], r["player_id"], bool(r["starter"])) for r in prev)
        if prev_sig == lineup_signature(rows):
            continue
        for r in rows:
            conn.execute("INSERT INTO lineup_snapshots (captured_at, game_id, team, "
                         "player_id, player_name, starter) VALUES (?,?,?,?,?,?)",
                         (now, gid, r.get("team"), r.get("player_id"),
                          r.get("player_name"), int(bool(r.get("starter")))))
        saved += 1
    conn.commit()
    conn.close()
    return saved
