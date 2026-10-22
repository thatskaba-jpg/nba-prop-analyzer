"""
load_csv_to_db.py — Phase 3: clean the downloaded CSVs into the database.

Reads the raw box-score CSVs, keeps only the columns we care about, derives
each player's opponent and home/away flag by pairing the two teams in a game,
and stores the result in data/bot.db (the same player_game_logs table the rest
of the project already expects).

How to run (from the project root, with .venv active):
    python3 src/collect/load_csv_to_db.py
"""

import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("pandas is not installed. Run:  pip install -r requirements.txt")
    sys.exit(1)

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection, init_schema, DB_PATH  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
FILES = [
    "regular_season_box_scores_2010_2024_part_1.csv",
    "regular_season_box_scores_2010_2024_part_2.csv",
    "regular_season_box_scores_2010_2024_part_3.csv",
]

# Only read the columns we need — keeps memory low (these files are large).
USE_COLS = [
    "season_year", "game_date", "gameId", "teamTricode",
    "personId", "personName", "minutes",
    "points", "reboundsTotal", "assists", "threePointersMade",
    "steals", "blocks", "turnovers", "foulsPersonal",
]


def _minutes_to_float(val):
    """CSV minutes can look like '34:12' (min:sec) or '34.0' or be blank."""
    if pd.isna(val):
        return 0.0
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return 0.0
    if ":" in s:
        mins, secs = s.split(":")[:2]
        try:
            return round(int(mins) + int(secs) / 60.0, 2)
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def load():
    init_schema()

    frames = []
    for name in FILES:
        path = RAW_DIR / name
        if not path.exists():
            print(f"Missing {name}. Run download_csv_data.py first.")
            return
        print(f"Reading {name} ...")
        frames.append(pd.read_csv(path, usecols=USE_COLS, low_memory=False))
    df = pd.concat(frames, ignore_index=True)
    print(f"Total player-game rows read: {len(df):,}")

    # Keep only 2020-onward seasons (season_year like "2020", "2021", ...).
    # The dataset ends at 2023-24, so this gives 2020-21 through 2023-24.
    # NOTE: the current 2024-25 / 2025-26 seasons are NOT in this free dataset;
    # live/current data needs a live feed (Phase 6), handled separately.
    before = len(df)
    df["_season_start"] = pd.to_numeric(
        df["season_year"].astype(str).str[:4], errors="coerce"
    )
    df = df[df["_season_start"] >= 2020].drop(columns=["_season_start"])
    print(f"Kept {len(df):,} rows from 2020 onward (dropped {before - len(df):,} older rows).")

    # Build a game -> list of the (up to) two team tricodes, so we can set each
    # player's opponent. For each gameId the two distinct teamTricodes are the
    # two sides.
    teams_per_game = (
        df[["gameId", "teamTricode"]]
        .drop_duplicates()
        .groupby("gameId")["teamTricode"]
        .apply(list)
        .to_dict()
    )

    def opponent_of(game_id, team):
        sides = teams_per_game.get(game_id, [])
        others = [t for t in sides if t != team]
        return others[0] if others else None

    # Normalize the date to YYYY-MM-DD (string sort == chronological order,
    # which we rely on to prevent future-peeking).
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["game_date"])

    conn = get_connection()
    # Faster bulk insert: one transaction.
    conn.execute("BEGIN")
    stored = 0
    for row in df.itertuples(index=False):
        opp = opponent_of(row.gameId, row.teamTricode)
        # We don't have an explicit home/away flag in these box scores, so we
        # leave is_home NULL for now; Phase 5 can backfill it from the game
        # totals file if we decide we need it. matchup is "TEAM vs OPP".
        matchup = f"{row.teamTricode} vs {opp}" if opp else row.teamTricode
        conn.execute(
            """
            INSERT OR REPLACE INTO player_game_logs
            (player_id, player_name, game_id, game_date, season, matchup,
             is_home, opponent, minutes, points, rebounds, assists, threes_made,
             steals, blocks, turnovers, fouls)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(row.personId), str(row.personName), str(row.gameId),
                row.game_date, str(row.season_year), matchup,
                None, opp, _minutes_to_float(row.minutes),
                int(row.points or 0), int(row.reboundsTotal or 0),
                int(row.assists or 0), int(row.threePointersMade or 0),
                int(row.steals or 0), int(row.blocks or 0),
                int(row.turnovers or 0), int(row.foulsPersonal or 0),
            ),
        )
        stored += 1
    conn.commit()

    # Quick sanity report.
    cur = conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT player_id) AS p, "
                       "MIN(game_date) AS mn, MAX(game_date) AS mx FROM player_game_logs")
    r = cur.fetchone()
    conn.close()

    print(f"\nStored {stored:,} player-game rows into {DB_PATH}")
    print(f"Database now holds {r['n']:,} rows across {r['p']:,} players, "
          f"from {r['mn']} to {r['mx']}.")
    print("\nNext: we build the baseline prediction model (Phase 4).")


if __name__ == "__main__":
    load()
