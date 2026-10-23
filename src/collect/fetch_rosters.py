"""
fetch_rosters.py — official current team for every active player (GOAT).

Why: the dashboard was deriving a player's team from their most recent GAME,
which is wrong for a player traded in the offseason who hasn't suited up yet.
balldontlie's /players/active endpoint carries the OFFICIAL current team, so we
store it and prefer it.

We key by player NAME (not id), because the balldontlie player ids differ from
the CSV ids we also hold — and the dashboard already unifies a player by name.

Run (any time, GOAT is fast):
    python3 src/collect/fetch_rosters.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection  # noqa: E402
from balldontlie_client import iter_pages, BallDontLieError  # noqa: E402


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_teams (
            player_name TEXT PRIMARY KEY,
            team        TEXT,
            fetched_at  TEXT
        )
        """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(player_teams)")}
    if "position" not in cols:   # added for the per-game roster view
        conn.execute("ALTER TABLE player_teams ADD COLUMN position TEXT")
    conn.commit()


def fetch_rosters():
    conn = get_connection()
    _ensure_table(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    stored = 0
    try:
        for rows, _cur in iter_pages("players/active", {}, start_cursor=None):
            conn.execute("BEGIN")
            for p in rows:
                name = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
                team = (p.get("team") or {}).get("abbreviation")
                pos = (p.get("position") or "").strip().upper() or None
                if name and team:
                    conn.execute(
                        "INSERT OR REPLACE INTO player_teams "
                        "(player_name, team, fetched_at, position) "
                        "VALUES (?,?,?,?)",
                        (name, team, now, pos))
                    stored += 1
            conn.commit()
            print(f"  ...stored {stored} player teams so far")
    except BallDontLieError as e:
        conn.close()
        print(f"Roster fetch failed: {e} — re-run later (already-stored kept).")
        return
    conn.close()
    print(f"Done. Stored official current team for {stored} active players "
          f"(as of {now} UTC).")


def official_team(player_name: str):
    """Return the official current team abbr for a player name, or None."""
    conn = get_connection()
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='player_teams'").fetchone()
    if not exists:
        conn.close()
        return None
    row = conn.execute(
        "SELECT team FROM player_teams WHERE player_name = ?",
        (player_name,)).fetchone()
    conn.close()
    return row["team"] if row else None


if __name__ == "__main__":
    fetch_rosters()
