"""
backfill_opponents.py — fix missing opponents in the balldontlie seasons.

THE PROBLEM this fixes:
  The CSV seasons (2020-2024) store the opponent for every game. The balldontlie
  seasons (2024-25, 2025-26, ...) were loaded with only the player's OWN team in
  `matchup` and a NULL `opponent` — so the dashboard's recent-games table showed
  a blank opponent for current-season games.

THE FIX (no new data needed — it's already derivable):
  Every game_id has exactly two teams. A player's opponent is simply the OTHER
  team that appears under the same game_id. So we scan each game, find its two
  teams, and fill each side's opponent with the other. We also rewrite `matchup`
  into a readable "TEAM vs OPP" string so the UI reads clearly.

This is a one-time repair. It's safe to re-run (idempotent) and only touches
rows where opponent is currently missing. Run it after any new balldontlie pull:

    python3 src/collect/backfill_opponents.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
CSV_FILES = [
    "regular_season_box_scores_2010_2024_part_1.csv",
    "regular_season_box_scores_2010_2024_part_2.csv",
    "regular_season_box_scores_2010_2024_part_3.csv",
]


def backfill():
    conn = get_connection()

    # Which games still have at least one row with a missing opponent?
    games = conn.execute(
        "SELECT DISTINCT game_id FROM player_game_logs "
        "WHERE opponent IS NULL OR opponent = ''"
    ).fetchall()
    if not games:
        print("Nothing to fix — every row already has an opponent.")
        conn.close()
        return

    print(f"Found {len(games):,} games needing opponent backfill. Working...")
    fixed_rows = 0
    skipped_games = 0

    for g in games:
        gid = g["game_id"]
        # The distinct teams that played in this game (from `matchup`, which holds
        # each player's own team abbreviation).
        teams = [r["matchup"] for r in conn.execute(
            "SELECT DISTINCT matchup FROM player_game_logs WHERE game_id = ? "
            "AND matchup IS NOT NULL AND matchup != ''", (gid,)).fetchall()]
        teams = [t for t in teams if t]
        if len(teams) != 2:
            # Can't pair (only one team's rows present, or odd data) — skip safely.
            skipped_games += 1
            continue
        a, b = teams
        # For each side, the opponent is the other team. Rewrite matchup readable.
        cur = conn.execute(
            "UPDATE player_game_logs SET opponent = ?, matchup = ? "
            "WHERE game_id = ? AND matchup = ?", (b, f"{a} vs {b}", gid, a))
        fixed_rows += cur.rowcount
        cur = conn.execute(
            "UPDATE player_game_logs SET opponent = ?, matchup = ? "
            "WHERE game_id = ? AND matchup = ?", (a, f"{b} vs {a}", gid, b))
        fixed_rows += cur.rowcount

    conn.commit()

    # Report the result honestly.
    remaining = conn.execute(
        "SELECT COUNT(*) FROM player_game_logs "
        "WHERE opponent IS NULL OR opponent = ''").fetchone()[0]
    conn.close()

    print(f"Done. Filled opponents on {fixed_rows:,} rows.")
    if skipped_games:
        print(f"  ({skipped_games:,} games skipped — only one team's rows were "
              f"present, so no opponent could be paired.)")
    print(f"Rows still missing an opponent: {remaining:,}"
          + (" — all fixed!" if remaining == 0 else
             " (these are single-team games; harmless)."))


def backfill_home_away():
    """Fill is_home for the CSV seasons from the raw CSV `matchup` column.

    THE PROBLEM: the balldontlie seasons already know home/away, but the CSV
    loader left is_home NULL for 2020-2024 — so the dashboard's 'Where' column
    was blank for those games.

    THE FIX (already in the raw files): NBA matchup strings mark the home team
    with 'vs.' and the away team with '@' (e.g. 'OKC vs. PHX' = home,
    'OKC @ NOP' = away). We read that per (gameId, teamTricode) from the raw
    CSVs and set is_home on the matching rows. No new data needed.
    """
    try:
        import pandas as pd
    except ImportError:
        print("\n(Skipping home/away backfill — pandas not available.)")
        return

    conn = get_connection()
    need = conn.execute(
        "SELECT COUNT(*) FROM player_game_logs WHERE is_home IS NULL").fetchone()[0]
    if need == 0:
        print("\nHome/away: nothing to fix — every row already has it.")
        conn.close()
        return

    print(f"\nHome/away: {need:,} rows missing it. Reading raw CSVs for the "
          f"'vs./@' marker...")
    # Build a (gameId, teamTricode) -> is_home map from the raw matchup strings.
    flag = {}
    for name in CSV_FILES:
        path = RAW_DIR / name
        if not path.exists():
            continue
        df = pd.read_csv(path, usecols=["gameId", "teamTricode", "matchup"],
                         low_memory=False).drop_duplicates(["gameId", "teamTricode"])
        for row in df.itertuples(index=False):
            m = str(row.matchup)
            is_home = 1 if "vs." in m else (0 if "@" in m else None)
            if is_home is not None:
                flag[(str(row.gameId), str(row.teamTricode))] = is_home

    if not flag:
        print("  Couldn't read raw CSVs (maybe deleted). Leaving is_home as-is.")
        conn.close()
        return

    # Our stored matchup for CSV rows is "TEAM vs OPP", so the player's own team
    # is the first token — that's the teamTricode we keyed on.
    fixed = 0
    rows = conn.execute(
        "SELECT rowid, game_id, matchup FROM player_game_logs "
        "WHERE is_home IS NULL").fetchall()
    conn.execute("BEGIN")
    for r in rows:
        team = (r["matchup"] or "").split(" ")[0]
        val = flag.get((str(r["game_id"]), team))
        if val is not None:
            conn.execute("UPDATE player_game_logs SET is_home = ? WHERE rowid = ?",
                         (val, r["rowid"]))
            fixed += 1
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM player_game_logs WHERE is_home IS NULL").fetchone()[0]
    conn.close()
    print(f"Home/away: filled {fixed:,} rows. Still missing: {remaining:,}"
          + (" — all fixed!" if remaining == 0 else " (harmless leftovers)."))


if __name__ == "__main__":
    backfill()
    backfill_home_away()
