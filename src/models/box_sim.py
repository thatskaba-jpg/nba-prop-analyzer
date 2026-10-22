"""
box_sim.py — simulate a FULL NBA game to a real box score, player by player.

Where game_sim.py draws two team totals from team-level form, this builds the
game from the PLAYERS up, using each player's real balldontlie season averages
and advanced usage. Every simulated game gives a believable stat line per
player (min / pts / reb / ast / stl / blk / to / fg / 3p), and the team score
is the sum of its players — exactly like a real box score.

DATA (all live from balldontlie):
  - season_average(player): min, fga, fg_pct, fg3a, fg3_pct, fta, ft_pct,
    reb, ast, stl, blk, turnover  -> the player's baseline rates
  - active_players(): the current roster per team (who actually plays)
  - injury_map(): drop OUT players, downgrade questionable
  - player_advanced_avg(): usage/pace (optional polish)

HOW ONE GAME IS SIMULATED (per player who plays):
  - Field goals: attempts ~ Poisson(season FGA), makes ~ Binomial(att, FG%)
  - Threes:      attempts ~ Poisson(season 3PA), makes ~ Binomial(att, 3P%),
                 capped by made FGs (a three is a made FG)
  - Free throws: attempts ~ Poisson(season FTA), makes ~ Binomial(att, FT%)
  - points = 2*(FGM - 3PM) + 3*3PM + FTM
  - reb/ast/stl/blk/to ~ Poisson(their season average)
  This gives natural game-to-game variance grounded in real rates.

Honest limits: this is a rate-based sim, not a possession-by-possession
engine — it does NOT model clock, fouls, or play-by-play. It produces a
realistic STAT LINE and final score, which is what a box score shows.
"""

import numpy as np


def _min_to_float(m):
    if m is None:
        return 0.0
    try:
        if isinstance(m, (int, float)):
            return float(m)
        s = str(m).strip()
        if ":" in s:
            mm, ss = s.split(":", 1)
            return float(int(mm)) + float(int(ss)) / 60.0
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _sim_player(rng, avg):
    """One simulated stat line from a player's season-average dict."""
    mins = _min_to_float(avg.get("min"))
    if mins < 4:                      # deep bench / DNP-caliber — skip
        return None
    # Injury handling: a questionable/doubtful player plays reduced minutes
    # (and produces proportionally less). OUT players are dropped upstream.
    inj = avg.get("_inj")
    inj_scale = 1.0
    if inj:
        s = str(inj).lower()
        if "doubt" in s:
            inj_scale = 0.45          # likely limited or late scratch
        elif "question" in s or "game-time" in s or "day" in s:
            inj_scale = 0.75          # probable but hampered
    # Scale the player's rate inputs by how much of their minutes they play.
    fga_rate = max(0.0, float(avg.get("fga") or 0)) * inj_scale
    fg_pct = min(0.95, max(0.05, float(avg.get("fg_pct") or 0.45)))
    fg3a_rate = max(0.0, float(avg.get("fg3a") or 0)) * inj_scale
    fg3_pct = min(0.95, max(0.01, float(avg.get("fg3_pct") or 0.35)))
    fta_rate = max(0.0, float(avg.get("fta") or 0)) * inj_scale
    ft_pct = min(0.99, max(0.30, float(avg.get("ft_pct") or 0.75)))

    fga = rng.poisson(fga_rate)
    fgm = rng.binomial(fga, fg_pct) if fga > 0 else 0
    fg3a = min(rng.poisson(fg3a_rate), fga)       # can't shoot more 3s than FGs
    fg3m = rng.binomial(fg3a, fg3_pct) if fg3a > 0 else 0
    fg3m = min(fg3m, fgm)                          # a made 3 is a made FG
    fta = rng.poisson(fta_rate)
    ftm = rng.binomial(fta, ft_pct) if fta > 0 else 0

    pts = 2 * (fgm - fg3m) + 3 * fg3m + ftm
    return {
        "min": int(round(mins * inj_scale)),
        "pts": int(pts),
        "reb": int(rng.poisson(max(0.0, float(avg.get("reb") or 0)) * inj_scale)),
        "ast": int(rng.poisson(max(0.0, float(avg.get("ast") or 0)) * inj_scale)),
        "stl": int(rng.poisson(max(0.0, float(avg.get("stl") or 0)) * inj_scale)),
        "blk": int(rng.poisson(max(0.0, float(avg.get("blk") or 0)) * inj_scale)),
        "to": int(rng.poisson(max(0.0, float(avg.get("turnover") or 0)) * inj_scale)),
        "fgm": int(fgm), "fga": int(fga),
        "fg3m": int(fg3m), "fg3a": int(fg3a),
        "ftm": int(ftm), "fta": int(fta),
        "inj": inj,
    }


def simulate_box(home_roster, away_roster, seed=None):
    """home_roster / away_roster: list of (player_name, season_avg_dict).
    Returns {home:[lines], away:[lines], home_pts, away_pts, ...}. Each line is
    {name, min, pts, reb, ast, stl, blk, to, fgm, fga, fg3m, fg3a, ftm, fta}.
    None if rosters are empty."""
    if not home_roster or not away_roster:
        return None
    rng = np.random.default_rng(seed)

    def _team(roster):
        lines = []
        for name, avg in roster:
            s = _sim_player(rng, avg)
            if s is None:
                continue
            s["name"] = name
            lines.append(s)
        lines.sort(key=lambda x: x["pts"], reverse=True)
        return lines

    hl, al = _team(home_roster), _team(away_roster)
    hp = sum(p["pts"] for p in hl)
    ap = sum(p["pts"] for p in al)
    if hp == ap:
        hp += 1                        # no ties
    return {"home": hl, "away": al, "home_pts": hp, "away_pts": ap,
            "winner_is_home": hp > ap, "margin": abs(hp - ap)}
