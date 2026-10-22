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


# How many games on the current team we want before we fully trust current-team
# form as the anchor. Below this we blend it toward the overall sample so one or
# two games don't swing the projection.
_SOLID_TEAM_SAMPLE = 10


def project_stat(feats: dict) -> float:
    """Blend recent and season form into an expected stat. Returns None if we
    have no usable history.

    TRADE-AWARE ANCHOR
      The default blend weights recent form heavily (0.5/0.3/0.2 over
      last5/last10/season). That is right for a settled player, but misleading
      for one who just changed teams: his "recent" games were a different role
      on a different team. We use the per-game team context from features to
      pick a sensible anchor:

        * recent games already on his CURRENT team  -> normal recency blend, but
          the season anchor is his CURRENT-TEAM average (not career years on
          other teams), when we have a solid current-team sample.
        * just TRADED (his most recent game was on the old team) -> down-weight
          the stale recent windows and lean on his current-team average, or the
          overall season average if he has no current-team games yet.

      Thin current-team samples are blended toward the overall sample so a
      single new-team game doesn't dominate. Nothing here invents data — it
      only re-weights games the player actually played.
    """
    if feats.get("games_played_before", 0) == 0:
        return None
    a5 = feats.get("avg_last_5")
    a10 = feats.get("avg_last_10")
    season = feats.get("season_avg")
    team_avg = feats.get("current_team_avg")
    n_team = feats.get("games_on_current_team", 0) or 0
    stale_recent = feats.get("recent_is_stale_team", False)

    # The "anchor" is the stable, slow-moving level the recency windows pull
    # away from. Prefer the current-team average when it's a meaningful sample;
    # blend it toward the overall season average when the sample is thin.
    anchor = season
    if team_avg is not None and n_team >= 1:
        if n_team >= _SOLID_TEAM_SAMPLE:
            anchor = team_avg
        elif season is not None:
            # Linearly trust the team average more as the sample grows.
            w_team = n_team / _SOLID_TEAM_SAMPLE
            anchor = w_team * team_avg + (1 - w_team) * season
        else:
            anchor = team_avg

    # Just traded: the recent windows describe the OLD team, so they're stale.
    # Lean almost entirely on the anchor (current-team / season level) and give
    # the recent windows only a small voice.
    if stale_recent:
        parts, weights = [], []
        if anchor is not None:
            parts.append(anchor); weights.append(0.8)
        if a10 is not None:
            parts.append(a10); weights.append(0.15)
        if a5 is not None:
            parts.append(a5); weights.append(0.05)
        if not parts:
            return None
        total_w = sum(weights)
        return sum(p * w for p, w in zip(parts, weights)) / total_w

    # Normal case: recency-weighted blend, anchored on the current-team level.
    parts, weights = [], []
    if a5 is not None:
        parts.append(a5); weights.append(0.5)
    if a10 is not None:
        parts.append(a10); weights.append(0.3)
    if anchor is not None:
        parts.append(anchor); weights.append(0.2)
    if not parts:
        return None
    total_w = sum(weights)
    return sum(p * w for p, w in zip(parts, weights)) / total_w


# Rough league-average pace (possessions per 48). Used to normalize the pace
# multiplier so an average-tempo game leaves the rate projection unchanged.
_LEAGUE_PACE = 100.0


def project_stat_rate(feats: dict, pace: float = None,
                      minutes_override: float = None) -> float:
    """Deeper projection: per-minute rate x projected minutes x pace.

    WHY THIS EXISTS
      The blend in project_stat() works on game TOTALS, so it can't tell a
      change in ROLE (minutes) apart from a change in PRODUCTION (per-minute
      output). This path separates them:

        projection = per_min_rate  x  projected_minutes  x  (pace / league_pace)

      * per_min_rate      how much of the stat the player produces per minute
                          (recent-weighted; from features).
      * projected_minutes how many minutes we expect tonight (recent minutes,
                          or an override when a lineup/injury changes the role).
      * pace              game possessions/48; more pace = more opportunities.
                          Normalized by league pace so an average game is x1.

    This is how minutes and tempo get VALUED directly. Falls back to None when
    we lack the rate or minutes (caller then uses the totals blend instead), so
    it never fabricates a projection from missing data.
    """
    rate = feats.get("per_min_rate")
    minutes = minutes_override if minutes_override is not None else \
        feats.get("proj_minutes")
    if rate is None or minutes is None or minutes <= 0:
        return None
    proj = rate * minutes
    if pace and pace > 0:
        proj *= pace / _LEAGUE_PACE
    return proj


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
                 cutoff_date: str, history=None, current_team: str = None) -> dict:
    """Full Phase-4 analysis for one prop: projection, probabilities, confidence.

    Returns a dict with everything a dashboard card needs (odds/EV come in
    Phase 6). cutoff_date is the game date; only prior games are used.

    current_team (optional) is the player's live-roster team, so the projection
    can be trade-aware (lean on current-team form for a just-moved player).
    """
    if history is None:
        history = load_player_history(player_id)
    feats = pregame_features(player_id, prop_type, cutoff_date, history,
                             current_team=current_team)
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
        "current_team": feats.get("current_team"),
        "current_team_avg": feats.get("current_team_avg"),
        "games_on_current_team": feats.get("games_on_current_team", 0),
        "recent_is_stale_team": feats.get("recent_is_stale_team", False),
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
