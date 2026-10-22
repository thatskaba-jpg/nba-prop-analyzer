"""
fetch_team_pace.py — per-GAME team pace, for a TRUE recent-pace signal.

The rate model wants how fast a team has been playing LATELY, not its season
average and not a betting-total guess. balldontlie's V2 advanced feed carries a
`pace` (possessions/48) on every player row; a team's game pace is the pace its
players ran while on the floor. Player paces for the same team-game vary a little
(each is that player's on-court pace), so we take the MEAN of the team's non-zero
player paces that game as the team's game pace — a robust estimate from the data
we already have access to.

We store one row per (team, game) in `team_game_pace`, then expose
recent_team_pace(team, cutoff_date, n) which averages the team's last N games
STRICTLY BEFORE the cutoff — leak-free, same rule as player features.

Run (GOAT tier; paginates, resumable per season via the stats checkpoint style):
    python3 src/collect/fetch_team_pace.py --verify
    python3 src/collect/fetch_team_pace.py 2025 2026
"""

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection  # noqa: E402
from collect.goat_data import advanced_stats  # noqa: E402


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_game_pace (
            team       TEXT    NOT NULL,
            game_id    TEXT    NOT NULL,
            game_date  TEXT    NOT NULL,
            season     TEXT    NOT NULL,
            pace       REAL,
            fetched_at TEXT,
            PRIMARY KEY (team, game_id)
        )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_game_pace_team_date "
        "ON team_game_pace (team, game_date)")
    conn.commit()


def _team_game_paces(rows):
    """From V2 advanced rows, yield (team, game_id, game_date, season, pace).

    Keeps full-game rows (period 0/None), groups by (game_id, team), and takes
    the MEAN of non-zero player paces as the team's game pace. A team-game with
    no usable pace is skipped (never stored as a fabricated 0)."""
    # (game_id, team) -> {"paces": [...], "date":..., "season":...}
    agg = defaultdict(lambda: {"paces": [], "date": None, "season": None})
    for r in rows:
        if r.get("period", 0) not in (0, None):
            continue
        team = (r.get("team") or {}).get("abbreviation")
        game = r.get("game") or {}
        gid = game.get("id")
        if not team or gid is None:
            continue
        p = r.get("pace")
        key = (str(gid), team)
        slot = agg[key]
        slot["date"] = game.get("date")
        slot["season"] = game.get("season")
        if p is not None and p > 0:
            slot["paces"].append(float(p))
    for (gid, team), slot in agg.items():
        if not slot["paces"]:
            continue
        pace = sum(slot["paces"]) / len(slot["paces"])
        yield team, gid, slot["date"], str(slot["season"]), round(pace, 2)


def fetch_team_pace(season: int) -> int:
    """Pull one season's V2 advanced rows and store per-game team pace. Returns
    the number of team-game rows stored."""
    conn = get_connection()
    _ensure_table(conn)
    rows = advanced_stats(season=season)
    stored = 0
    conn.execute("BEGIN")
    for team, gid, gdate, gseason, pace in _team_game_paces(rows):
        if not gdate:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO team_game_pace "
            "(team, game_id, game_date, season, pace, fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            (team, gid, gdate, gseason, pace, _now()))
        stored += 1
    conn.commit()
    conn.close()
    print(f"Season {season}: stored {stored} team-game pace rows.")
    return stored


def recent_team_pace(team: str, cutoff_date: str, n: int = 10):
    """Average pace of a team's last `n` games STRICTLY BEFORE cutoff_date.

    Leak-free (same rule as player features). Returns None when we have no
    stored pace for the team before the cutoff, so the caller can fall back."""
    if not team:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT pace FROM team_game_pace "
            "WHERE team = ? AND game_date < ? AND pace IS NOT NULL "
            "ORDER BY game_date DESC LIMIT ?",
            (team.upper(), cutoff_date, int(n))).fetchall()
    except Exception:
        conn.close()
        return None
    conn.close()
    paces = [r[0] for r in rows if r[0] is not None]
    return round(sum(paces) / len(paces), 2) if paces else None


def _table_ready() -> bool:
    """True if the pace table exists AND holds at least one row — lets callers
    skip the query entirely before any collection has run."""
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='team_game_pace'").fetchone()
        if not exists:
            return False
        n = conn.execute("SELECT 1 FROM team_game_pace LIMIT 1").fetchone()
        return n is not None
    except Exception:
        return False
    finally:
        conn.close()


def verify():
    import sqlite3  # noqa: F401
    rows = advanced_stats(season=2026)
    got = list(_team_game_paces(rows))
    print(f"2026 advanced rows -> {len(got)} team-game pace estimates.")
    for team, gid, gdate, gseason, pace in got[:6]:
        print(f"  {team}  game {gid}  {gdate}  pace={pace}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        verify()
    elif len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            fetch_team_pace(int(arg))
    else:
        print("Usage: python3 src/collect/fetch_team_pace.py SEASON [SEASON ...]")
        print("       python3 src/collect/fetch_team_pace.py --verify")
