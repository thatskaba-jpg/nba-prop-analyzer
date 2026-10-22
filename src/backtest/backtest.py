"""
backtest.py — Phase 8: test a model configuration HONESTLY.

This is the engine behind the website's "Run Backtest" button. You hand it a
ModelConfig (the factors + weights you picked), a date range, and a prop line
rule, and it replays history one game at a time, predicting each game using ONLY
information available before that game, then scores the predictions against what
actually happened.

WHAT IT MEASURES (and why each matters)
  - Accuracy: how often the side we leaned (over/under) was correct. Easy to
    read, but not the whole story — a model can be "accurate" and still lose
    money, so we never stop here.
  - Brier score: average squared error of the probability. Lower is better
    (0 = perfect, 0.25 = a coin flip). Rewards being both right AND honest about
    confidence.
  - Log loss: punishes confident wrong calls harder than Brier. Lower is better.
  - Calibration: of the props where we said "~70% over", did ~70% actually go
    over? A well-calibrated model's stated probabilities mean what they say.
  - ROI / profit: if we'd bet a flat 1 unit on our leaned side at standard
    -110 odds every time, what happens to the bankroll? This is the bottom line.
  - vs. baseline: the SAME metrics for the dumb "season average" model, so you
    can see whether the configuration actually earns its complexity.

HONESTY RULES BAKED IN
  - No leakage: every prediction uses pregame_features (games strictly before
    the game date). The actual result is only read AFTER predicting, to score.
  - A real line: we don't have historical sportsbook lines, so by default we set
    the line at the player's season average to date (a fair, leak-free proxy)
    and test whether the model beats a coin flip at calling over/under on it.
    You can override with a fixed line or a custom line rule. This limitation is
    stated plainly — real closing lines come in Phase 6/9 and would replace it.
  - -110 odds (bet 1.1 to win 1.0) is the standard vig; ROI reflects that house
    edge, so beating break-even (~52.4%) is the real bar, not 50%.
"""

import sys
import math
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402
from features.features import load_player_history, _stat_series, COMBO_PARTS  # noqa: E402
from features.opponent_defense import defense_factors  # noqa: E402
from models.prop_model_config import analyze, ModelConfig  # noqa: E402
from models.baseline_model import predict_baseline_naive  # noqa: E402

# Standard -110 odds: risk 1.1 units to win 1.0. Break-even win rate ~52.38%.
WIN_PROFIT = 1.0
LOSS_COST = 1.10
BREAK_EVEN = LOSS_COST / (LOSS_COST + WIN_PROFIT)  # ~0.5238


def _eligible_player_games(prop_type: str, start_date: str, end_date: str,
                           min_prior_games: int, max_games: int):
    """Yield (player_id, player_name, game_date, actual_stat, is_home, opponent)
    for games in the window, for players with enough prior history.

    We pick players with the most games (stars/regulars) first, since props are
    only offered on players who play real minutes — that keeps the test
    realistic rather than dominated by deep-bench noise.
    """
    conn = get_connection()
    # Players ranked by how many games they have, to focus on real rotation players.
    players = conn.execute(
        "SELECT player_id, player_name, COUNT(*) c FROM player_game_logs "
        "GROUP BY player_id HAVING c >= ? ORDER BY c DESC",
        (min_prior_games + 10,),
    ).fetchall()
    conn.close()

    stat_expr = {
        "points": "points", "rebounds": "rebounds", "assists": "assists",
        "threes_made": "threes_made", "steals": "steals", "blocks": "blocks",
        "turnovers": "turnovers",
    }.get(prop_type)

    produced = 0
    for p in players:
        if produced >= max_games:
            break
        hist = load_player_history(p["player_id"])
        if hist.empty:
            continue
        window = hist[(hist["game_date"] >= start_date) &
                      (hist["game_date"] <= end_date)]
        for _, g in window.iterrows():
            if produced >= max_games:
                break
            # Need enough prior games before this one.
            prior_count = (hist["game_date"] < g["game_date"]).sum()
            if prior_count < min_prior_games:
                continue
            if prop_type in COMBO_PARTS:
                actual = sum(g[c] for c in COMBO_PARTS[prop_type])
            else:
                actual = g[stat_expr]
            yield (p["player_id"], p["player_name"], g["game_date"],
                   float(actual), g.get("is_home"), g.get("opponent"), hist)
            produced += 1


def _score_prediction(p_over, line, actual):
    """Given P(over), the line, and the actual stat, return (picked_side,
    won, prob_of_pick). We bet whichever side the model favored."""
    if p_over is None:
        return None
    if p_over >= 0.5:
        picked, prob = "over", p_over
        won = actual > line
    else:
        picked, prob = "under", 1.0 - p_over
        won = actual < line
    # A push (actual == line) is a no-action tie; treat as neither win nor loss.
    push = (actual == line)
    return picked, (None if push else bool(won)), prob, push


def _metrics(records):
    """Compute accuracy, Brier, log loss, ROI, calibration bins from a list of
    dicts each having: won (bool/None), prob (P of the picked side), hit_over
    (bool: did it actually go over), p_over."""
    graded = [r for r in records if r["won"] is not None]  # drop pushes
    n = len(graded)
    if n == 0:
        return None

    correct = sum(1 for r in graded if r["won"])
    accuracy = correct / n

    # Brier / log loss use P(over) vs. the over outcome (1 if went over).
    brier_terms, logloss_terms = [], []
    for r in graded:
        y = 1.0 if r["hit_over"] else 0.0
        p = min(max(r["p_over"], 1e-9), 1 - 1e-9)
        brier_terms.append((p - y) ** 2)
        logloss_terms.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    brier = sum(brier_terms) / n
    logloss = sum(logloss_terms) / n

    # Flat-stake ROI at -110 on the picked side.
    profit = sum(WIN_PROFIT if r["won"] else -LOSS_COST for r in graded)
    staked = n * LOSS_COST
    roi = profit / staked if staked else 0.0

    # Maximum drawdown: the largest peak-to-trough drop in the running bankroll,
    # walking bets in the order they were placed. Tells you the worst losing
    # stretch you'd have had to sit through — a key honesty/risk metric.
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    equity = []
    for r in graded:
        running += WIN_PROFIT if r["won"] else -LOSS_COST
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        equity.append(round(running, 2))

    # Calibration: bucket by stated P(pick) in 10% bands, compare to hit rate.
    bins = {}
    for r in graded:
        b = min(int(r["prob"] * 10), 9)
        bins.setdefault(b, []).append(1 if r["won"] else 0)
    calibration = []
    for b in sorted(bins):
        hits = bins[b]
        calibration.append({
            "band": f"{b*10}-{b*10+10}%",
            "n": len(hits),
            "predicted": round((b * 10 + 5) / 100, 2),
            "actual": round(sum(hits) / len(hits), 3),
        })

    return {
        "n_bets": n, "accuracy": round(accuracy, 4),
        "brier": round(brier, 4), "log_loss": round(logloss, 4),
        "profit_units": round(profit, 2), "roi": round(roi, 4),
        "max_drawdown": round(max_dd, 2),
        "equity_curve": equity,
        "break_even_rate": round(BREAK_EVEN, 4),
        "beat_break_even": accuracy > BREAK_EVEN,
        "calibration": calibration,
    }


def run_backtest(config: ModelConfig, prop_type: str = "points",
                 start_date: str = "2022-10-01", end_date: str = "2024-07-01",
                 line_mode: str = "season_avg", fixed_line: float = None,
                 min_prior_games: int = 20, max_games: int = 800,
                 use_defense: bool = True) -> dict:
    """Replay history and score `config` vs. the naive baseline.

    line_mode:
      'season_avg' (default) -> line = player's season-to-date average (leak-free
                                proxy, since we lack real historical lines).
      'fixed'                -> use fixed_line for every prop.
    Returns a dict with both models' metrics and the config summary.
    """
    # Precompute one defense map at the window's start (cheap, leak-safe enough
    # for a backtest: it only uses games before start_date).
    dmap = defense_factors(start_date) if use_defense else {}

    model_records, base_records = [], []
    evaluated = 0

    for (pid, pname, gdate, actual, is_home, opp, hist) in _eligible_player_games(
            prop_type, start_date, end_date, min_prior_games, max_games):

        prior = hist[hist["game_date"] < gdate]
        # Determine the line for this prop.
        if line_mode == "fixed" and fixed_line is not None:
            line = fixed_line
        else:
            s = _stat_series(prior, prop_type)
            if len(s) == 0:
                continue
            # Half-point line at the season avg so there are no pushes.
            line = round(float(s.mean())) + 0.5

        # Season of this game (for the per-season breakdown).
        season = prior.iloc[-1]["season"] if len(prior) else "?"
        if "season" in hist.columns:
            srow = hist[hist["game_date"] == gdate]
            if len(srow):
                season = srow.iloc[0]["season"]

        ctx = {"opponent_abbr": opp, "defense_factor_map": dmap,
               "upcoming_is_home": (bool(is_home) if is_home is not None else None)}

        # Our configured model.
        res = analyze(config, pid, prop_type, line, gdate, hist, ctx)
        scored = _score_prediction(res["p_over"], line, actual)
        if scored:
            picked, won, prob, push = scored
            model_records.append({
                "won": won, "prob": prob, "p_over": res["p_over"],
                "hit_over": actual > line, "season": season,
            })

        # The dumb baseline (season-average projection, same line).
        b = predict_baseline_naive(pid, prop_type, line, gdate, hist)
        bscored = _score_prediction(b["p_over"], line, actual)
        if bscored:
            _, bwon, bprob, _ = bscored
            base_records.append({
                "won": bwon, "prob": bprob, "p_over": b["p_over"],
                "hit_over": actual > line, "season": season,
            })

        evaluated += 1

    # Per-season breakdown (brief: "performance across seasons").
    by_season = {}
    seasons = sorted({r["season"] for r in model_records})
    for s in seasons:
        recs = [r for r in model_records if r["season"] == s]
        sm = _metrics(recs)
        if sm:
            by_season[s] = {"n_bets": sm["n_bets"], "accuracy": sm["accuracy"],
                            "roi": sm["roi"], "max_drawdown": sm["max_drawdown"]}

    return {
        "config": config.name,
        "mode": config.mode,
        "weights": {k: round(v, 3) for k, v in config.normalized_weights().items()},
        "prop_type": prop_type,
        "window": f"{start_date} .. {end_date}",
        "line_mode": line_mode if line_mode != "fixed" else f"fixed {fixed_line}",
        "games_evaluated": evaluated,
        "by_season": by_season,
        "model": _metrics(model_records),
        "baseline": _metrics(base_records),
    }


def print_report(result: dict):
    """Human-readable backtest report, honest about what it means."""
    print("=" * 66)
    print(f"BACKTEST: {result['config']}  (mode={result['mode']})")
    print(f"Prop: {result['prop_type']}   Window: {result['window']}")
    print(f"Line rule: {result['line_mode']}   Games evaluated: {result['games_evaluated']}")
    print(f"Weights: " + ", ".join(f"{k} {v:.0%}" for k, v in result["weights"].items()))
    print("=" * 66)

    m, b = result["model"], result["baseline"]
    if not m:
        print("No gradable bets — widen the window or lower min_prior_games.")
        return

    def line(label, mv, bv, better="high"):
        arrow = ""
        if mv is not None and bv is not None:
            if (mv > bv and better == "high") or (mv < bv and better == "low"):
                arrow = "  <- model better"
            elif mv == bv:
                arrow = "  (tie)"
            else:
                arrow = "  <- baseline better"
        print(f"  {label:<18} model={mv!s:<10} baseline={bv!s:<10}{arrow}")

    print("\nHOW OFTEN IT WAS RIGHT (higher is better):")
    line("Accuracy", m["accuracy"], b["accuracy"], "high")
    print(f"  Break-even at -110 odds is {m['break_even_rate']:.4f} "
          f"({m['break_even_rate']*100:.1f}%). Beating 50% is NOT enough.")
    print(f"  Model beats break-even? {'YES' if m['beat_break_even'] else 'NO'}")

    print("\nPROBABILITY QUALITY (lower is better):")
    line("Brier score", m["brier"], b["brier"], "low")
    line("Log loss", m["log_loss"], b["log_loss"], "low")

    print("\nMONEY (flat 1u bets at -110):")
    line("Profit (units)", m["profit_units"], b["profit_units"], "high")
    line("ROI", m["roi"], b["roi"], "high")
    line("Max drawdown", m["max_drawdown"], b["max_drawdown"], "low")

    if result.get("by_season"):
        print("\nBY SEASON (model):")
        print(f"  {'season':<10}{'bets':<7}{'accuracy':<10}{'roi':<9}{'max dd':<8}")
        for s, sv in result["by_season"].items():
            print(f"  {s:<10}{sv['n_bets']:<7}{sv['accuracy']:<10}"
                  f"{sv['roi']:<9}{sv['max_drawdown']:<8}")

    print("\nCALIBRATION (does '70%' really win ~70%?):")
    print(f"  {'band':<10}{'bets':<7}{'predicted':<11}{'actual':<8}")
    for c in m["calibration"]:
        print(f"  {c['band']:<10}{c['n']:<7}{c['predicted']:<11}{c['actual']:<8}")

    print("\nHONEST READ:")
    if m["roi"] <= 0:
        print("  This configuration does NOT make money at -110 in this window.")
        print("  That's the expected, honest result for a simple model vs. an")
        print("  efficient-ish line proxy — the point of the backtest is to SHOW")
        print("  that, not hide it. Try different factors/weights and compare.")
    else:
        print("  Positive ROI in THIS window and line proxy. Treat with caution:")
        print("  the line here is a season-avg proxy, not a real sportsbook line.")
        print("  Real validation needs historical closing lines (Phase 6/9).")
    if m["accuracy"] > b["accuracy"]:
        print("  The configured model beat the dumb season-average baseline on")
        print("  accuracy — the factors added something. Backtest more windows.")
    else:
        print("  The configured model did NOT beat the season-average baseline")
        print("  here. The extra factors aren't helping in this window — exactly")
        print("  the kind of thing this test exists to reveal.")
    print("=" * 66)


if __name__ == "__main__":
    # Default demo: run a preset config on points over a 2022-2024 window.
    from models.prop_model_config import preset_matchup_edge, preset_momentum_tracker

    which = sys.argv[1] if len(sys.argv) > 1 else "matchup"
    cfg = preset_momentum_tracker() if which == "momentum" else preset_matchup_edge()

    print(f"Running backtest for preset '{cfg.name}'... "
          f"(this scans real games; give it a moment)\n")
    result = run_backtest(cfg, prop_type="points",
                          start_date="2022-10-01", end_date="2024-04-14",
                          max_games=600)
    print_report(result)
