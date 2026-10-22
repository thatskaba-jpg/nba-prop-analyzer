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


# Switchable GAME-MODEL profiles. Each dict overrides the model's knobs, so the
# SAME team ratings produce DIFFERENT ML/spread picks under different profiles.
#   home_edge  : home-court points added to the margin
#   margin_std : spread of outcomes (lower = more confident win probs)
#   net_scale  : how hard a rating gap is pushed into the margin (1.0 = default;
#                >1 trusts rating gaps more, <1 shrinks toward a coin flip)
#   pace_gain  : sensitivity to pace vs league-average (1.0 = default)
GAME_PROFILES = {
    "Balanced": {"home_edge": 2.5, "margin_std": 12.0, "net_scale": 1.0,
                 "pace_gain": 1.0},
    "Strong home court": {"home_edge": 4.0, "margin_std": 12.0,
                          "net_scale": 1.0, "pace_gain": 1.0},
    "Trust the ratings (bold)": {"home_edge": 2.5, "margin_std": 10.0,
                                 "net_scale": 1.35, "pace_gain": 1.0},
    "Cautious (toward coin flip)": {"home_edge": 2.0, "margin_std": 14.0,
                                    "net_scale": 0.7, "pace_gain": 1.0},
    "Pace-sensitive": {"home_edge": 2.5, "margin_std": 12.0, "net_scale": 1.0,
                       "pace_gain": 1.6},
    "No home edge (neutral court)": {"home_edge": 0.0, "margin_std": 12.0,
                                     "net_scale": 1.0, "pace_gain": 1.0},
    "Road warrior (fade home)": {"home_edge": 1.0, "margin_std": 12.0,
                                 "net_scale": 1.0, "pace_gain": 1.0},
    "Underdog hunter": {"home_edge": 2.5, "margin_std": 16.0, "net_scale": 0.55,
                        "pace_gain": 1.0},
    "Favorite slammer (chalk)": {"home_edge": 3.5, "margin_std": 9.0,
                                 "net_scale": 1.5, "pace_gain": 1.0},
    "Slow grind (low pace)": {"home_edge": 2.5, "margin_std": 11.0,
                              "net_scale": 1.0, "pace_gain": 0.5},
    "Max confidence (sharp)": {"home_edge": 3.0, "margin_std": 8.5,
                               "net_scale": 1.5, "pace_gain": 1.2},
}
DEFAULT_PROFILE = "Balanced"

# Plain-English description per profile — shown in the UI like the prop presets.
GAME_PROFILE_DESC = {
    "Balanced": "The solid all-purpose default — standard home court and "
                "confidence.",
    "Strong home court": "Gives the home team extra credit — good when home "
                         "edges are running hot.",
    "Trust the ratings (bold)": "Leans harder on the better-rated team; more "
                                "decisive picks.",
    "Cautious (toward coin flip)": "Pulls close games toward 50/50 — fewer "
                                   "strong calls, safer.",
    "Pace-sensitive": "Reacts more to fast/slow matchups — favors up-tempo "
                      "spots.",
    "No home edge (neutral court)": "Ignores home court entirely — pure "
                                    "team-vs-team on a neutral floor.",
    "Road warrior (fade home)": "Barely credits home court — leans toward road "
                                "teams the market overtaxes at home.",
    "Underdog hunter": "Shrinks favorites and widens outcomes — surfaces more "
                       "underdog value.",
    "Favorite slammer (chalk)": "Backs favorites hard with a big home edge — "
                                "chalk-heavy picks.",
    "Slow grind (low pace)": "Dampens pace — built for low-scoring, grind-it-out "
                             "games.",
    "Max confidence (sharp)": "Tightest, most decisive model — strong rating "
                              "trust, smallest spread of outcomes.",
}


def _norm_cdf(x):
    """Standard normal CDF via erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _star_tax_points(out_list):
    """Total point-value a team loses from its OUT/DOUBTFUL players.

    Each absent player's spread value ≈ ppg × 0.20, clamped to 1-7 points (a
    ~25-ppg star ≈ 5, a bench player ≈ 1), which matches the market's rule of
    thumb that a top star is worth 4-7 points to the spread. DOUBTFUL counts
    half (the player might suit up). Returns (points, [reasons])."""
    if not out_list:
        return 0.0, []
    total, reasons = 0.0, []
    for entry in out_list:
        nm = entry[0]
        status = (entry[1] if len(entry) > 1 else "out") or ""
        ppg = entry[2] if len(entry) > 2 else 0.0
        s = status.lower()
        if "out" in s:
            weight = 1.0
        elif "doubtful" in s:
            weight = 0.5
        elif "question" in s:
            weight = 0.25   # ~50/50 to play — small partial dock
        else:
            continue
        val = max(1.0, min(7.0, (ppg or 0.0) * 0.20)) * weight
        total += val
        _lbl = ("OUT" if weight >= 1.0
                else "DOUBTFUL" if weight >= 0.5 else "QUESTIONABLE")
        reasons.append(f"{nm} {_lbl} (-{val:.1f})")
    return round(total, 1), reasons


def project_game(home_abbr, away_abbr, ratings, profile=None, injuries=None):
    """Return a dict with expected margin, win probs, and the inputs used.

    ratings: {abbr: {off, def, pace}} from team_ratings_map. Returns None if
    either team's ratings are missing. `profile` is an optional dict of knobs
    (see GAME_PROFILES). `injuries` is an optional {team_abbr: [(name, status,
    ppg), ...]} map — present teams get a 'Star Tax' margin deduction for their
    OUT/DOUBTFUL players, like the books do when a star is ruled out."""
    h = ratings.get(home_abbr)
    a = ratings.get(away_abbr)
    if not h or not a:
        return None
    for t in (h, a):
        if t.get("off") is None or t.get("def") is None:
            return None

    prof = dict(GAME_PROFILES[DEFAULT_PROFILE])
    if isinstance(profile, dict):
        prof.update(profile)
    home_edge = prof["home_edge"]
    margin_std = prof["margin_std"]
    net_scale = prof["net_scale"]
    pace_gain = prof["pace_gain"]

    # Pace: amplify/dampen the game's distance from league average by pace_gain.
    raw_pace = ((h.get("pace") or LEAGUE_PACE)
                + (a.get("pace") or LEAGUE_PACE)) / 2.0
    pace = LEAGUE_PACE + (raw_pace - LEAGUE_PACE) * pace_gain
    poss = pace / 100.0

    # Expected points = average of (my offense) and (opponent defense allows),
    # scaled to the game's pace.
    home_pts = ((h["off"] + a["def"]) / 2.0) * poss
    away_pts = ((a["off"] + h["def"]) / 2.0) * poss
    # net_scale controls how hard the rating gap is trusted.
    raw_margin = (home_pts - away_pts) * net_scale
    margin = raw_margin + home_edge   # + = home favored

    # ---- Star Tax: injuries shift the margin like the market does ----
    inj = injuries or {}
    home_tax, home_reasons = _star_tax_points(inj.get(home_abbr))
    away_tax, away_reasons = _star_tax_points(inj.get(away_abbr))
    # A team losing players gives up margin; the opponent gains it.
    margin += (away_tax - home_tax)

    p_home = _norm_cdf(margin / margin_std)
    return {
        "home": home_abbr, "away": away_abbr,
        "exp_home_pts": round(home_pts, 1), "exp_away_pts": round(away_pts, 1),
        "exp_margin": round(margin, 1),           # home perspective
        "exp_total": round(home_pts + away_pts, 1),
        "p_home_win": round(p_home, 4),
        "p_away_win": round(1 - p_home, 4),
        "pace": round(pace, 1),
        "margin_std": margin_std,
        "home_injury_tax": home_tax, "away_injury_tax": away_tax,
        "home_injury_reasons": home_reasons,
        "away_injury_reasons": away_reasons,
    }


def cover_probability(exp_margin, spread_home, margin_std=MARGIN_STD):
    """P(home covers its spread). spread_home is the home line (negative =
    favored, e.g. -6.5). Home covers when actual margin > -spread_home.
    margin_std defaults to the league value but a profile can pass its own."""
    threshold = -spread_home
    z = (exp_margin - threshold) / margin_std
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
