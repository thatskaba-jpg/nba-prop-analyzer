"""
positional_defense.py — Phase 5 upgrade: defense by POSITION, per stat.

WHY THIS EXISTS (the user's point, and the brief's "defensive tendencies"):
  The blunt version (opponent_defense.py) asks only "how many TOTAL points does
  this team allow?" That misses the real matchup question: a team can shut down
  guards but get torched by centers. A guard's points projection should care how
  the opponent defends GUARDS, not the team's overall average.

WHAT THIS DOES:
  1. Assigns each player a POSITION GROUP: Guard / Wing / Big.
       - If a `player_positions` table exists (filled from balldontlie's free
         `position` field by fetch_positions.py), we use that — the accurate path.
       - Otherwise we INFER the group from the player's own stat profile (bigs
         rebound a lot and rarely shoot threes; guards assist and shoot threes).
         This is an approximation, clearly labeled, and needs no API.
  2. For a cutoff date, computes — using ONLY prior games (no leakage) — how many
     of a STAT (points/rebounds/assists) each team allows TO EACH POSITION GROUP,
     versus the league average for that group. The result is a factor:
        factor > 1.0  -> this team is SOFT against that position for that stat
        factor < 1.0  -> this team is TOUGH against that position for that stat
  3. A projection adjustment then uses the factor for THIS player's group and
     the stat being projected — "how this team defends players like him."

HONESTY: inferred positions are a stand-in for real ones; points-allowed from
box scores is a team-total proxy, not play-by-play matchup tracking. Both are
stated, and the accurate `position` field slots in the moment it's fetched.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

POSITION_GROUPS = ("Guard", "Wing", "Big")

# Map balldontlie position codes to our three groups.
BDL_POSITION_MAP = {
    "G": "Guard", "PG": "Guard", "SG": "Guard", "G-F": "Wing",
    "F": "Wing", "SF": "Wing", "F-G": "Wing", "F-C": "Big",
    "C": "Big", "C-F": "Big", "PF": "Big",
}


def _has_positions_table(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='player_positions'").fetchone()
    return row is not None


def _known_positions(conn) -> dict:
    """player_id -> group, from the balldontlie-filled table (if present)."""
    if not _has_positions_table(conn):
        return {}
    out = {}
    for r in conn.execute("SELECT player_id, position FROM player_positions"):
        grp = BDL_POSITION_MAP.get((r["position"] or "").upper())
        if grp:
            out[r["player_id"]] = grp
    return out


def _infer_group(reb_pg: float, ast_pg: float, tpm_pg: float) -> str:
    """Infer a position group from average rebounds / assists / threes.

    Rough, transparent rules:
      - Big:   rebounds heavy AND few threes (interior player).
      - Guard: assists and/or threes prominent (perimeter creator/shooter).
      - Wing:  everything else (forwards / combo players).
    """
    if reb_pg >= 7.0 and tpm_pg < 1.0:
        return "Big"
    if ast_pg >= 4.0 or tpm_pg >= 1.8:
        return "Guard"
    return "Wing"


def player_groups(cutoff_date: str) -> dict:
    """player_id -> position group, using known positions first, else inferred
    from each player's PRIOR-GAME averages (leak-free)."""
    conn = get_connection()
    known = _known_positions(conn)

    # Compute prior-game averages per player to infer anyone not in `known`.
    df = pd.read_sql_query(
        "SELECT player_id, rebounds, assists, threes_made "
        "FROM player_game_logs WHERE game_date < ?",
        conn, params=(cutoff_date,))
    conn.close()
    groups = dict(known)
    if df.empty:
        return groups
    agg = df.groupby("player_id").agg(
        reb=("rebounds", "mean"), ast=("assists", "mean"),
        tpm=("threes_made", "mean"), n=("rebounds", "size")).reset_index()
    for r in agg.itertuples(index=False):
        if r.player_id in groups:
            continue  # trust the real position when we have it
        if r.n < 5:
            continue  # too few games to infer reliably
        groups[r.player_id] = _infer_group(r.reb, r.ast, r.tpm)
    return groups


def positional_defense_factors(stat: str, cutoff_date: str) -> dict:
    """Return {team_abbr: {group: factor}} for one stat, leak-free.

    factor = (stat this team allows to that position group) / (league avg for
    that group). >1 soft, <1 tough. Built only from games before cutoff_date.
    """
    if stat not in ("points", "rebounds", "assists"):
        raise ValueError("stat must be points, rebounds, or assists")

    conn = get_connection()
    df = pd.read_sql_query(
        f"SELECT player_id, matchup, opponent, {stat} AS val "
        "FROM player_game_logs "
        "WHERE game_date < ? AND opponent IS NOT NULL AND opponent != ''",
        conn, params=(cutoff_date,))
    conn.close()
    if df.empty:
        return {}

    groups = player_groups(cutoff_date)
    df["group"] = df["player_id"].map(groups)
    df = df.dropna(subset=["group"])
    if df.empty:
        return {}

    # Each row: a player of `group` scored `val` AGAINST `opponent`. So from the
    # defending team's view, `opponent` is the team that allowed it. Sum the
    # stat allowed per (opponent=defending team, group), then average per game
    # by dividing over distinct games is approximated by mean per player-row —
    # we use mean allowed per player-appearance, which is the matchup unit.
    allowed = (df.groupby(["opponent", "group"])["val"]
               .mean().reset_index()
               .rename(columns={"opponent": "team", "val": "allowed"}))

    # League average allowed per group (across all teams).
    league = allowed.groupby("group")["allowed"].mean().to_dict()

    factors = {}
    for r in allowed.itertuples(index=False):
        lg = league.get(r.group)
        if not lg or lg <= 0:
            continue
        factors.setdefault(r.team, {})[r.group] = round(r.allowed / lg, 3)
    return factors


if __name__ == "__main__":
    cutoff = "2024-01-01"
    stat = sys.argv[1] if len(sys.argv) > 1 else "points"
    conn = get_connection()
    has_tbl = _has_positions_table(conn)
    conn.close()
    print(f"Position source: "
          f"{'balldontlie table + inference' if has_tbl else 'INFERRED from stats (no positions table yet)'}")
    f = positional_defense_factors(stat, cutoff)
    if not f:
        print("No data before cutoff.")
    else:
        # Show which teams are softest/toughest vs Guards for this stat.
        rows = [(t, g["Guard"]) for t, g in f.items() if "Guard" in g]
        rows.sort(key=lambda x: x[1])
        print(f"\n{stat} allowed to GUARDS vs league avg as of {cutoff} "
              f"(lower = tougher):")
        print("Toughest 5:", [(t, v) for t, v in rows[:5]])
        print("Softest 5: ", [(t, v) for t, v in rows[-5:]])
