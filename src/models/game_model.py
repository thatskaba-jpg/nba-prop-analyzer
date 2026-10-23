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
#   sim_weight : how much the ROTATION SIM drives the projection vs the flat
#                ratings model (0 = pure ratings, 1 = pure sim). This is what
#                makes presets genuinely DIFFERENT engines now, not just margin
#                resizers: a sim-heavy preset trusts the rotation blowout, a
#                ratings-heavy one stays conservative, so they can land on
#                different sides of close games.
GAME_PROFILES = {
    # RATINGS-based models (the rotation sim is OFF — sim_weight 0 — because its
    # early-season rotation noise underrated strong teams like OKC). The
    # projection still runs on the FULL custom stack: roster strength built from
    # V2 advanced stats (PIE, def_rating, eFG%, usage, etc.), the custom
    # positional-defense stat, four factors, recent form, play-type and
    # injuries. Presets differ by betting stance (home court, confidence, chalk
    # vs dogs).
    "Balanced": {"home_edge": 2.5, "margin_std": 12.0, "net_scale": 1.0,
                 "pace_gain": 1.0, "sim_weight": 0.0, "bet_posture": "balanced",
                 "weights": {}},
    "Trust the ratings (bold)": {"home_edge": 2.5, "margin_std": 10.0,
                                 "net_scale": 1.4, "pace_gain": 1.0,
                                 "sim_weight": 0.0, "bet_posture": "balanced",
                                 # Leans on season ratings; downweights the
                                 # noisier game-to-game signals (form/matchup).
                                 "weights": {"form": 0.5, "matchup": 0.5}},
    "Cautious (toward coin flip)": {"home_edge": 2.0, "margin_std": 16.0,
                                    "net_scale": 0.6, "pace_gain": 1.0,
                                    "sim_weight": 0.0, "bet_posture": "underdog",
                                    "weights": {}},
    "Hot hand (recent form)": {"home_edge": 2.5, "margin_std": 12.0,
                               "net_scale": 0.9, "pace_gain": 1.0,
                               "sim_weight": 0.0, "bet_posture": "balanced",
                               # Chases who's playing well RIGHT NOW.
                               "weights": {"form": 2.5, "matchup": 1.4}},
    "Matchup hunter": {"home_edge": 2.5, "margin_std": 12.0, "net_scale": 1.0,
                       "pace_gain": 1.1, "sim_weight": 0.0,
                       "bet_posture": "balanced",
                       # Leans on positional + play-type matchup edges.
                       "weights": {"matchup": 2.5, "shooting": 1.2}},
    "Favorite slammer (chalk)": {"home_edge": 3.5, "margin_std": 9.0,
                                 "net_scale": 1.5, "pace_gain": 1.0,
                                 "sim_weight": 0.0, "bet_posture": "balanced",
                                 "weights": {}},
}
DEFAULT_PROFILE = "Balanced"

# Plain-English description per profile — shown in the UI like the prop presets.
GAME_PROFILE_DESC = {
    "Balanced": "The solid all-purpose default — team ratings (built from V2 "
                "advanced stats) + standard home court.",
    "Trust the ratings (bold)": "Leans harder on the better-rated team; more "
                                "decisive picks.",
    "Cautious (toward coin flip)": "Pulls close games toward 50/50 and widens "
                                   "outcomes — safer, finds underdog value.",
    "Hot hand (recent form)": "Chases who's playing well RIGHT NOW — weights "
                              "recent form over season-long ratings.",
    "Matchup hunter": "Leans on positional + play-type matchup edges — who "
                      "exploits whom, not just overall strength.",
    "Favorite slammer (chalk)": "Backs favorites hard with a big home edge — "
                                "chalk-heavy, decisive picks.",
}


def _norm_cdf(x):
    """Standard normal CDF via erf — no scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p):
    """Inverse standard-normal CDF (Acklam's rational approximation). Maps a
    probability back to a z-score, so a market-blended win prob can be turned
    into a consistent point margin. No scipy dependency."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _star_tax_points(out_list):
    """Total point-value a team loses from its OUT/DOUBTFUL players.

    Each absent player's spread value ≈ ppg × 0.20, clamped to 0.25-7 points (a
    ~25-ppg star ≈ 5, a 10-ppg rotation player ≈ 2, a deep-bench player well
    under a point), which matches the market's rule of thumb that a top star is
    worth 4-7 points to the spread. The floor used to be a full point, so five
    end-of-bench players listed out docked a team 5 points. DOUBTFUL counts
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
        val = max(0.25, min(7.0, (ppg or 0.0) * 0.20)) * weight
        total += val
        _lbl = ("OUT" if weight >= 1.0
                else "DOUBTFUL" if weight >= 0.5 else "QUESTIONABLE")
        reasons.append(f"{nm} {_lbl} (-{val:.1f})")
    return round(total, 1), reasons


def _playtype_edge(home_abbr, away_abbr, playtype):
    """Points-margin edge from PLAY-TYPE efficiency: each team's offense in the
    key play types (post-up, pick-and-roll ball-handler, isolation, handoff,
    spot-up) vs the opponent's overall defense, weighted by how often the team
    runs each. `playtype` is {type: {abbr: {ppp, poss/freq}}} plus a special
    'def' key {abbr: def_rating} for the opponent-defense baseline.

    We compare each team's frequency-weighted points-per-play to the league
    average (~1.0 PPP), scaled by the opponent's defensive quality, and net the
    two teams. Gentle and capped so it refines, never dominates. 0.0 on missing
    data — never fabricated."""
    if not playtype:
        return 0.0
    KEYS = ("postup", "prballhandler", "isolation", "handoff", "spotup")
    LEAGUE_PPP = 1.0

    def team_pp(abbr):
        num, den = 0.0, 0.0
        for k in KEYS:
            tbl = playtype.get(k) or {}
            s = tbl.get(abbr) or {}
            ppp = s.get("ppp") or s.get("points_per_play")
            freq = s.get("poss") or s.get("possessions") or s.get("freq")
            if ppp is None or not freq:
                continue
            num += float(ppp) * float(freq)
            den += float(freq)
        return (num / den) if den else None

    hpp, app_ = team_pp(home_abbr), team_pp(away_abbr)
    if hpp is None or app_ is None:
        return 0.0
    # Opponent defense scaler (league def ~113; better D shrinks the edge).
    dmap = playtype.get("def") or {}
    hd = dmap.get(away_abbr) or 113.0   # home offense faces away defense
    ad = dmap.get(home_abbr) or 113.0
    # PPP above league, tilted by how good the defense it faces is.
    home_adv = (hpp - LEAGUE_PPP) * (hd / 113.0)
    away_adv = (app_ - LEAGUE_PPP) * (ad / 113.0)
    # ~20 margin pts per 1.0 PPP edge over a ~100-possession game, capped.
    edge = (home_adv - away_adv) * 20.0
    return max(-5.0, min(5.0, edge))


def _tov_battle(home_abbr, away_abbr, four_factors):
    """The turnover BATTLE: my offense's TOV% vs the opponent's defense forcing
    TOV. four_factors carries offensive tov_pct; we approximate the defensive
    force with opp_tov_pct when present, else the team's own as a mirror. Fewer
    turnovers than the opponent forces = extra possessions = margin. Capped."""
    if not four_factors:
        return 0.0
    hf = four_factors.get(home_abbr) or {}
    af = four_factors.get(away_abbr) or {}
    h_off_tov = hf.get("tov_pct")
    a_off_tov = af.get("tov_pct")
    # Defensive TOV forced: use opp_tov_pct if the map carries it.
    h_def_force = hf.get("opp_tov_pct") or af.get("tov_pct")
    a_def_force = af.get("opp_tov_pct") or hf.get("tov_pct")
    if h_off_tov is None or a_off_tov is None:
        return 0.0
    # Net turnover margin for home: (away gives up) - (home gives up), each
    # the player's own rate net of the pressure it faces. ~0.6 pts per 1% edge.
    home_net = (a_off_tov - h_off_tov) * 100.0 * 0.6
    return max(-4.0, min(4.0, home_net))


def _positional_defense_edge(home_abbr, away_abbr, positional):
    """Margin edge from the CUSTOM positional-defense stat — the same leak-free
    signal the player-prop model uses (how a team defends each position group
    for each stat), aggregated to a team level for the game projection.

    `positional` is {stat: {team: {group: factor}}} where factor > 1 means the
    team is SOFT (gives up more than average) to that group/stat, < 1 means
    TOUGH. We average each team's factors across the points/rebounds/assists
    position groups into one 'defense softness' index, then net the two teams:
    the team whose defense is softer concedes margin. Gentle + capped; returns
    0.0 on missing data so it never fabricates an edge."""
    if not positional:
        return 0.0

    def softness(team):
        """Average defensive factor across all stats/groups for one team.
        >1 = soft overall, <1 = tough overall; None when no data."""
        vals = []
        for _stat, tmap in positional.items():
            grp_map = (tmap or {}).get(team) or {}
            for _grp, f in grp_map.items():
                if f:
                    vals.append(float(f))
        return (sum(vals) / len(vals)) if vals else None

    hs = softness(home_abbr)   # home DEFENSE softness
    as_ = softness(away_abbr)  # away DEFENSE softness
    if hs is None or as_ is None:
        return 0.0
    # A soft home defense (hs>1) HELPS the away team -> lowers home margin, and
    # vice-versa. Net = how much tougher home's D is than away's D.
    # ~12 margin pts per full 1.0 softness gap, capped at ±5.
    edge = (as_ - hs) * 12.0
    return max(-5.0, min(5.0, edge))


def project_game(home_abbr, away_abbr, ratings, profile=None, injuries=None,
                 four_factors=None, team_form=None, playtype=None,
                 positional=None, sim=None, market_p_home=None,
                 games_played=0, home_edges=None, record_strength=None,
                 team_api=None):
    """Return a dict with expected margin, win probs, and the inputs used.

    ratings: {abbr: {off, def, pace}} from team_ratings_map. Returns None if
    either team's ratings are missing. `profile` is an optional dict of knobs
    (see GAME_PROFILES). `injuries` is an optional {team_abbr: [(name, status,
    ppg), ...]} map — present teams get a 'Star Tax' margin deduction for their
    OUT/DOUBTFUL players, like the books do when a star is ruled out.
    `four_factors` is an optional {abbr: {efg, tov_pct, oreb_pct, ...}} map
    (team_four_factors_map); when present, the eFG% edge — the single most
    predictive of Dean Oliver's Four Factors — nudges the margin. Off by
    default, so behavior is unchanged when it isn't supplied.
    `team_form` is an optional {abbr: {recent_margin, rest_days}} map
    (team_form.team_form_map); when present it adds RECENT FORM (how each team
    has actually been playing lately) and a REST/back-to-back adjustment on top
    of the season ratings — so the projection isn't built on season averages
    alone. Opt-in; absent means unchanged behavior."""
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
    # Per-team home court: if we have real home/road splits, use THIS home
    # team's actual edge (good home teams get more, poor ones ~0 or negative),
    # scaled by the profile's home_edge dial relative to the ~2.5 league norm.
    # (BOS is -0.3 at home; the flat +2.5 wrongly boosted it over a better DET.)
    if home_edges and home_abbr in home_edges:
        home_edge = home_edges[home_abbr] * (prof["home_edge"] / 2.5)
    margin_std = prof["margin_std"]
    net_scale = prof["net_scale"]
    pace_gain = prof["pace_gain"]
    # Per-profile ADVANCED-STAT weights: how hard this model leans on each V2
    # signal. This is what makes models genuinely DIFFERENT (not just margin
    # rescaling) — one can weight shooting, another rebounding, another form.
    # Default 1.0 = the baseline weight; a profile overrides via prof["weights"].
    _w = prof.get("weights", {})
    w_shoot = _w.get("shooting", 1.0)     # eFG% / TS% edge
    w_ball = _w.get("ball", 1.0)          # turnover battle
    w_reb = _w.get("rebounding", 1.0)     # OREB%/second chances
    w_form = _w.get("form", 1.0)          # recent hot/cold
    w_match = _w.get("matchup", 1.0)      # positional + playtype edge

    # `games_played` arrives as the LEAGUE-WIDE count of finished games this
    # season (that is what the dashboard and the market snapshots store). The
    # fade constants below (K_FORM, K_MKT) are meant PER TEAM, so convert: every
    # game involves two of the 30 teams, so the average team has played
    # 2 * league_games / 30. Left unconverted, 14 league games (two nights in)
    # read as "14 games each" and handed the model 54% of the market blend when
    # each team had played one or two games.
    team_games_played = (games_played or 0) * 2.0 / 30.0

    # Pace: amplify/dampen the game's distance from league average by pace_gain.
    raw_pace = ((h.get("pace") or LEAGUE_PACE)
                + (a.get("pace") or LEAGUE_PACE)) / 2.0
    pace = LEAGUE_PACE + (raw_pace - LEAGUE_PACE) * pace_gain
    poss = pace / 100.0

    # Expected points = average of (my offense) and (opponent defense allows),
    # scaled to the game's pace. This is the RATINGS-based estimate.
    rt_home_pts = ((h["off"] + a["def"]) / 2.0) * poss
    rt_away_pts = ((a["off"] + h["def"]) / 2.0) * poss

    # ---- Optional SIM blend (dormant) -------------------------------------
    # `sim` is an OPTIONAL precomputed {home_pts, away_pts} dict that, when
    # supplied with a profile sim_weight > 0, blends a rotation-based estimate
    # into the margin. Currently OFF for all presets (sim_weight 0) because its
    # early-season rotation noise underrated strong teams; the projection runs
    # on the V2-advanced-driven ratings. Kept as a clean hook to re-enable later.
    SIM_WEIGHT = prof.get("sim_weight", 0.0)
    sim_pts = None
    if SIM_WEIGHT > 0 and isinstance(sim, dict) \
            and sim.get("home_pts") is not None \
            and sim.get("away_pts") is not None:
        home_pts = SIM_WEIGHT * sim["home_pts"] + (1 - SIM_WEIGHT) * rt_home_pts
        away_pts = SIM_WEIGHT * sim["away_pts"] + (1 - SIM_WEIGHT) * rt_away_pts
        sim_pts = (round(sim["home_pts"], 1), round(sim["away_pts"], 1))
    else:
        home_pts = rt_home_pts
        away_pts = rt_away_pts

    # net_scale controls how hard the rating gap is trusted.
    raw_margin = (home_pts - away_pts) * net_scale
    # Home-court soft cap: when the RATINGS clearly favor the road team
    # (raw_margin < -1.5), a flat home bump shouldn't flip the pick to the home
    # side. It can narrow the road edge but not reverse a gap bigger than ~1.5
    # pts. (MIN rated +4.2 at MIA, home court flipped it to MIA — MIN won by 25.)
    if raw_margin < -1.5 and home_edge > 0:
        applied_home_edge = max(0.0, min(home_edge, abs(raw_margin) - 1.5))
    else:
        applied_home_edge = home_edge
    margin = raw_margin + applied_home_edge   # + = home favored

    # ---- Star Tax: injuries shift the margin like the market does ----
    inj = injuries or {}
    home_tax, home_reasons = _star_tax_points(inj.get(home_abbr))
    away_tax, away_reasons = _star_tax_points(inj.get(away_abbr))
    # A team losing players gives up margin; the opponent gains it.
    margin += (away_tax - home_tax)

    # ---- Four Factors: Dean Oliver's advanced drivers nudge the margin ----
    # The research-backed predictors of who wins, in order of weight:
    #   eFG% (~40%), TOV% (~25%), OREB% (~20%), FT-rate (~15%).
    # The feed exposes the three heaviest (eFG, TOV, OREB); we apply each as a
    # per-100-possession points edge, scaled by its Oliver weight, so the
    # margin is built on HOW teams win (shooting, taking care of the ball,
    # second chances) — not season point-differential alone. Applied gently and
    # only when both teams have the field. Opt-in via `four_factors`.
    ff_pts = 0.0
    if four_factors:
        hf = four_factors.get(home_abbr) or {}
        af = four_factors.get(away_abbr) or {}
        # eFG%: ~0.8 margin pts per 1 percentage-point edge (heaviest factor).
        if hf.get("efg") is not None and af.get("efg") is not None:
            ff_pts += (hf["efg"] - af["efg"]) * 100.0 * 0.8 * w_shoot
        # TOV%: scored ONCE, by _tov_battle() below (~0.6 margin pts per 1pt
        # TOV% edge, capped). It used to be added here as well from the very
        # same tov_pct, so every turnover edge counted twice (~1.1 pts per 1pt).
        # OREB%: extra possessions. ~0.4 margin pts per 1pt OREB% edge.
        if hf.get("oreb_pct") is not None and af.get("oreb_pct") is not None:
            ff_pts += (hf["oreb_pct"] - af["oreb_pct"]) * 100.0 * 0.4 * w_reb
        # Cap the combined four-factors swing so it refines, never dominates.
        ff_pts = max(-6.0, min(6.0, ff_pts))
        margin += ff_pts

    # ---- Recent FORM + REST (opt-in): build on more than season ratings ----
    # Season off/def ratings are slow-moving; a team can be red-hot or slumping
    # right now. We nudge the margin toward the difference in the two teams'
    # RECENT point margins (how they've actually played lately), gently
    # (25% weight) so it refines the ratings rather than replacing them. We also
    # apply a small back-to-back fatigue penalty: a team on 0-1 days rest when
    # its opponent is rested loses ~1.5 pts of margin.
    form_pts = 0.0
    rest_pts = 0.0
    tf = team_form or {}
    hform = tf.get(home_abbr) or {}
    aform = tf.get(away_abbr) or {}
    hrm, arm = hform.get("recent_margin"), aform.get("recent_margin")
    if hrm is not None and arm is not None:
        # Blend recent-margin gap into the projected margin (base 25%, scaled by
        # this profile's form weight).
        form_pts = 0.25 * (hrm - arm) * w_form
        # Early-season FADE: with only a game or two played, a single blowout
        # makes "recent form" a ±12 swing that wrongly flips good ratings (NYK
        # +11.9 vs PHI -7.7 flipped PHI@NYK). Shrink form toward 0 until a real
        # sample exists: weight = g/(g+K_FORM). Full strength ~2 weeks in.
        K_FORM = 6.0
        form_pts *= team_games_played / (team_games_played + K_FORM)
        margin += form_pts

    # ---- REAL RECORD (W-L) strength ---------------------------------------
    # A team's actual win% is the cleanest "are they good" signal. The gap
    # between the two teams' record-strength (prior season blended into this
    # one) nudges the margin. Gentle (×0.5) and capped ±4 so it refines the
    # ratings, not replaces them.
    if record_strength:
        hr = record_strength.get(home_abbr)
        ar = record_strength.get(away_abbr)
        if hr is not None and ar is not None:
            rec_pts = max(-4.0, min(4.0, (hr - ar) * 0.5))
            margin += rec_pts

    # ---- ROSTER-SAFE API team cross-check (current season only) -----------
    # The API's own team net rating + eFG for THIS season cross-checks our
    # player-summed ratings. It is NOT blended with last year (a team average
    # carries its old roster), so it's faded by each team's actual games played
    # (gp/(gp+5)) — ~0 now, growing as the real roster logs games. Gentle, cap 3.
    if team_api:
        ha = team_api.get(home_abbr) or {}
        aa = team_api.get(away_abbr) or {}
        hn, an = ha.get("net"), aa.get("net")
        if hn is not None and an is not None:
            gp = min(ha.get("gp") or 0, aa.get("gp") or 0)
            fade = gp / (gp + 5.0)
            api_pts = max(-3.0, min(3.0, (hn - an) * 0.15 * fade))
            margin += api_pts
    hrest, arest = hform.get("rest_days"), aform.get("rest_days")
    if hrest is not None and arest is not None:
        def _b2b(days):
            return 1.5 if days is not None and days <= 1 else 0.0
        # Home tired -> margin down; away tired -> margin up.
        rest_pts = _b2b(arest) - _b2b(hrest)
        margin += rest_pts

    # ---- Play-type matchup edge + turnover battle (opt-in) ----------------
    # How each offense scores (post-ups, PnR, iso, handoffs, spot-ups) vs the
    # defense it faces, and the turnover battle — the matchup specifics a flat
    # rating can't see. Both gentle + capped; 0 when data is missing.
    pt_pts = _playtype_edge(home_abbr, away_abbr, playtype) * w_match
    margin += pt_pts
    tov_pts = _tov_battle(home_abbr, away_abbr, four_factors) * w_ball
    margin += tov_pts
    # Custom positional-defense stat (same signal the prop model uses), netted
    # to a team level so it feeds game lines too — not just player props.
    pos_pts = _positional_defense_edge(home_abbr, away_abbr, positional) * w_match
    margin += pos_pts

    p_home = _norm_cdf(margin / margin_std)

    # ---- Early-season MARKET ANCHOR ---------------------------------------
    # With almost no games played, our player-vs-player ratings are built on
    # last season and can badly misjudge a reshaped roster (it picked NYK over
    # PHI, MIA over MIN). The market has human priors + preseason info we don't.
    # So early on we blend our win prob TOWARD the book's implied prob, then
    # shift to our own model as real games accumulate (w_model = g/(g+K_MKT)).
    # No market or no games_played -> pure model (unchanged).
    market_used = False
    if market_p_home is not None and 0.0 < market_p_home < 1.0:
        K_MKT = 12.0
        w_model = team_games_played / (team_games_played + K_MKT)
        p_home = w_model * p_home + (1 - w_model) * market_p_home
        market_used = True
        # Re-derive a margin consistent with the blended probability, so the
        # displayed "by X" and the pick never contradict the blended win %.
        try:
            margin = _norm_ppf(p_home) * margin_std
        except Exception:
            pass
    # Reconcile the displayed team points with the (possibly market-blended)
    # margin, so "PHI 118 / NYK 112" always agrees with the pick & win%.
    _total = home_pts + away_pts
    disp_home = (_total + margin) / 2.0
    disp_away = (_total - margin) / 2.0
    return {
        "home": home_abbr, "away": away_abbr,
        "exp_home_pts": round(disp_home, 1), "exp_away_pts": round(disp_away, 1),
        "exp_margin": round(margin, 1),           # home perspective
        "exp_total": round(_total, 1),
        "p_home_win": round(p_home, 4),
        "p_away_win": round(1 - p_home, 4),
        "pace": round(pace, 1),
        "margin_std": margin_std,
        "home_injury_tax": home_tax, "away_injury_tax": away_tax,
        "home_injury_reasons": home_reasons,
        "away_injury_reasons": away_reasons,
        "four_factor_pts": round(ff_pts, 1),
        "recent_form_pts": round(form_pts, 1),
        "rest_pts": round(rest_pts, 1),
        "playtype_pts": round(pt_pts, 1),
        "tov_battle_pts": round(tov_pts, 1),
        "positional_pts": round(pos_pts, 1),
        "sim_used": sim_pts is not None,
        "sim_home_pts": sim_pts[0] if sim_pts else None,
        "sim_away_pts": sim_pts[1] if sim_pts else None,
        "market_used": market_used,
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
