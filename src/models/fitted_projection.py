"""
fitted_projection.py — a projection FITTED to history instead of hand-tuned.

The rules-based projection blends L5/L10/season averages with fixed weights
(0.5/0.3/0.2) and then multiplies in a stack of hand-picked nudges. Nothing ever
checked those numbers against outcomes. This module learns the mapping from
leak-free pre-game features to the stat from ~200K player-games with a gradient
boosted model (Poisson loss, since these are counts).

Every feature for a game uses only information strictly BEFORE that game:
  - the player's own form (rolling and exponentially weighted, last season's
    average, games played this season, rest days, home/away), for the stat and
    for minutes
  - team context (each side's recent points scored/allowed)
  - teammates OUT: rotation players (18+ min) who logged 0 minutes in this game.
    The inactive list is public before tip, so this is a pre-game fact.

Use `build_features()` for a leak-free feature table of every played game,
`fit()` to train on games before a cutoff date, and `predict_mean()` to score.
Compare against the market with `src/backtest/projection_eval.py`.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402

STATS = ["points", "rebounds", "assists", "threes_made"]
COLS = STATS + ["minutes"]
ROTATION_MINUTES = 18.0          # a teammate averaging this many minutes counts as "rotation"
MIN_PRIOR_GAMES = 5


def load_logs():
    """Every box-score row (including DNPs), one per player per game."""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT player_id, player_name, game_id, game_date, season, matchup, "
        "is_home, opponent, minutes, points, rebounds, assists, threes_made "
        "FROM player_game_logs", conn)
    conn.close()
    df["game_id"] = df["game_id"].astype(str)
    df = df.drop_duplicates(["player_name", "game_id"])
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["team"] = df["matchup"].fillna("").str.split(" ").str[0].str.upper()
    df["opponent"] = df["opponent"].fillna("").str.upper()
    df["season_i"] = pd.to_numeric(df["season"], errors="coerce")
    df["is_home"] = pd.to_numeric(df["is_home"], errors="coerce")
    return df.sort_values(["game_date", "game_id", "player_name"]).reset_index(drop=True)


def _team_context(df):
    """Rolling points scored / allowed per team, as of BEFORE each game."""
    played = df[df["minutes"] > 0]
    tg = (played.groupby(["game_id", "team"])
          .agg(pts=("points", "sum"), date=("game_date", "first"),
               opp=("opponent", "first")).reset_index())
    allowed = tg[["game_id", "team", "pts"]].rename(
        columns={"team": "opp", "pts": "pts_allowed"})
    tg = tg.merge(allowed, on=["game_id", "opp"], how="left")
    tg = tg.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
    grp = tg.groupby("team", sort=False)
    for col in ("pts", "pts_allowed"):
        tg[f"{col}_r15"] = grp[col].transform(
            lambda s: s.shift(1).rolling(15, min_periods=3).mean())
    tg["pace_r15"] = tg["pts_r15"] + tg["pts_allowed_r15"]
    return tg[["game_id", "team", "pts_r15", "pts_allowed_r15", "pace_r15"]]


def _teammates_out(df):
    """Per (game, team): how many rotation players sat out, and the minutes they
    normally play. A DNP row inherits that player's rolling minutes from his last
    game played, so this is known before tip."""
    played = df[df["minutes"] > 0].sort_values(["player_name", "game_date", "game_id"])
    incl = played.groupby("player_name", sort=False)["minutes"].transform(
        lambda s: s.rolling(10, min_periods=3).mean())
    last = pd.DataFrame({"player_name": played["player_name"].values,
                         "game_id": played["game_id"].values,
                         "min10": incl.values})
    allr = df[["player_name", "game_date", "game_id", "team", "minutes"]].merge(
        last, on=["player_name", "game_id"], how="left")
    allr = allr.sort_values(["player_name", "game_date", "game_id"])
    allr["min10"] = allr.groupby("player_name", sort=False)["min10"].ffill()
    dnp = allr[(allr["minutes"] <= 0) & (allr["min10"] >= ROTATION_MINUTES)]
    return (dnp.groupby(["game_id", "team"])
            .agg(out_cnt=("player_name", "count"), out_min=("min10", "sum"))
            .reset_index())


def build_features(df=None):
    """Leak-free feature table for every PLAYED game with enough history."""
    df = load_logs() if df is None else df
    played = (df[df["minutes"] > 0]
              .sort_values(["player_name", "game_date", "game_id"])
              .reset_index(drop=True))
    by_player = played["player_name"]
    out = played[["player_id", "player_name", "game_id", "game_date", "season",
                  "season_i", "team", "opponent", "is_home"] + COLS].copy()

    feats = {}
    for c in COLS:
        shifted = played.groupby("player_name", sort=False)[c].shift(1)
        g = shifted.groupby(by_player, sort=False)
        for w in (3, 5, 10, 20):
            feats[f"{c}_r{w}"] = g.transform(
                lambda s, w=w: s.rolling(w, min_periods=1).mean())
        feats[f"{c}_ew10"] = g.transform(lambda s: s.ewm(halflife=10).mean())
        feats[f"{c}_ew40"] = g.transform(lambda s: s.ewm(halflife=40).mean())
        feats[f"{c}_std"] = shifted.groupby(
            [by_player, played["season"]], sort=False).transform(
            lambda s: s.expanding(min_periods=1).mean())
    feats = pd.DataFrame(feats)

    prev = (played.groupby(["player_name", "season_i"])[COLS].mean()
            .add_suffix("_prev").reset_index())
    prev["season_i"] = prev["season_i"] + 1
    out = pd.concat([out, feats], axis=1).merge(
        prev, on=["player_name", "season_i"], how="left")

    out["n_total"] = out.groupby("player_name", sort=False).cumcount()
    out["n_season"] = out.groupby(["player_name", "season"], sort=False).cumcount()
    dates = pd.to_datetime(out["game_date"])
    out["days_rest"] = dates.groupby(out["player_name"], sort=False).diff().dt.days

    tc = _team_context(df)
    out = out.merge(tc, on=["game_id", "team"], how="left")
    opp = tc.rename(columns={"team": "opponent", "pts_r15": "opp_pts_r15",
                             "pts_allowed_r15": "opp_allowed_r15",
                             "pace_r15": "opp_pace_r15"})
    out = out.merge(opp, on=["game_id", "opponent"], how="left")
    out = out.merge(_teammates_out(df), on=["game_id", "team"], how="left")
    out[["out_cnt", "out_min"]] = out[["out_cnt", "out_min"]].fillna(0.0)
    return out[out["n_total"] >= MIN_PRIOR_GAMES].reset_index(drop=True)


NON_FEATURES = {"player_id", "player_name", "game_id", "game_date", "season",
                "season_i", "team", "opponent"} | set(COLS)


def feature_columns(table):
    return [c for c in table.columns if c not in NON_FEATURES]


def fit(table, cutoff):
    """Train one model per stat on games dated BEFORE cutoff.

    Returns {stat: {"model", "alpha"}} where alpha is the negative-binomial
    dispersion (variance = mu + alpha * mu^2) estimated from the training
    residuals, so a probability can be read off the predicted mean."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    def make():
        return HistGradientBoostingRegressor(
            loss="poisson", max_iter=250, learning_rate=0.06, max_leaf_nodes=31,
            min_samples_leaf=100, l2_regularization=1.0, random_state=0)

    train = table[table["game_date"] < cutoff]
    # Dispersion is measured OUT OF SAMPLE: fit on everything before the last
    # year, read the residual spread on that last year. (In-sample residuals of a
    # boosted model are too small, which would make every probability too sharp.)
    split = (pd.to_datetime(cutoff) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    early = table[table["game_date"] < split]
    val = table[(table["game_date"] >= split) & (table["game_date"] < cutoff)]
    cols = feature_columns(table)
    models = {}
    for stat in STATS:
        probe = make().fit(early[cols], early[stat])
        mu = np.clip(probe.predict(val[cols]), 0.05, None)
        resid = val[stat].to_numpy() - mu
        alpha = float(np.sum(resid ** 2 - mu) / np.sum(mu ** 2))
        models[stat] = {"model": make().fit(train[cols], train[stat]),
                        "alpha": max(alpha, 0.01)}
    models["_columns"] = cols
    return models


def predict_mean(models, table, stat):
    """Predicted mean of `stat` for every row of `table`."""
    return np.clip(models[stat]["model"].predict(table[models["_columns"]]), 0.05, None)


def std_for(models, stat, mu):
    """Predictive std for a mean under the fitted dispersion."""
    return np.sqrt(mu + models[stat]["alpha"] * mu ** 2)


# --------------------------------------------------------------------------- #
# Saving, and scoring TODAY'S slate
#
# A game that hasn't been played has no box-score row, so we build a "virtual"
# one: every rostered player on both teams gets a placeholder row for the game
# (minutes=1, stats 0 -- the features of a row never use its own values), and
# anyone listed OUT gets a 0-minute row, exactly like a real DNP. Running the
# SAME feature builder over history + those rows means training and live
# scoring cannot drift apart.
# --------------------------------------------------------------------------- #

_MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted_projection.joblib"
_SLATE_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted_slate.json"
_slate_cache = {"mtime": None, "data": None}


def save(models, trained_through):
    import datetime as _dt
    import joblib
    models = dict(models)
    models["_meta"] = {
        "trained_through": trained_through,
        "trained_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    _MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(models, _MODEL_PATH, compress=3)


def load():
    """The saved models, or None if none has been trained yet."""
    try:
        import joblib
        return joblib.load(_MODEL_PATH)
    except Exception:
        return None


def _games_with_teams(day):
    """[(home_abbr, away_abbr)] for a date, from balldontlie."""
    from odds.line_capture import _get_all
    out = []
    for g in _get_all("v1/games", {"dates[]": day, "per_page": 100}).get("data", []):
        home = (g.get("home_team") or {}).get("abbreviation")
        away = (g.get("visitor_team") or {}).get("abbreviation")
        if home and away:
            out.append((home.upper(), away.upper()))
    return out


def _roster():
    """{player_name: team_abbr} from the daily roster refresh."""
    conn = get_connection()
    rows = conn.execute("SELECT player_name, team FROM player_teams "
                        "WHERE team IS NOT NULL").fetchall()
    conn.close()
    return {r["player_name"]: str(r["team"]).upper() for r in rows}


def _out_players():
    """Names currently listed OUT on the injury report."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT player_name, status FROM injuries").fetchall()
    except Exception:
        rows = []
    conn.close()
    return {r["player_name"] for r in rows
            if "out" in str(r["status"] or "").lower()}


def virtual_rows(day, games, roster, out_names, logs):
    """Placeholder box-score rows for games on `day` (see the note above)."""
    ids = logs.groupby("player_name")["player_id"].last().to_dict()
    cut = pd.to_datetime(day)
    season = str(cut.year if cut.month >= 10 else cut.year - 1)
    rows = []
    for home, away in games:
        gid = f"upcoming-{day}-{home}-{away}"
        for team, opp, is_home in ((home, away, 1), (away, home, 0)):
            matchup = f"{team} vs {opp}" if is_home else f"{team} @ {opp}"
            for name, t in roster.items():
                if t != team or name not in ids:
                    continue
                rows.append({
                    "player_id": ids[name], "player_name": name, "game_id": gid,
                    "game_date": day, "season": season, "matchup": matchup,
                    "is_home": is_home, "opponent": opp,
                    "minutes": 0.0 if name in out_names else 1.0,
                    "points": 0.0, "rebounds": 0.0, "assists": 0.0,
                    "threes_made": 0.0, "team": team, "season_i": float(season)})
    return pd.DataFrame(rows)


def build_slate(day=None, games=None, roster=None, out_names=None, models=None,
                logs=None, write=True):
    """Predicted mean of every stat for every available rostered player in the
    day's games. Written to data/fitted_slate.json so the dashboard and the
    paper-bet logger read the same numbers without retraining or recomputing.
    Returns the slate dict, or None if there is no trained model / no games."""
    from database import nba_today
    day = day or nba_today()
    models = models or load()
    if not models:
        return None
    logs = load_logs() if logs is None else logs
    games = _games_with_teams(day) if games is None else games
    if not games:
        return None
    roster = _roster() if roster is None else roster
    out_names = _out_players() if out_names is None else out_names
    virtual = virtual_rows(day, games, roster, out_names, logs)
    if virtual.empty:
        return None
    table = build_features(pd.concat([logs, virtual], ignore_index=True))
    up = table[table["game_id"].astype(str).str.startswith("upcoming-")]
    mu, meta = {}, {}
    for stat in STATS:
        vals = predict_mean(models, up, stat)
        for name, v in zip(up["player_name"], vals):
            mu[f"{name}|{stat}"] = round(float(v), 3)
    for r in up.itertuples():
        meta[r.player_name] = {"team": r.team, "opp": r.opponent,
                               "home": int(r.is_home) if r.is_home == r.is_home else None,
                               "out_cnt": float(r.out_cnt)}
    import datetime as _dt
    import json
    slate = {"date": day, "built_at": _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"), "alpha": {s: models[s]["alpha"] for s in STATS},
        "mu": mu, "meta": meta}
    if write:
        _SLATE_PATH.parent.mkdir(exist_ok=True)
        _SLATE_PATH.write_text(json.dumps(slate))
    return slate


def _read_slate():
    import json
    try:
        mtime = _SLATE_PATH.stat().st_mtime
    except OSError:
        return None
    if _slate_cache["mtime"] != mtime:
        try:
            _slate_cache["data"] = json.loads(_SLATE_PATH.read_text())
        except Exception:
            _slate_cache["data"] = None
        _slate_cache["mtime"] = mtime
    return _slate_cache["data"]


def slate_mean(player_name, stat, day=None):
    """The fitted mean for a player/stat in today's slate, or None (no slate for
    that day, or the player isn't in it)."""
    from database import nba_today
    slate = _read_slate()
    if not slate or slate.get("date") != (day or nba_today()):
        return None
    return slate["mu"].get(f"{player_name}|{stat}")


def slate_p_over(player_name, stat, line, day=None):
    """Fitted P(over `line`) for a player/stat today, or None if unavailable.
    Combo stats are not modelled (they would need the joint distribution)."""
    from models.baseline_model import over_under_probabilities
    mu = slate_mean(player_name, stat, day)
    slate = _read_slate()
    if mu is None or not slate or stat not in slate.get("alpha", {}):
        return None
    std = float(np.sqrt(mu + slate["alpha"][stat] * mu ** 2))
    return over_under_probabilities(mu, float(line), std)["p_over"]


def _main():
    import argparse
    import datetime as _dt
    ap = argparse.ArgumentParser(description="Fitted projection model")
    ap.add_argument("command", choices=["train", "slate"])
    args = ap.parse_args()
    if args.command == "train":
        print("Building features and training on every game so far...")
        table = build_features()
        cutoff = (_dt.date.fromisoformat(table["game_date"].max())
                  + _dt.timedelta(days=1)).isoformat()
        models = fit(table, cutoff)
        save(models, trained_through=table["game_date"].max())
        print(f"Saved {_MODEL_PATH} (trained through {table['game_date'].max()}, "
              f"{len(table)} player-games).")
    else:
        slate = build_slate()
        if not slate:
            print("No slate built (no trained model, no games today, or no roster).")
        else:
            print(f"Slate for {slate['date']}: {len(slate['meta'])} players, "
                  f"{len(slate['mu'])} projections -> {_SLATE_PATH}")


if __name__ == "__main__":
    _main()

