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
    "threes_att": "threes_att",
    "fgm": "fgm",
    "fga": "fga",
    "ftm": "ftm",
    "fta": "fta",
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
    # Build the column list defensively: attempts columns (threes_att/fga/fta)
    # may not exist yet on a DB created before this migration ran. Select them
    # only when present, and synthesize 0-filled columns otherwise, so the app
    # keeps working between the code deploy and the backfill/restart.
    have = {r[1] for r in conn.execute("PRAGMA table_info(player_game_logs)")}
    optional = [c for c in ("threes_att", "fgm", "fga", "ftm", "fta")
                if c in have]
    select_cols = (
        "game_date, season, is_home, opponent, matchup, game_id, "
        "minutes, points, rebounds, assists, threes_made, "
        "steals, blocks, turnovers, fouls"
        + ("".join(f", {c}" for c in optional))
    )
    df = pd.read_sql_query(
        f"""
        SELECT {select_cols}
        FROM player_game_logs
        WHERE player_name = ?
        ORDER BY game_date ASC
        """,
        conn, params=(name,),
    )
    conn.close()
    # Backfill any missing optional column with 0 so downstream code (STAT_COLUMNS
    # for threes_att/fga/fta) finds the column regardless of DB age.
    for c in ("threes_att", "fgm", "fga", "ftm", "fta"):
        if c not in df.columns:
            df[c] = 0
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
        for col in ("steals", "blocks", "turnovers", "fouls",
                    "threes_att", "fgm", "fga", "ftm", "fta"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
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
                     history: pd.DataFrame = None,
                     current_team: str = None) -> dict:
    """Build features for a player's game on cutoff_date using ONLY prior games.

    cutoff_date is 'YYYY-MM-DD'. We keep rows with game_date < cutoff_date.
    Passing a preloaded `history` avoids re-querying in tight loops (backtests).

    current_team (optional) is the player's CURRENT team abbreviation from the
    live roster feed (which reflects offseason trades the game log can't). When
    given, we compute current-team-only form so a just-traded player is judged
    on his new team, not stale games from the old one. When omitted, we infer it
    from his most recent prior game.
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
        # Sample std (ddof=1). ddof=0 under-states the spread of a short window
        # (5 games: variance 20% too small), which made the over/under
        # probabilities too confident.
        "std_last_10": float(stat.tail(10).std(ddof=1)) if n >= 2 else None,
        # Full-sample spread: a more stable estimate of game-to-game volatility,
        # used by the overdispersion-aware probability model (Negative Binomial).
        # Prefer the last ~25 games so it reflects the current role, not seasons
        # of old data, but needs enough games to be meaningful.
        "std_recent": float(stat.tail(25).std(ddof=1)) if n >= 5 else (
            float(stat.std(ddof=1)) if n >= 2 else None),
        "avg_minutes_last_10": float(prior["minutes"].tail(10).mean()),
    }

    # How many of those prior games belong to the season the cutoff falls in.
    # Early in a season most of the history is LAST season's, so this is what
    # tells the confidence label (and the reader) how thin this year's sample is.
    # NBA seasons are labelled by their start year and tip off in October.
    try:
        cut = pd.to_datetime(cutoff_date)
        cut_season = str(cut.year if cut.month >= 10 else cut.year - 1)
        feats["games_this_season"] = int(
            (prior["season"].astype(str) == cut_season).sum())
    except Exception:
        feats["games_this_season"] = None

    # --- Rate-model features (per-minute production + projected minutes) ------
    # A deeper projection path: stat = per-minute rate x projected minutes x
    # pace. Here we compute the player's own pieces (leak-free, from `prior`);
    # the pace multiplier is applied later from game context.
    #   per_min_rate : recent stat per minute, weighted toward recent games but
    #                  computed as total_stat/total_minutes (not a mean of
    #                  ratios) so garbage-time minutes don't distort it.
    #   proj_minutes : expected minutes tonight = recent minutes, nudged by the
    #                  minutes trend (rising/falling role). This is how we
    #                  explicitly VALUE minutes in the projection.
    try:
        mins = prior["minutes"].astype(float)
        # Weighted per-minute rate: last-5 (0.5), last-10 (0.3), season (0.2),
        # each as total_stat/total_minutes over that window.
        def _rate(k):
            m = mins.tail(k).sum()
            s = stat.tail(k).sum()
            return (float(s) / float(m)) if m > 0 else None
        r5, r10, rseason = _rate(5), _rate(10), _rate(n)
        parts = [(r5, 0.5), (r10, 0.3), (rseason, 0.2)]
        parts = [(v, w) for v, w in parts if v is not None]
        if parts:
            tw = sum(w for _, w in parts)
            feats["per_min_rate"] = sum(v * w for v, w in parts) / tw
        else:
            feats["per_min_rate"] = None
        # Projected minutes: recent-10 minutes as the base.
        feats["proj_minutes"] = float(mins.tail(10).mean())
    except Exception:
        feats["per_min_rate"] = None
        feats["proj_minutes"] = None
    # --- Trade-aware team context (leak-free: all from `prior`) ---------------
    # The per-game `matchup` string starts with the player's OWN team for that
    # game (e.g. "CLE vs NYK" -> "CLE"), so we can tell which team each prior
    # game was played on. A traded player's recent games may be on his OLD team,
    # which makes recent-form weighting misleading — so we also compute his
    # form on his CURRENT team only, and let project_stat lean on that.
    feats["current_team"] = None
    feats["games_on_current_team"] = 0
    feats["current_team_avg"] = None
    feats["recent_is_stale_team"] = False
    try:
        if "matchup" in prior.columns and prior["matchup"].notna().any():
            game_team = (prior["matchup"].fillna("")
                         .str.split(" ").str[0].str.upper())
            cur = (current_team or "").upper() or None
            if cur is None:
                # Infer from the most recent prior game when not supplied.
                last_team = game_team.iloc[-1]
                cur = last_team or None
            feats["current_team"] = cur
            if cur is not None:
                on_cur_mask = (game_team == cur).values
                cur_stat = stat[on_cur_mask]
                feats["games_on_current_team"] = int(len(cur_stat))
                if len(cur_stat) >= 1:
                    feats["current_team_avg"] = float(cur_stat.mean())
                # "Stale recent form" = his most recent game was NOT on his
                # current team (a genuine, just-happened trade). In that case
                # last-5/last-10 describe the old team's role.
                feats["recent_is_stale_team"] = bool(
                    len(game_team) and game_team.iloc[-1] != cur)
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
