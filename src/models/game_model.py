"""
game_model.py — project a game's WINNER and MARGIN for moneyline/spread bets.

This is the game-side cousin of the player prop model. It does NOT try to
out-model the book; it produces an honest, transparent estimate from data we
already pull, then the EV engine compares it to the real line.

INPUTS (all already in the project):
  - Team off/def ratings + pace (balldontlie team_ratings_map): points per 100
    possessions each team scores and allows.
  - Home-court edge: a flat, well-established ~2.5-point bump for the home team.

METHOD (plain):
  1. Estimate each team's expected points = blend of its OWN offense and the
     OPPONENT's defense, scaled by the game's expected pace.
  2. Margin = home expected points - away expected points + home-court edge.
  3. Convert margin -> win probability with a logistic curve calibrated to the
     NBA (a ~12-point favorite wins ~80%; std ~ 12).
  4. Spread cover prob and moneyline win prob both fall out of that margin +
     its uncertainty.

HONESTY: this is a ratings-based estimate, not a simulation. It's a reasonable
baseline the EV engine can test against the market — the same "find a soft line"
use the prop scanner has, applied to sides. Returns None cleanly when ratings
are missing rather than guessing.
"""

import math

HOME_EDGE = 2.5          # points, league-standard home-court advantage
LEAGUE_PACE = 100.0
LEAGUE_RTG = 113.0       # points per 100 possessions, league average
MARGIN_STD = 12.0        # NBA final-margin spread, for win-prob conversion


def _norm_cdf(x):
    """Standard normal CDF via erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def project_game(home_abbr, away_abbr, ratings):
    """Return a dict with expected margin, win probs, and the inputs used.

    ratings: {abbr: {off, def, pace}} from team_ratings_map. Returns None if
    either team's ratings are missing."""
    h = ratings.get(home_abbr)
    a = ratings.get(away_abbr)
    if not h or not a:
        return None
    for t in (h, a):
        if t.get("off") is None or t.get("def") is None:
            return None

    pace = ((h.get("pace") or LEAGUE_PACE) + (a.get("pace") or LEAGUE_PACE)) / 2.0
    poss = pace / 100.0

    # Expected points = average of (my offense) and (opponent defense allows),
    # scaled to the game's pace.
    home_pts = ((h["off"] + a["def"]) / 2.0) * poss
    away_pts = ((a["off"] + h["def"]) / 2.0) * poss
    margin = (home_pts - away_pts) + HOME_EDGE   # + = home favored

    # Win probability from margin via a normal model.
    p_home = _norm_cdf(margin / MARGIN_STD)
    return {
        "home": home_abbr, "away": away_abbr,
        "exp_home_pts": round(home_pts, 1), "exp_away_pts": round(away_pts, 1),
        "exp_margin": round(margin, 1),           # home perspective
        "exp_total": round(home_pts + away_pts, 1),
        "p_home_win": round(p_home, 4),
        "p_away_win": round(1 - p_home, 4),
        "pace": round(pace, 1),
    }


def cover_probability(exp_margin, spread_home):
    """P(home covers its spread). spread_home is the home line (negative =
    favored, e.g. -6.5). Home covers when actual margin > -spread_home."""
    # Need actual_margin + spread_home > 0  ->  actual_margin > -spread_home.
    threshold = -spread_home
    # P(margin > threshold) under Normal(exp_margin, MARGIN_STD).
    z = (exp_margin - threshold) / MARGIN_STD
    p_home_cover = _norm_cdf(z)
    return round(p_home_cover, 4), round(1 - p_home_cover, 4)


if __name__ == "__main__":
    import sys
    sys.path.append(__file__.rsplit("/", 2)[0])
    from collect.goat_data import team_ratings_map
    r = team_ratings_map(2025)
    g = project_game("LAL", "GSW", r)
    print("LAL (home) vs GSW:", g)
    if g:
        print("spread cover (home -6.5):",
              cover_probability(g["exp_margin"], -6.5))
