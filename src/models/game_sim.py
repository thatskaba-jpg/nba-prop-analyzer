"""
game_sim.py — Monte Carlo game simulator for NBA sides and totals.

Instead of one point estimate, play the game MANY times and read the outcome
distribution. Each team's score in a single simulated game is drawn from a
normal distribution centered on its expected points (offense vs the opponent's
defense, scaled to pace) with a spread set by how streaky that team's scoring
has been. Ten thousand simulated games give:

  - win probability          (share of sims each team wins)
  - margin distribution      (median, and the 10th-90th percentile band)
  - spread cover probability (share of sims covering a given spread)
  - total over/under prob    (share of sims over a given total line)

All inputs are the real recent-form numbers already pulled from the balldontlie
API (points for / against per game, and their game-to-game std). No magic: the
sim just turns those stats into a believable spread of outcomes so you can see
HOW SURE a pick is, not just which side.
"""

import numpy as np

HOME_EDGE = 2.5          # points, league-standard home-court advantage
LEAGUE_PACE = 100.0
_DEFAULT_TEAM_STD = 12.0  # a team's game-to-game scoring spread (points)


def simulate_game(home_form, away_form, n_sims=10000,
                  spread_home=None, total_line=None, seed=None):
    """home_form / away_form: dicts with
         off  = points scored per game (recent)
         deff = points allowed per game (recent)
         std  = game-to-game scoring std (optional; defaults to 12)
         pace = team pace (optional; defaults to league)
    Returns a summary dict of simulated probabilities. None if inputs missing.
    """
    if not home_form or not away_form:
        return None
    rng = np.random.default_rng(seed)

    def _exp_points(off_team, def_opp, pace):
        # Expected points = blend of our offense and their defense, at pace.
        poss = pace / 100.0
        return ((off_team + def_opp) / 2.0) * poss

    h_pace = home_form.get("pace") or LEAGUE_PACE
    a_pace = away_form.get("pace") or LEAGUE_PACE
    pace = (h_pace + a_pace) / 2.0

    h_mean = _exp_points(home_form["off"], away_form["deff"], pace) + HOME_EDGE / 2.0
    a_mean = _exp_points(away_form["off"], home_form["deff"], pace) - HOME_EDGE / 2.0
    h_std = home_form.get("std") or _DEFAULT_TEAM_STD
    a_std = away_form.get("std") or _DEFAULT_TEAM_STD

    h_scores = rng.normal(h_mean, h_std, n_sims)
    a_scores = rng.normal(a_mean, a_std, n_sims)
    margins = h_scores - a_scores          # home perspective
    totals = h_scores + a_scores

    home_win = float(np.mean(margins > 0))
    out = {
        "n_sims": n_sims,
        "home_win_prob": round(home_win, 4),
        "away_win_prob": round(1.0 - home_win, 4),
        "exp_home_pts": round(float(np.mean(h_scores)), 1),
        "exp_away_pts": round(float(np.mean(a_scores)), 1),
        "median_margin": round(float(np.median(margins)), 1),
        "margin_p10": round(float(np.percentile(margins, 10)), 1),
        "margin_p90": round(float(np.percentile(margins, 90)), 1),
        "median_total": round(float(np.median(totals)), 1),
        "total_p10": round(float(np.percentile(totals, 10)), 1),
        "total_p90": round(float(np.percentile(totals, 90)), 1),
    }
    # Spread cover: home covers if (home margin + spread_home) > 0. spread_home
    # is the home line, e.g. -3.25 for a 3.25-pt home favorite.
    if spread_home is not None:
        covered = float(np.mean((margins + spread_home) > 0))
        out["home_cover_prob"] = round(covered, 4)
        out["away_cover_prob"] = round(1.0 - covered, 4)
        out["spread_home"] = spread_home
    if total_line is not None:
        over = float(np.mean(totals > total_line))
        out["over_prob"] = round(over, 4)
        out["under_prob"] = round(1.0 - over, 4)
        out["total_line"] = total_line
    return out


def sim_one(home_form, away_form, seed=None):
    """Play the game ONCE — a single believable final score (SimCast style).
    Returns {home_pts, away_pts, winner_is_home, margin}. None if no inputs."""
    if not home_form or not away_form:
        return None
    rng = np.random.default_rng(seed)
    poss_pace = ((home_form.get("pace") or LEAGUE_PACE)
                 + (away_form.get("pace") or LEAGUE_PACE)) / 2.0
    poss = poss_pace / 100.0
    h_mean = ((home_form["off"] + away_form["deff"]) / 2.0) * poss + HOME_EDGE / 2.0
    a_mean = ((away_form["off"] + home_form["deff"]) / 2.0) * poss - HOME_EDGE / 2.0
    h = int(round(rng.normal(h_mean, home_form.get("std") or _DEFAULT_TEAM_STD)))
    a = int(round(rng.normal(a_mean, away_form.get("std") or _DEFAULT_TEAM_STD)))
    if h == a:                 # no ties in NBA — nudge by one basket
        h += 1
    return {"home_pts": h, "away_pts": a,
            "winner_is_home": h > a, "margin": abs(h - a)}


if __name__ == "__main__":
    h = {"off": 115, "deff": 112, "std": 12, "pace": 100}
    a = {"off": 118, "deff": 114, "std": 13, "pace": 101}
    r = simulate_game(h, a, spread_home=-3.5, total_line=232, seed=1)
    for k, v in r.items():
        print(f"{k:16} {v}")
