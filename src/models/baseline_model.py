"""
baseline_model.py — Phase 4: the first honest prediction model.

Two jobs:
  1. PROJECT a player's stat for an upcoming game (expected value).
  2. Turn that projection into an OVER/UNDER probability for a given line.

Design choices, explained:

PROJECTION (expected stat)
  A weighted blend of recent form and season form:
      projection = 0.5*avg_last_5 + 0.3*avg_last_10 + 0.2*season_avg
  Recent games get more weight (role/health change over a season) but the
  season average anchors it so a couple of hot or cold games don't dominate.
  This is a transparent baseline — not a black box. Phase 5 improves the inputs
  (opponent defense, rest, role); the probability math below stays the same.

PROBABILITY (over/under)
  Points, rebounds, assists and threes are COUNTS, so the Poisson distribution
  is the natural fit: given an expected rate (our projection), it gives the
  probability of each possible count. P(over line) = P(stat > line).
  For a line like 25.5 there's no push; for a whole-number line we split the
  exact-value probability. Poisson assumes variance == mean, which is only
  roughly true for NBA stats, so we treat these probabilities as well-reasoned
  estimates, not certainties — and Phase 8's backtest checks their calibration.

BASELINE TO BEAT
  predict_baseline_naive() just uses the season average as the projection.
  Every real model must be compared against this; if we can't beat the dumb
  guess, we don't have an edge. The backtest (Phase 8) does that comparison.
"""

import sys
from pathlib import Path

from scipy.stats import poisson, nbinom

sys.path.append(str(Path(__file__).resolve().parents[1]))
from features.features import pregame_features, load_player_history  # noqa: E402

# Minimum prior games before we trust a projection enough to report confidence.
MIN_GAMES_FOR_CONFIDENCE = 10


def project_stat(feats: dict) -> float:
    """Blend recent and season form into an expected stat. Returns None if we
    have no usable history."""
    if feats.get("games_played_before", 0) == 0:
        return None
    a5 = feats.get("avg_last_5")
    a10 = feats.get("avg_last_10")
    season = feats.get("season_avg")

    # Fall back gracefully when a window is missing (few games played).
    parts, weights = [], []
    if a5 is not None:
        parts.append(a5); weights.append(0.5)
    if a10 is not None:
        parts.append(a10); weights.append(0.3)
    if season is not None:
        parts.append(season); weights.append(0.2)
    if not parts:
        return None
    total_w = sum(weights)
    return sum(p * w for p, w in zip(parts, weights)) / total_w


def over_under_probabilities(projection: float, line: float,
                             std: float = None) -> dict:
    """P(over) and P(under) for a count stat, honest about its UNCERTAINTY.

    projection is the expected count. line is the sportsbook line. `std` is the
    player's observed game-to-game standard deviation for this stat, if known.

    WHY THIS CHANGED (the overconfidence fix):
      Poisson assumes variance == mean. Real NBA stats are OVERDISPERSED — a
      25-point scorer swings far more than Poisson expects (hot/cold nights,
      blowouts, foul trouble). Using Poisson made the distribution too narrow,
      so 90%+ and 10%- calls looked far more certain than they really were
      (the backtest showed "95%" calls hitting only ~80%).

      When we know the player's real spread (std), we use the NEGATIVE BINOMIAL,
      the overdispersed cousin of Poisson: same mean, but a fatter, realistic
      spread. We only do this when variance actually exceeds the mean (true for
      scoring); otherwise, or when std is unknown, we fall back to Poisson.
    """
    if projection is None or projection < 0:
        return {"p_over": None, "p_under": None, "push": None}

    mean = max(projection, 1e-6)

    # Choose the distribution: Negative Binomial when we have a real, wider
    # spread; Poisson otherwise.
    use_nb = False
    if std is not None and std > 0:
        var = std ** 2
        if var > mean * 1.05:  # genuinely overdispersed (5% margin to avoid noise)
            # NB parameterization: var = mean + mean^2 / r  ->  solve for r.
            r = mean ** 2 / (var - mean)
            p = r / (r + mean)          # scipy nbinom uses (n=r, p)
            use_nb = True

    def sf_at(x_inclusive_lower):
        """P(X >= x_inclusive_lower)."""
        if use_nb:
            return float(nbinom.sf(x_inclusive_lower - 1, r, p))
        return float(1.0 - poisson.cdf(x_inclusive_lower - 1, mean))

    def pmf_at(x):
        if use_nb:
            return float(nbinom.pmf(x, r, p))
        return float(poisson.pmf(x, mean))

    # Half-point line (e.g. 25.5) can't push. P(over) = P(X >= floor(line)+1).
    if line != int(line):
        threshold = int(line) + 1
        p_over = sf_at(threshold)
        return {"p_over": p_over, "p_under": 1.0 - p_over, "push": 0.0}

    # Whole-number line: a push is possible (stat exactly == line).
    line_int = int(line)
    p_push = pmf_at(line_int)
    p_over = sf_at(line_int + 1)                 # P(X > line)
    p_under = 1.0 - p_over - p_push              # P(X < line)
    return {"p_over": p_over, "p_under": max(p_under, 0.0), "push": p_push}


def confidence_label(feats: dict) -> str:
    """Plain-English confidence driven by sample size and volatility."""
    n = feats.get("games_played_before", 0)
    if n == 0:
        return "none (no prior games)"
    if n < MIN_GAMES_FOR_CONFIDENCE:
        return f"low (only {n} prior games)"
    std = feats.get("std_last_10")
    if std is not None and feats.get("avg_last_10"):
        # High variability relative to the average = less confident.
        cov = std / max(feats["avg_last_10"], 1e-6)
        if cov > 0.6:
            return f"moderate ({n} games, but volatile recent form)"
    return f"solid ({n} prior games)"


def analyze_prop(player_id: int, prop_type: str, line: float,
                 cutoff_date: str, history=None) -> dict:
    """Full Phase-4 analysis for one prop: projection, probabilities, confidence.

    Returns a dict with everything a dashboard card needs (odds/EV come in
    Phase 6). cutoff_date is the game date; only prior games are used.
    """
    if history is None:
        history = load_player_history(player_id)
    feats = pregame_features(player_id, prop_type, cutoff_date, history)
    projection = project_stat(feats)
    probs = over_under_probabilities(projection, line, feats.get("std_recent"))

    return {
        "player_id": player_id,
        "prop_type": prop_type,
        "line": line,
        "cutoff_date": cutoff_date,
        "projection": round(projection, 2) if projection is not None else None,
        "p_over": round(probs["p_over"], 4) if probs["p_over"] is not None else None,
        "p_under": round(probs["p_under"], 4) if probs["p_under"] is not None else None,
        "push": round(probs["push"], 4) if probs["push"] is not None else None,
        "games_played_before": feats.get("games_played_before", 0),
        "avg_last_5": feats.get("avg_last_5"),
        "avg_last_10": feats.get("avg_last_10"),
        "season_avg": feats.get("season_avg"),
        "confidence": confidence_label(feats),
    }


def project_stat_enhanced(feats: dict, prop_type: str, opponent_abbr: str = None,
                          defense_factor_map: dict = None,
                          use_defense=True, use_rest=True, use_home=True) -> dict:
    """Phase 5: start from the Phase 4 blend, then apply TRANSPARENT nudges.

    Each adjustment is small and explained, and can be toggled off so the
    backtest can measure whether it actually helps. Returns the final number
    plus a breakdown of every adjustment, so a dashboard card can show the
    reasoning ("why this number moved").
    """
    base = project_stat(feats)
    if base is None:
        return {"projection": None, "base": None, "adjustments": []}

    projection = base
    adjustments = []

    # 1) Opponent defense: multiply by how soft/tough the opponent is.
    if use_defense and opponent_abbr and defense_factor_map:
        factor = defense_factor_map.get(opponent_abbr)
        if factor:
            # Dampen it — don't let defense swing the projection too hard.
            damped = 1.0 + (factor - 1.0) * 0.5
            before = projection
            projection *= damped
            adjustments.append(
                f"opponent {opponent_abbr} defense x{damped:.3f} "
                f"({before:.1f} -> {projection:.1f})")

    # 2) Rest: back-to-back (0 days) slight down; well-rested slight up.
    if use_rest and feats.get("rest_days") is not None:
        rd = feats["rest_days"]
        if rd <= 1:
            before = projection; projection *= 0.97
            adjustments.append(f"back-to-back/short rest x0.97 ({before:.1f} -> {projection:.1f})")
        elif rd >= 3:
            before = projection; projection *= 1.02
            adjustments.append(f"well rested ({rd}d) x1.02 ({before:.1f} -> {projection:.1f})")

    # 3) Home/away: lean the projection toward the relevant split (gently).
    #    We don't know the upcoming game's venue here unless the caller tells
    #    us via feats['upcoming_is_home']; if present, blend 20% toward it.
    if use_home and feats.get("upcoming_is_home") is not None:
        split = feats.get("home_avg") if feats["upcoming_is_home"] else feats.get("away_avg")
        if split is not None:
            before = projection
            projection = 0.8 * projection + 0.2 * split
            venue = "home" if feats["upcoming_is_home"] else "away"
            adjustments.append(f"{venue} split blend ({before:.1f} -> {projection:.1f})")

    return {"projection": round(projection, 2), "base": round(base, 2),
            "adjustments": adjustments}


def analyze_prop_enhanced(player_id: int, prop_type: str, line: float,
                          cutoff_date: str, opponent_abbr: str = None,
                          defense_factor_map: dict = None,
                          upcoming_is_home: bool = None, history=None) -> dict:
    """Phase 5 analysis: enhanced projection + Poisson probs + reasoning."""
    if history is None:
        history = load_player_history(player_id)
    feats = pregame_features(player_id, prop_type, cutoff_date, history)
    if upcoming_is_home is not None:
        feats["upcoming_is_home"] = upcoming_is_home

    proj = project_stat_enhanced(feats, prop_type, opponent_abbr,
                                 defense_factor_map)
    probs = over_under_probabilities(proj["projection"], line, feats.get("std_recent"))

    return {
        "player_id": player_id,
        "prop_type": prop_type,
        "line": line,
        "cutoff_date": cutoff_date,
        "opponent": opponent_abbr,
        "projection": proj["projection"],
        "base_projection": proj["base"],
        "adjustments": proj["adjustments"],
        "p_over": round(probs["p_over"], 4) if probs["p_over"] is not None else None,
        "p_under": round(probs["p_under"], 4) if probs["p_under"] is not None else None,
        "push": round(probs["push"], 4) if probs["push"] is not None else None,
        "games_played_before": feats.get("games_played_before", 0),
        "rest_days": feats.get("rest_days"),
        "confidence": confidence_label(feats),
    }


def predict_baseline_naive(player_id: int, prop_type: str, line: float,
                           cutoff_date: str, history=None) -> dict:
    """The dumb baseline: projection = season average only. For comparison."""
    if history is None:
        history = load_player_history(player_id)
    feats = pregame_features(player_id, prop_type, cutoff_date, history)
    projection = feats.get("season_avg")
    probs = over_under_probabilities(projection, line, feats.get("std_recent"))
    return {
        "projection": round(projection, 2) if projection is not None else None,
        "p_over": round(probs["p_over"], 4) if probs["p_over"] is not None else None,
    }


if __name__ == "__main__":
    # Demo against the most-played player in the DB.
    conn_hist = None
    from database import get_connection
    row = get_connection().execute(
        "SELECT player_id, player_name, COUNT(*) c FROM player_game_logs "
        "GROUP BY player_id ORDER BY c DESC LIMIT 1"
    ).fetchone()
    if not row:
        print("No data yet — run the collectors first.")
        sys.exit(0)

    pid, name = row["player_id"], row["player_name"]
    hist = load_player_history(pid)
    # Use the most recent game date as the "upcoming" game to analyze.
    cutoff = hist.iloc[-1]["game_date"]
    line = 25.5
    print(f"Demo: {name} (id={pid}) — points over/under {line} as of {cutoff}\n")
    result = analyze_prop(pid, "points", line, cutoff, hist)
    for k, v in result.items():
        print(f"  {k}: {v}")
    base = predict_baseline_naive(pid, "points", line, cutoff, hist)
    print(f"\n  [baseline] season-avg projection: {base['projection']}, "
          f"p_over: {base['p_over']}")
    print("\nActual result that game (for sanity, NOT used in prediction):")
    actual = hist.iloc[-1]
    print(f"  points={actual['points']} rebounds={actual['rebounds']} "
          f"assists={actual['assists']}")
