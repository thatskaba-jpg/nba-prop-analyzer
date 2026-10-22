"""
game_ml_model.py — a REAL trained model for game lines (moneyline + spread).

Unlike the prop model (rules-based) and game_model.py (ratings->logistic), this
is an actual machine-learned model, trained on reconstructed historical games:

  TARGET
    - home_win  (1/0)      -> moneyline win probability   (LogisticRegression)
    - margin    (home-away)-> point margin / spread cover  (Ridge regression)

  FEATURES  (ALL strictly pre-game — no leakage)
    For the home team and the away team, each computed from ONLY their games
    BEFORE the current one:
      - rolling points scored per game (offense form)
      - rolling points allowed per game (defense form)
      - rolling win rate
      - rest days since their last game
    Plus the constant home-court flag (margin intercept captures the edge).

  HONESTY
    - Trained with a TIME-ORDERED split (train on older seasons, test on the
      newest) so the reported accuracy is out-of-sample, not memorized.
    - We reconstruct final scores by summing each team's player points per
      game from player_game_logs (is_home splits the two sides). This is the
      same 7.7k-game history the rest of the project already has.
    - If sklearn or data is missing, train() returns None and the dashboard
      falls back to the ratings-based game_model.py.

USAGE
    python src/models/game_ml_model.py train     # fit + backtest + save
    # then in code:
    from models.game_ml_model import load_model, predict_game
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

_MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "game_ml_model.json"
_ROLL = 10          # games in each team's rolling form window
_MIN_PRIOR = 5      # need at least this many prior games to feature a team


def _games_from_api(seasons) -> pd.DataFrame:
    """Real official games with FINAL SCORES, straight from the balldontlie API
    (the source of truth). One row per finished game. Empty DataFrame on error.
    seasons is a list of start years."""
    try:
        import sys
        sys.path.append(str(Path(__file__).resolve().parents[1]))
        from collect.goat_data import _get_all
    except Exception:
        return pd.DataFrame()
    try:
        rows = _get_all("games", {"seasons[]": list(seasons)})
    except Exception:
        return pd.DataFrame()
    recs = []
    for g in rows:
        if g.get("status") != "Final":
            continue
        hs, vs = g.get("home_team_score"), g.get("visitor_team_score")
        if hs is None or vs is None:
            continue
        ht = (g.get("home_team") or {}).get("abbreviation")
        at = (g.get("visitor_team") or {}).get("abbreviation")
        if not ht or not at:
            continue
        recs.append({
            "game_id": g.get("id"),
            "game_date": str(g.get("date"))[:10],
            "season": str(g.get("season") or ""),
            "home_team": ht, "away_team": at,
            "home_pts": int(hs), "away_pts": int(vs),
            "home_win": 1 if hs > vs else 0,
            "margin": int(hs) - int(vs),
        })
    if not recs:
        return pd.DataFrame()
    games = pd.DataFrame(recs).dropna(subset=["home_team", "away_team"])
    return games.sort_values("game_date").reset_index(drop=True)


def _reconstruct_games(conn=None, seasons=None) -> pd.DataFrame:
    """Games with final scores — API-FIRST (real official scores), falling back
    to reconstructing from local player logs only if the API is unavailable."""
    if seasons is None:
        seasons = list(range(2026, 2019, -1))  # 2020-2026
    api = _games_from_api(seasons)
    if not api.empty and len(api) >= 500:
        return api
    # Fallback: reconstruct from the local DB (offline / API down).
    if conn is None:
        import sys
        sys.path.append(str(Path(__file__).resolve().parents[1]))
        from database import get_connection
        conn = get_connection()
    df = pd.read_sql_query(
        "SELECT game_id, game_date, season, is_home, opponent, points "
        "FROM player_game_logs WHERE game_id IS NOT NULL", conn)
    if df.empty:
        return df
    g = (df.groupby(["game_id", "game_date", "season", "is_home", "opponent"])
           ["points"].sum().reset_index())
    home = g[g["is_home"] == 1][["game_id", "game_date", "season",
                                 "opponent", "points"]].rename(
        columns={"points": "home_pts", "opponent": "away_team"})
    away = g[g["is_home"] == 0][["game_id", "opponent", "points"]].rename(
        columns={"points": "away_pts", "opponent": "home_team"})
    games = home.merge(away, on="game_id")
    games = games.dropna(subset=["home_team", "away_team"])
    games["home_win"] = (games["home_pts"] > games["away_pts"]).astype(int)
    games["margin"] = games["home_pts"] - games["away_pts"]
    games = games.sort_values("game_date").reset_index(drop=True)
    return games


def _team_timeline(games: pd.DataFrame) -> dict:
    """For every team, a chronological list of (date, pts_for, pts_against,
    won) from BOTH home and away games — the raw material for pre-game form."""
    tl: dict = {}
    for _, r in games.iterrows():
        tl.setdefault(r["home_team"], []).append(
            (r["game_date"], r["home_pts"], r["away_pts"],
             1 if r["home_win"] else 0))
        tl.setdefault(r["away_team"], []).append(
            (r["game_date"], r["away_pts"], r["home_pts"],
             0 if r["home_win"] else 1))
    for t in tl:
        tl[t].sort(key=lambda x: x[0])
    return tl


def _form_before(timeline, team, date):
    """Rolling pre-game form for `team` from games strictly before `date`.
    Returns (off, def, winrate, rest, net, scoring_std, last5_off, last5_def)
    — all leak-free. None if too few prior games."""
    rows = [x for x in timeline.get(team, []) if x[0] < date]
    if len(rows) < _MIN_PRIOR:
        return None
    recent = rows[-_ROLL:]
    pf = [x[1] for x in recent]
    pa = [x[2] for x in recent]
    off = float(np.mean(pf))
    dfn = float(np.mean(pa))
    wr = float(np.mean([x[3] for x in recent]))
    net = off - dfn                          # point differential (net rating-ish)
    scoring_std = float(np.std(pf)) if len(pf) >= 2 else 10.0  # consistency
    last5 = recent[-5:]
    l5_off = float(np.mean([x[1] for x in last5]))   # hotter/colder recently
    l5_def = float(np.mean([x[2] for x in last5]))
    try:
        rest = (pd.to_datetime(date) - pd.to_datetime(rows[-1][0])).days
        rest = max(0, min(rest, 10))   # clamp offseason gaps
    except Exception:
        rest = 2
    return off, dfn, wr, rest, net, scoring_std, l5_off, l5_def


def _build_matrix(games: pd.DataFrame):
    """Assemble (X, y_win, y_margin, dates) with leak-free features."""
    tl = _team_timeline(games)
    X, yw, ym, yt, dates = [], [], [], [], []
    for _, r in games.iterrows():
        hf = _form_before(tl, r["home_team"], r["game_date"])
        af = _form_before(tl, r["away_team"], r["game_date"])
        if hf is None or af is None:
            continue
        ho, hd, hw, hr, hnet, hstd, hl5o, hl5d = hf
        ao, ad, aw, ar, anet, astd, al5o, al5d = af
        # Feature vector: each team's form + the differentials that matter.
        X.append([
            ho, hd, hw, hr,
            ao, ad, aw, ar,
            ho - ad,          # home offense vs away defense
            ao - hd,          # away offense vs home defense
            hw - aw,          # win-rate gap
            hr - ar,          # rest advantage
            hnet, anet,       # each team's point differential (net rating)
            hnet - anet,      # NET-rating gap (strongest single signal)
            hstd, astd,       # scoring consistency (streaky vs steady)
            hl5o - hl5d,      # home last-5 net (recent hot/cold)
            al5o - al5d,      # away last-5 net
        ])
        yw.append(int(r["home_win"]))
        ym.append(float(r["margin"]))
        yt.append(float(r["home_pts"] + r["away_pts"]))   # game total
        dates.append(r["game_date"])
    return (np.array(X, dtype=float), np.array(yw), np.array(ym),
            np.array(yt), np.array(dates))


_FEATURES = ["home_off", "home_def", "home_wr", "home_rest",
             "away_off", "away_def", "away_wr", "away_rest",
             "home_off_vs_away_def", "away_off_vs_home_def",
             "wr_gap", "rest_gap",
             "home_net", "away_net", "net_gap",
             "home_scoring_std", "away_scoring_std",
             "home_last5_net", "away_last5_net"]


def train(save: bool = True) -> dict:
    """Fit the win + margin models on a time-ordered split and report honest
    out-of-sample accuracy. Returns the fit dict (also written to disk)."""
    try:
        from sklearn.linear_model import LogisticRegression, Ridge
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import accuracy_score, roc_auc_score, log_loss
    except Exception as e:
        print("sklearn missing:", e)
        return None
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[1]))

    # API-first: real official final scores from balldontlie.
    games = _reconstruct_games(seasons=list(range(2026, 2019, -1)))
    src = "balldontlie API"
    if games.empty or len(games) < 500:
        print("not enough games to train:", len(games))
        return None

    X, yw, ym, yt, dates = _build_matrix(games)
    print(f"training rows (leak-free, >= {_MIN_PRIOR} prior games): {len(X)}")

    # Time-ordered split: newest ~20% is the out-of-sample test set.
    order = np.argsort(dates)
    X, yw, ym, yt, dates = X[order], yw[order], ym[order], yt[order], dates[order]
    cut = int(len(X) * 0.80)
    Xtr, Xte = X[:cut], X[cut:]
    ywtr, ywte = yw[:cut], yw[cut:]
    ymtr, ymte = ym[:cut], ym[cut:]
    yttr, ytte = yt[:cut], yt[cut:]

    scaler = StandardScaler().fit(Xtr)
    Xtr_s, Xte_s = scaler.transform(Xtr), scaler.transform(Xte)

    win = LogisticRegression(max_iter=1000, C=1.0).fit(Xtr_s, ywtr)
    marg = Ridge(alpha=1.0).fit(Xtr_s, ymtr)
    tot = Ridge(alpha=1.0).fit(Xtr_s, yttr)

    # Honest out-of-sample metrics.
    p_te = win.predict_proba(Xte_s)[:, 1]
    acc = accuracy_score(ywte, (p_te >= 0.5).astype(int))
    try:
        auc = roc_auc_score(ywte, p_te)
    except Exception:
        auc = float("nan")
    ll = log_loss(ywte, p_te, labels=[0, 1])
    base = max(ywte.mean(), 1 - ywte.mean())   # always-pick-majority baseline
    m_pred = marg.predict(Xte_s)
    mae = float(np.mean(np.abs(m_pred - ymte)))
    # Spread direction: does predicted margin sign beat always-home?
    spread_acc = float(np.mean((m_pred > 0).astype(int) == ywte))
    t_pred = tot.predict(Xte_s)
    total_mae = float(np.mean(np.abs(t_pred - ytte)))

    print(f"\n=== OUT-OF-SAMPLE (newest {len(Xte)} games) ===")
    print(f"moneyline accuracy : {acc:.3f}   (majority baseline {base:.3f})")
    print(f"moneyline AUC      : {auc:.3f}   (0.5 = coin flip)")
    print(f"log loss           : {ll:.4f}")
    print(f"margin MAE         : {mae:.2f} pts")
    print(f"margin-sign acc    : {spread_acc:.3f}")
    print(f"total MAE          : {total_mae:.2f} pts  (avg total {ytte.mean():.0f})")

    fit = {
        "features": _FEATURES,
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "win_coef": win.coef_[0].tolist(),
        "win_intercept": float(win.intercept_[0]),
        "margin_coef": marg.coef_.tolist(),
        "margin_intercept": float(marg.intercept_),
        "total_coef": tot.coef_.tolist(),
        "total_intercept": float(tot.intercept_),
        "roll": _ROLL,
        "min_prior": _MIN_PRIOR,
        "metrics": {"ml_accuracy": acc, "ml_auc": auc, "log_loss": ll,
                    "margin_mae": mae, "margin_sign_acc": spread_acc,
                    "total_mae": total_mae,
                    "majority_baseline": base, "test_games": int(len(Xte)),
                    "train_games": int(len(Xtr))},
    }
    if save:
        _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_PATH.write_text(json.dumps(fit, indent=1))
        print(f"\nsaved -> {_MODEL_PATH}")
    return fit


_CACHE = None


def load_model() -> dict:
    """Load the saved fit (cached). None if not trained yet."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = json.loads(_MODEL_PATH.read_text())
    except Exception:
        _CACHE = None
    return _CACHE


def _apply_linear(fit, x, coef_key, intercept_key):
    mean = np.array(fit["scaler_mean"]); scale = np.array(fit["scaler_scale"])
    xs = (np.array(x, dtype=float) - mean) / scale
    return float(np.dot(xs, fit[coef_key]) + fit[intercept_key])


def predict_from_features(home_form, away_form) -> dict:
    """home_form/away_form are (off, def, winrate, rest). Returns the trained
    model's home win probability + predicted margin. None if no model."""
    fit = load_model()
    if not fit:
        return None
    ho, hd, hw, hr = home_form[:4]
    ao, ad, aw, ar = away_form[:4]
    hnet, anet = ho - hd, ao - ad
    # Dashboard passes only off/def/winrate/rest; derive the rest sensibly
    # (net = off-def, consistency defaults to league ~12, last-5 net = net).
    hstd = home_form[5] if len(home_form) > 5 else 12.0
    astd = away_form[5] if len(away_form) > 5 else 12.0
    hl5 = (home_form[6] - home_form[7]) if len(home_form) > 7 else hnet
    al5 = (away_form[6] - away_form[7]) if len(away_form) > 7 else anet
    x = [ho, hd, hw, hr, ao, ad, aw, ar,
         ho - ad, ao - hd, hw - aw, hr - ar,
         hnet, anet, hnet - anet,
         hstd, astd, hl5, al5]
    logit = _apply_linear(fit, x, "win_coef", "win_intercept")
    p_home = 1.0 / (1.0 + math.exp(-logit))
    margin = _apply_linear(fit, x, "margin_coef", "margin_intercept")
    result = {"p_home_win": p_home, "p_away_win": 1.0 - p_home,
              "margin": margin, "trained": True}
    if "total_coef" in fit:
        result["total"] = _apply_linear(fit, x, "total_coef", "total_intercept")
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "train":
        train(save=True)
    else:
        print("usage: python src/models/game_ml_model.py train")
