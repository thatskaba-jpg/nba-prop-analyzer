"""
fetch_injuries.py — Phase 5 (GOAT): pull current player injury status.

GOAT unlocks balldontlie's player-injuries endpoint. Injuries matter for props:
when a starter is OUT, a teammate's usage, minutes, and scoring chances tend to
rise — so knowing who's unavailable sharpens (and sometimes invalidates) a
projection.

This stores current injuries in an `injuries` table (player_id -> status +
description + fetched_at). It's a SNAPSHOT of the present — unlike box scores it
has no history — so we timestamp it and treat it as "as of the last pull."

DEFENSIVE: the exact field names are confirmed against a live GOAT response in
verify() before we trust them. Never fabricated — a missing field is reported.

Run (after the stats download finishes):
    python3 src/collect/fetch_injuries.py --verify
    python3 src/collect/fetch_injuries.py          # store current injuries
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection  # noqa: E402
from balldontlie_client import iter_pages, _get, BallDontLieError  # noqa: E402


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS injuries (
            player_id   INTEGER PRIMARY KEY,
            player_name TEXT,
            status      TEXT,
            description TEXT,
            fetched_at  TEXT
        )
        """)
    conn.commit()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fetch_injuries():
    """Pull all current injuries and replace the injuries table snapshot."""
    conn = get_connection()
    _ensure_table(conn)
    stored = 0
    try:
        for rows, _cur in iter_pages("player_injuries", {}, start_cursor=None):
            conn.execute("BEGIN")
            for r in rows:
                player = r.get("player") or {}
                pid = player.get("id")
                if pid is None:
                    continue
                name = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
                status = (r.get("status") or "").strip()
                desc = (r.get("description") or r.get("return_date") or "")
                conn.execute(
                    "INSERT OR REPLACE INTO injuries "
                    "(player_id, player_name, status, description, fetched_at) "
                    "VALUES (?,?,?,?,?)",
                    (int(pid), name, status, str(desc), _now()))
                stored += 1
            conn.commit()
    except BallDontLieError as e:
        conn.close()
        print(f"Injuries request failed: {e}")
        print("If this is a tier/endpoint issue, injuries may need a specific "
              "GOAT add-on — we don't fabricate them.")
        return
    conn.close()
    print(f"Stored {stored} current injuries (as of {_now()} UTC).")


def current_injury(player_id: int):
    """Return (status, description) for a player, or None if not listed hurt."""
    conn = get_connection()
    row = conn.execute(
        "SELECT status, description FROM injuries WHERE player_id = ?",
        (player_id,)).fetchone() if _table_exists(conn) else None
    conn.close()
    return (row["status"], row["description"]) if row else None


def _table_exists(conn):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='injuries'"
    ).fetchone() is not None


def verify():
    import json
    try:
        payload = _get("player_injuries", {})
    except BallDontLieError as e:
        print(f"Verify failed: {e}")
        return
    data = payload.get("data", []) if isinstance(payload, dict) else []
    print(f"/player_injuries returned {len(data)} record(s).")
    if data:
        print("First raw record (real field names):")
        print(json.dumps(data[0], indent=2)[:1000])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        verify()
    else:
        fetch_injuries()
