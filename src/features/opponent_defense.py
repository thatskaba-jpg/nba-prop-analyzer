"""
opponent_defense.py — Phase 5: how good/bad is each team's defense?

We measure defense simply and honestly: the average TOTAL points a team allows
its opponents to score, computed ONLY from games before a cutoff date (no
leakage). A team that allows a lot of points is a "soft" defense (good for the
over); a team that allows few is "tough" (good for the under).

How we get "points allowed" from player box scores:
  For each game, sum every player's points by team. In a game, the points the
  OTHER team scored are the points THIS team allowed. We pair the two teams in
  each game to get each side's points-allowed.

We express a team's defense as a RATING relative to the league average:
    defense_factor = team_points_allowed_avg / league_points_allowed_avg
  factor > 1.0  -> allows more than average (soft defense)
  factor < 1.0  -> allows fewer than average (tough defense)
This factor is what the projection adjustment multiplies by (gently).

Caveat: our DB stores opponent as a team abbreviation for the CSV seasons and
leaves it blank for the balldontlie seasons (different schema). So this works
cleanly within the CSV era; for balldontlie rows we fall back to league-average
(factor 1.0) until Phase 6 wiring resolves team ids. Stated plainly, not hidden.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402


def _game_team_points(cutoff_date: str) -> pd.DataFrame:
    """Total points scored by each (game, team) BEFORE cutoff_date."""
    conn = get_connection()
    df = pd.read_sql_query(
        """
        SELECT game_id, game_date, matchup, opponent, points
        FROM player_game_logs
        WHERE game_date < ?
        """,
        conn, params=(cutoff_date,),
    )
    conn.close()
    if df.empty:
        return df
    # The team is the first token of matchup (e.g. "LAL vs BOS" -> "LAL").
    df["team"] = df["matchup"].str.split().str[0]
    grouped = (
        df.groupby(["game_id", "team", "opponent"], dropna=False)["points"]
        .sum()
        .reset_index()
        .rename(columns={"points": "team_points"})
    )
    return grouped


def defense_factors(cutoff_date: str) -> dict:
    """Return {team_abbr: defense_factor} using games before cutoff_date.

    points_allowed for a team = points scored by its opponents against it.
    """
    gtp = _game_team_points(cutoff_date)
    if gtp.empty or gtp["opponent"].isna().all():
        return {}

    # points a team ALLOWED = team_points of the OTHER side in the same game.
    # Build a lookup of (game_id, team) -> team_points, then for each row the
    # allowed value is the opponent's scored points.
    scored = {(r.game_id, r.team): r.team_points for r in gtp.itertuples(index=False)}
    allowed_rows = []
    for r in gtp.itertuples(index=False):
        opp_scored = scored.get((r.game_id, r.opponent))
        if opp_scored is not None:
            allowed_rows.append({"team": r.team, "allowed": opp_scored})
    if not allowed_rows:
        return {}

    adf = pd.DataFrame(allowed_rows)
    team_allowed = adf.groupby("team")["allowed"].mean()
    league_avg = team_allowed.mean()
    if league_avg <= 0:
        return {}
    factors = (team_allowed / league_avg).to_dict()
    return factors


if __name__ == "__main__":
    # Show the softest and toughest defenses as of a date in the data.
    cutoff = "2024-01-01"
    f = defense_factors(cutoff)
    if not f:
        print("No opponent-resolved games before cutoff (or no data yet).")
    else:
        s = pd.Series(f).sort_values()
        print(f"Defense factors as of {cutoff} (lower = tougher defense):\n")
        print("Toughest 5:"); print(s.head(5).round(3).to_string())
        print("\nSoftest 5:"); print(s.tail(5).round(3).to_string())
