"""
features.py — Phase 4/5: build PRE-GAME features with NO data leakage.

The golden rule: to predict a player's stat in a game on date D, we may only
use that player's games STRICTLY BEFORE date D. This module is the single place
that enforces it, so every other part of the project inherits the guarantee.

Given a player and a cutoff date, it returns simple, honest features:
  - games_played_before: how many prior games we have (the SAMPLE SIZE — small
    samples mean low confidence, and we surface that everywhere).
  - avg_last_5 / avg_last_10 / avg_last_20: recent form over those windows.
  - season_avg: average so far this season (before the cutoff).
  - home/away splits (when available).

These are deliberately plain. Phase 5 adds opponent defense, rest days, role,
and injuries on top of this same leak-proof foundation.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

# The stat columns we can model. Keys are prop types; values are DB columns.
STAT_COLUMNS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes_made": "threes_made",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    # Combos computed on the fly from the base columns:
    "pra": None,     # points + rebounds + assists
    "pr": None,      # points + rebounds
    "pa": None,      # points + assists
    "ra": None,      # rebounds + assists
    "stocks": None,  # steals + blocks
}

# How each on-the-fly combo is built from base columns.
COMBO_PARTS = {
    "pra": ("points", "rebounds", "assists"),
    "pr": ("points", "rebounds"),
    "pa": ("points", "assists"),
    "ra": ("rebounds", "assists"),
    "stocks": ("steals", "blocks"),
}


def load_player_history(player_id: int) -> pd.DataFrame:
    """All of one player's games, oldest first, UNIFIED ACROSS DATA SOURCES.

    The free CSVs (2020-2024) and balldontlie (current) use DIFFERENT player-id
    systems, so the same person can appear under two ids — which previously made
    a player's history stop in 2024. We fix that here by resolving the player's
    NAME, then loading every id that shares it, and de-duplicating by game. The
    result is one continuous timeline from 2020 through the current season.
    """
    conn = get_connection()
    # Find the player's name, then every id that shares that name.
    name_row = conn.execute(
        "SELECT player_name FROM player_game_logs WHERE player_id = ? LIMIT 1",
        (player_id,)).fetchone()
    if not name_row:
        conn.close()
        return pd.DataFrame()
    name = name_row["player_name"]
    df = pd.read_sql_query(
        """
        SELECT game_date, season, is_home, opponent, matchup, game_id,
               minutes, points, rebounds, assists, threes_made,
               steals, blocks, turnovers, fouls
        FROM player_game_logs
        WHERE player_name = ?
        ORDER BY game_date ASC
        """,
        conn, params=(name,),
    )
    conn.close()
    if not df.empty:
        # The two sources can both hold the same game (overlap around 2024);
        # de-dupe on game_id + date so one game isn't counted twice.
        df = df.drop_duplicates(subset=["game_id", "game_date"]).sort_values(
            "game_date").reset_index(drop=True)
        # Exclude DNPs / scratches: a 0-minute game is an ABSENCE, not a real
        # "0-point performance", so counting it would unfairly drag projections
        # down. Only keep games the player actually appeared in. This is the
        # single source of truth, so the fix applies to projections, the
        # over/under probabilities, and the backtest alike.
        df = df[df["minutes"].fillna(0) > 0].reset_index(drop=True)
    if not df.empty:
        for col in ("steals", "blocks", "turnovers", "fouls"):
            if col in df.columns:
                df[col] = df[col].fillna(0).astype(int)
        df["pra"] = df["points"] + df["rebounds"] + df["assists"]
    return df


def _stat_series(df: pd.DataFrame, prop_type: str) -> pd.Series:
    if prop_type in COMBO_PARTS:
        parts = COMBO_PARTS[prop_type]
        series = df[list(parts)[0]].copy()
        for p in parts[1:]:
            series = series + df[p]
        return series
    col = STAT_COLUMNS.get(prop_type)
    if col is None:
        raise ValueError(f"Unknown prop_type '{prop_type}'. "
                         f"Choose from {list(STAT_COLUMNS)}.")
    return df[col]


def pregame_features(player_id: int, prop_type: str, cutoff_date: str,
                     history: pd.DataFrame = None) -> dict:
    """Build features for a player's game on cutoff_date using ONLY prior games.

    cutoff_date is 'YYYY-MM-DD'. We keep rows with game_date < cutoff_date.
    Passing a preloaded `history` avoids re-querying in tight loops (backtests).
    """
    df = history if history is not None else load_player_history(player_id)
    if df.empty:
        return {"games_played_before": 0}

    prior = df[df["game_date"] < cutoff_date]
    n = len(prior)
    if n == 0:
        return {"games_played_before": 0}

    stat = _stat_series(prior, prop_type)

    def tail_mean(k):
        return float(stat.tail(k).mean()) if n >= 1 else None

    feats = {
        "games_played_before": n,
        "avg_last_5": tail_mean(5),
        "avg_last_10": tail_mean(10),
        "avg_last_20": tail_mean(20),
        "season_avg": float(stat.mean()),
        # Average over the player's most recent SEASON on record (the season of
        # his latest prior game) — a stable anchor that doesn't straddle the
        # offseason gap the way a chronological "last 5" does early in the year.
        "recent_season_avg": None,
        "std_last_10": float(stat.tail(10).std(ddof=0)) if n >= 2 else None,
        # Full-sample spread: a more stable estimate of game-to-game volatility,
        # used by the overdispersion-aware probability model (Negative Binomial).
        # Prefer the last ~25 games so it reflects the current role, not seasons
        # of old data, but needs enough games to be meaningful.
        "std_recent": float(stat.tail(25).std(ddof=0)) if n >= 5 else (
            float(stat.std(ddof=0)) if n >= 2 else None),
        "avg_minutes_last_10": float(prior["minutes"].tail(10).mean()),
    }

    # recent_season_avg: average over the player's LATEST season on record.
    # Stable anchor for early-season projections (avoids the offseason-straddling
    # "last 5"). Falls back silently if there's no season column.
    #
    # Also expose the CURRENT-season sample and the PRIOR-season average so the
    # projection can blend them transparently: 1 game into a new season we lean
    # on the prior-season prior, and the weight shifts to this season's games as
    # they accumulate (see project_stat's early-season blend).
    try:
        if "season" in prior.columns and prior["season"].notna().any():
            _seasons = sorted(prior["season"].dropna().unique().tolist())
            _latest = _seasons[-1]
            _cur = prior[prior["season"] == _latest]
            _cur_s = _stat_series(_cur, prop_type)
            if len(_cur_s) > 0:
                feats["recent_season_avg"] = float(_cur_s.mean())
                feats["current_season_games"] = int(len(_cur_s))
                feats["current_season_avg"] = float(_cur_s.mean())
            if len(_seasons) >= 2:
                _prev = prior[prior["season"] == _seasons[-2]]
                _prev_s = _stat_series(_prev, prop_type)
                if len(_prev_s) > 0:
                    feats["prior_season_avg"] = float(_prev_s.mean())
    except Exception:
        pass

    # --- Phase 5 context features (still leak-free: all from `prior`) ---

    # Rest days: gap between the most recent prior game and the cutoff date.
    try:
        last_game = pd.to_datetime(prior["game_date"].iloc[-1])
        cutoff_dt = pd.to_datetime(cutoff_date)
        feats["rest_days"] = int((cutoff_dt - last_game).days)
    except Exception:
        feats["rest_days"] = None

    # Minutes trend: recent-5 minutes vs. prior baseline (rising role > 0).
    if n >= 6:
        recent_min = prior["minutes"].tail(5).mean()
        base_min = prior["minutes"].iloc[:-5].tail(15).mean()
        feats["minutes_trend"] = float(recent_min - base_min) if base_min else 0.0
    else:
        feats["minutes_trend"] = 0.0

    # Home vs away average for this stat, when the home flag is present.
    if "is_home" in prior.columns and prior["is_home"].notna().any():
        home_stat = _stat_series(prior[prior["is_home"] == 1], prop_type)
        away_stat = _stat_series(prior[prior["is_home"] == 0], prop_type)
        feats["home_avg"] = float(home_stat.mean()) if len(home_stat) else None
        feats["away_avg"] = float(away_stat.mean()) if len(away_stat) else None
    else:
        feats["home_avg"] = None
        feats["away_avg"] = None

    return feats


if __name__ == "__main__":
    # Tiny self-test against whatever is in the DB.
    conn = get_connection()
    row = conn.execute(
        "SELECT player_id, player_name, COUNT(*) c FROM player_game_logs "
        "GROUP BY player_id ORDER BY c DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if row:
        pid = row["player_id"]
        print(f"Testing features for {row['player_name']} (id={pid}, {row['c']} games)")
        hist = load_player_history(pid)
        mid_date = hist.iloc[len(hist) // 2]["game_date"]
        print(f"As of {mid_date}, points features:")
        for k, v in pregame_features(pid, "points", mid_date, hist).items():
            print(f"  {k}: {v}")
    else:
        print("No data in the database yet — run the collectors first.")
