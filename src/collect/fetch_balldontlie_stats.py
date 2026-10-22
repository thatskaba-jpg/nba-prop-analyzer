"""
fetch_balldontlie_stats.py — Phase 3 (live/current data via balldontlie).

Pulls per-game player stats for one or more seasons from balldontlie and stores
them in the SAME player_game_logs table the CSV loader uses. That way the model
sees one unified history: 2020-2024 (free CSVs) + current seasons (this script).

Verified response shape (from a real call):
  row.pts / reb / ast / fg3m / min
  row.player.{id, first_name, last_name, team_id}
  row.team.abbreviation
  row.game.{date, season, home_team_score, visitor_team_score, ...}
  (home/away derived: the player's team vs the other team in the game)

NOTE on seasons: balldontlie's season number is the START year. season=2024
means the 2024-25 season. season=2025 means the current 2025-26 season.

How to run (from project root, .venv active):
    python3 src/collect/fetch_balldontlie_stats.py 2024
    python3 src/collect/fetch_balldontlie_stats.py 2024 2025   (multiple seasons)

Heads-up: the trial allows only 5 requests/minute, so a full season (many
pages) takes a while. The client paces itself automatically — let it run.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection, init_schema, DB_PATH  # noqa: E402
from balldontlie_client import iter_pages, BallDontLieError, RATE_NOTE  # noqa: E402

# Checkpoints let a stopped download resume instead of starting the season over.
CKPT_DIR = Path(__file__).resolve().parents[2] / "data" / "checkpoints"


def _ckpt_path(season: int) -> Path:
    return CKPT_DIR / f"stats_season_{season}.cursor"


def _read_ckpt(season: int):
    p = _ckpt_path(season)
    if p.exists():
        return p.read_text().strip() or None
    return None


def _write_ckpt(season: int, cursor):
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    _ckpt_path(season).write_text(str(cursor) if cursor is not None else "")


def _clear_ckpt(season: int):
    p = _ckpt_path(season)
    if p.exists():
        p.unlink()


def _minutes_to_float(val):
    if val is None:
        return 0.0
    s = str(val).strip()
    if s == "" or s.lower() == "none":
        return 0.0
    if ":" in s:
        m, sec = (s.split(":") + ["0"])[:2]
        try:
            return round(int(m) + int(sec) / 60.0, 2)
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _store_rows(conn, rows, season) -> int:
    """Store one page of stat rows. Returns how many were stored."""
    stored = 0
    for r in rows:
        player = r.get("player") or {}
        team = r.get("team") or {}
        game = r.get("game") or {}

        player_id = player.get("id")
        if player_id is None:
            continue
        name = f"{player.get('first_name','')} {player.get('last_name','')}".strip()
        game_id = str(game.get("id"))
        game_date = game.get("date")
        team_abbr = team.get("abbreviation")

        home_id = game.get("home_team_id")
        player_team_id = team.get("id")
        is_home = None
        if home_id is not None and player_team_id is not None:
            is_home = 1 if player_team_id == home_id else 0
        matchup = team_abbr or ""

        # Defensive field lookup: balldontlie uses stl/blk/pf and (historically)
        # either 'turnover' or 'turnovers'. Try the known aliases so we never
        # silently store a zero because of a key-name difference.
        def g(*keys):
            for k in keys:
                v = r.get(k)
                if v is not None:
                    return v
            return 0

        conn.execute(
            """
            INSERT OR REPLACE INTO player_game_logs
            (player_id, player_name, game_id, game_date, season, matchup,
             is_home, opponent, minutes, points, rebounds, assists, threes_made,
             steals, blocks, turnovers, fouls)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(player_id), name, game_id, game_date, str(season), matchup,
                is_home, None, _minutes_to_float(r.get("min")),
                int(g("pts") or 0), int(g("reb") or 0),
                int(g("ast") or 0), int(g("fg3m") or 0),
                int(g("stl") or 0), int(g("blk") or 0),
                int(g("turnover", "turnovers", "to") or 0), int(g("pf") or 0),
            ),
        )
        stored += 1
    return stored


def fetch_season(season: int):
    """Download and store every player-game row for one season, RESUMABLY.

    After each page we commit the rows and save the next cursor to a checkpoint
    file. If the run stops, re-running resumes from that cursor instead of
    starting the season over. When the season finishes, the checkpoint clears.
    """
    init_schema()
    resume_cursor = _read_ckpt(season)
    if resume_cursor:
        print(f"Resuming season {season} from saved checkpoint (cursor {resume_cursor}).")
    else:
        print(f"Fetching season {season} ({season}-{str(season+1)[-2:]}) from balldontlie...")
    print(f"Paginates at {RATE_NOTE}. "
          "Safe to stop and re-run — it resumes where it left off.")

    conn = get_connection()
    total = 0
    try:
        for rows, next_cursor in iter_pages("stats", {"seasons[]": season},
                                            start_cursor=resume_cursor):
            conn.execute("BEGIN")
            total += _store_rows(conn, rows, season)
            conn.commit()
            # Checkpoint AFTER the commit, so a stop never loses a saved page.
            if next_cursor:
                _write_ckpt(season, next_cursor)
            print(f"  ...stored {total} rows so far"
                  + ("" if next_cursor else " (last page)"))
    except BallDontLieError as e:
        conn.close()
        print(f"API error: {e}")
        print("Progress saved — just re-run the same command to resume.")
        return
    except KeyboardInterrupt:
        conn.close()
        print("\nStopped. Progress saved — re-run the same command to resume.")
        return

    # Reached the end cleanly.
    _clear_ckpt(season)
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM player_game_logs WHERE season = ?", (str(season),)
    )
    n = cur.fetchone()["n"]
    conn.close()
    if total == 0:
        print(f"No stats returned for season {season} "
              f"(season may not have started, or not on your tier).")
    else:
        print(f"Done season {season}: stored {total} rows this run; "
              f"that season now has {n:,} rows in {DB_PATH}.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 src/collect/fetch_balldontlie_stats.py SEASON [SEASON ...]")
        print("Example (2024-25 season): python3 src/collect/fetch_balldontlie_stats.py 2024")
        sys.exit(1)
    for arg in sys.argv[1:]:
        fetch_season(int(arg))
