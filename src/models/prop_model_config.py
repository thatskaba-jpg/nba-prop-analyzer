"""
prop_model_config.py — the CONFIGURABLE Player Prop model.

This is our version of the website's "Configure template" screen: you pick
FACTORS, turn them on/off, and assign each an IMPORTANCE. Two modes, exactly
like the site:

  - Simple mode:   each selected factor gets Low / Medium / High importance,
                   which map to weights (1 / 2 / 3) internally.
  - Weighted mode: you assign an exact percentage to each selected factor and
                   they must sum to 100 (we validate and tell you if they don't).

WHY THIS SHAPE. The website's team templates (Spread Edge, Over/Under Edge) are
GAME-prediction models built on team ATS records and historical betting lines —
data we do not have. Our data is player box scores, so this builder is the
PLAYER-PROP equivalent: the same "pick factors, set weights, run backtest"
experience, but every factor is one we can actually compute from our data with
NO data leakage. Each factor nudges the projection; the backtest (backtest.py)
then tells you honestly whether a given configuration beats the dumb baseline.

HOW A CONFIG TURNS INTO A PREDICTION. We start from the Phase 4 blended
projection (recent + season form), then apply each ENABLED factor as a small,
transparent multiplier. A factor's weight scales how hard it is allowed to push:
a High / large-% factor moves the number more than a Low / small-% one. Nothing
is a black box — analyze() returns a written breakdown of every adjustment so a
card can show exactly why the number moved.

This file defines:
  - FACTORS:        the catalog of available player-prop factors (the left-hand
                    list on the website screen), grouped like the site.
  - ModelConfig:    a saved configuration (name, bet type, mode, chosen factors).
  - build_config:   a helper/validator that builds a ModelConfig from your picks.
  - analyze():      run one prop through a config -> projection, probs, reasoning.
  - A few READY-MADE configs mirroring recognizable strategies.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.append(str(Path(__file__).resolve().parents[1]))
from features.features import pregame_features, load_player_history  # noqa: E402
from features.opponent_defense import defense_factors  # noqa: E402
from models.baseline_model import (  # noqa: E402
    project_stat,
    project_stat_rate,
    over_under_probabilities,
    confidence_label,
)

# Importance labels -> relative weight (used in Simple mode).
IMPORTANCE_WEIGHTS = {"low": 1.0, "medium": 2.0, "high": 3.0}


# --------------------------------------------------------------------------- #
# FACTOR CATALOG
#
# Each factor is a function that, given the pre-game features and some context,
# returns a MULTIPLIER on the projection (1.0 = no change) plus a human-readable
# reason. The factor is deliberately gentle on its own; the config's weight
# decides how much of that nudge is actually applied (see _apply_weight).
#
# Every factor uses ONLY pre-game information (features.py guarantees this), so
# the no-leakage rule holds no matter which factors you enable.
# --------------------------------------------------------------------------- #


def _recent_form_factor(feats, ctx):
    """player_last_n_avg: lean toward very recent form vs. season baseline.

    If the last-5 average is running hotter/colder than the season average,
    nudge the projection in that direction. Captures a hot/cold streak."""
    a5 = feats.get("avg_last_5")
    season = feats.get("season_avg")
    if a5 is None or not season:
        return 1.0, None
    ratio = a5 / season
    # Clamp so one wild game can't blow it up.
    ratio = max(0.80, min(1.20, ratio))
    if abs(ratio - 1.0) < 0.01:
        return 1.0, None
    direction = "hot" if ratio > 1 else "cold"
    return ratio, f"recent form {direction} (L5 {a5:.1f} vs season {season:.1f}) x{ratio:.3f}"


def _opponent_defense_factor(feats, ctx):
    """opponent_defense: soft defense -> up, tough defense -> down.

    Uses the leak-free defense_factors map built from games before the cutoff.
    Falls back to no-op when the opponent isn't resolvable (balldontlie rows)."""
    opp = ctx.get("opponent_abbr")
    dmap = ctx.get("defense_factor_map")
    if not opp or not dmap:
        return 1.0, None
    factor = dmap.get(opp)
    if not factor:
        return 1.0, None
    tag = "soft" if factor > 1 else "tough"
    return factor, f"opponent {opp} defense {tag} x{factor:.3f}"


def _positional_defense_factor(feats, ctx):
    """opponent_defense_by_position: how the opponent defends THIS player's
    position group for THIS stat — a sharper matchup signal than team-total
    points allowed (a team can stop guards but get torched by bigs).

    Needs ctx: opponent_abbr, position_group, and positional_defense_map
    = {team: {group: factor}}. No-op if any is missing."""
    opp = ctx.get("opponent_abbr")
    grp = ctx.get("position_group")
    pmap = ctx.get("positional_defense_map")
    if not opp or not grp or not pmap:
        return 1.0, None
    factor = (pmap.get(opp) or {}).get(grp)
    if not factor:
        return 1.0, None
    tag = "soft" if factor > 1 else "tough"
    return factor, (f"opponent {opp} is {tag} vs {grp}s for this stat "
                    f"x{factor:.3f}")


def _rest_days_factor(feats, ctx):
    """rest_days: back-to-backs drag output down; good rest nudges up."""
    rd = feats.get("rest_days")
    if rd is None:
        return 1.0, None
    if rd <= 1:
        return 0.97, f"short rest ({rd}d) x0.970"
    if rd >= 3:
        return 1.02, f"well rested ({rd}d) x1.020"
    return 1.0, None


def _injury_factor(feats, ctx):
    """injury: flag the player's own injury status (from ctx['injury_status']).

    OUT -> the prop is effectively void (project ~0 and say so loudly).
    DOUBTFUL -> heavy down. QUESTIONABLE -> mild down (uncertainty).
    No status, or healthy -> no change. We never invent a status."""
    status = (ctx.get("injury_status") or "").strip().lower()
    if not status:
        return 1.0, None
    if "out" in status:
        return 0.01, "player listed OUT — prop likely void (projection ~0)"
    if "doubtful" in status:
        return 0.5, "player DOUBTFUL — heavy downgrade"
    if "question" in status:
        return 0.9, "player QUESTIONABLE — mild downgrade for uncertainty"
    return 1.0, None


def _teammate_injuries_factor(feats, ctx):
    """teammate_injuries: BOOST the projection when the player's own teammates
    are OUT, scaled by HOW GOOD each absent teammate is.

    ctx['out_teammates'] is a list of tuples describing teammates listed
    OUT/DOUBTFUL tonight. Each may be:
        (name, status)              — unweighted (treated as an average starter)
        (name, status, ppg)         — weighted by the teammate's scoring average

    A benched 25-ppg star frees far more usage (shots, touches, minutes) than a
    4-ppg bench player, so we weight each absence by its ppg relative to a
    reference starter (~15 ppg = full weight). DOUBTFUL counts half (the player
    might still suit up). The total boost is gentle and capped so even a
    decimated roster can't produce an absurd line — it nudges, never fabricates.
    """
    mates = ctx.get("out_teammates")
    if not mates:
        return 1.0, None

    REF_PPG = 15.0   # a solid starter's scoring = one "full" absence
    weight = 0.0
    names = []
    for m in mates:
        nm = m[0]
        status = m[1] if len(m) > 1 else "out"
        ppg = m[2] if len(m) > 2 else None
        s = (status or "").lower()
        if "out" in s:
            status_mult = 1.0
        elif "doubtful" in s:
            status_mult = 0.5
        else:
            continue
        # Scoring weight: ppg/REF, clamped to [0.3, 2.0] so a superstar counts
        # more and a deep-bench absence counts little but never zero. Unknown
        # ppg falls back to 1.0 (treat as an average starter).
        if ppg is None:
            score_w = 1.0
        else:
            score_w = max(0.3, min(2.0, ppg / REF_PPG))
        weight += status_mult * score_w
        names.append(nm)
    if weight <= 0:
        return 1.0, None
    # ~4% per full-weight absence, diminishing, capped at +15% (a touch higher
    # than the flat version's cap since weight now reflects real scoring load).
    boost = min(0.15, 0.04 * weight)
    who = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
    return 1.0 + boost, f"teammate(s) out ({who}) — usage up x{1.0 + boost:.3f}"


def _confirmed_lineup_factor(feats, ctx):
    """confirmed_lineup: adjust for TONIGHT'S confirmed starting lineup.

    Lineups post near tip-off. Starting vs coming off the bench is a direct
    signal about minutes and usage that the injury report alone can miss (a
    healthy player can still be benched, and a surprise starter gets a real
    bump). ctx carries:
        lineup_status  — 'starter' | 'bench' | None  (what the feed says tonight)
        usually_starts — bool: did this player start most of his recent games?

    Rules, deliberately gentle (lineups are not a minutes guarantee):
      * confirmed BENCH + usually a starter  -> downgrade (benched / load-
        managed): x0.80.
      * confirmed BENCH + usually a bench guy -> no change (already priced in).
      * confirmed STARTER + usually a bench guy -> surprise starter: x1.10.
      * confirmed STARTER + usually a starter -> no change (expected).
      * nothing posted yet -> no-op.
    We never infer a lineup; only an explicit feed value moves the number."""
    status = (ctx.get("lineup_status") or "").strip().lower()
    if status not in ("starter", "bench"):
        return 1.0, None
    # Avoid double-counting: if the rate model is already adjusting projected
    # MINUTES for this role change (minutes_override present), don't also apply
    # the flat multiplier — the minutes override is the more precise mechanism.
    if ctx.get("minutes_override") is not None:
        return 1.0, None
    usually_starts = bool(ctx.get("usually_starts"))
    if status == "bench":
        if usually_starts:
            return 0.80, ("confirmed on the BENCH tonight (usually starts) — "
                          "fewer minutes/usage expected")
        return 1.0, None  # bench player benched: already reflected in his form
    # status == "starter"
    if not usually_starts:
        return 1.10, ("confirmed STARTING tonight (usually a reserve) — "
                      "more minutes/usage expected")
    return 1.0, None


# Opportunity-driven stats: these scale with how many shots/possessions a player
# gets, so usage and pace matter. Pure-efficiency or defensive stats don't, so
# the factor is a no-op for them.
_OPPORTUNITY_STATS = {
    "points", "threes_made", "threes_att", "fgm", "fga", "ftm", "fta",
    "pra", "pr", "pa", "assists", "rebounds",
}
_LEAGUE_USAGE = 0.20   # a league-average player uses ~20% of possessions.
_LEAGUE_PACE = 100.0   # ~100 possessions/48 is a rough league-average pace.
_LEAGUE_TS = 0.56      # ~0.56 true-shooting is a rough league-average mark.


def _usage_and_pace_factor(feats, ctx):
    """usage_and_pace: value the ADVANCED role signals we already fetch.

    A high-usage player on a fast team gets more shots, touches and trips down
    the floor than his raw average implies; a low-usage player on a slow team
    gets fewer. ctx['player_adv'] carries the player's season advanced averages
    (from balldontlie V2): usage (0-1 share of possessions) and pace
    (possessions/48). We nudge opportunity-driven stats by how far each sits
    from the league baseline, blended and capped so it refines, never dominates.

    Deliberately conservative: usage is the stronger signal (direct share of
    the offense), pace the weaker (team tempo). Combined move is capped at
    +/-12%. No advanced data, or a non-opportunity stat -> no-op."""
    prop = (ctx.get("_prop_type") or "").lower()
    if prop and prop not in _OPPORTUNITY_STATS:
        return 1.0, None
    adv = ctx.get("player_adv") or {}
    usage = adv.get("usage")
    pace = adv.get("pace")
    if usage is None and pace is None:
        return 1.0, None

    # Each component is a ratio vs league baseline, softened (half-weight) so a
    # 25%-usage star doesn't get a full +25% — role already shows up in his raw
    # average; this only captures the MARGINAL opportunity effect.
    mult = 1.0
    bits = []
    if usage is not None and usage > 0:
        u_ratio = usage / _LEAGUE_USAGE
        u_adj = 1.0 + 0.5 * (u_ratio - 1.0)          # half-weight
        u_adj = max(0.90, min(1.12, u_adj))
        mult *= u_adj
        bits.append(f"usage {usage*100:.0f}%")
    if pace is not None and pace > 0:
        p_ratio = pace / _LEAGUE_PACE
        p_adj = 1.0 + 0.3 * (p_ratio - 1.0)          # weaker signal
        p_adj = max(0.95, min(1.06, p_adj))
        mult *= p_adj
        bits.append(f"pace {pace:.0f}")
    # Per-stat V2 EFFICIENCY: for MADE-shot stats, true shooting% refines the
    # projection beyond pure volume — the same attempts convert more or less
    # often. Only applied to makes (points/fgm/ftm/threes_made), never to
    # attempts/volume. Half-weighted vs a ~0.56 league TS baseline, capped.
    _MAKES = {"points", "fgm", "ftm", "threes_made"}
    ts = adv.get("ts_pct")
    if prop in _MAKES and ts is not None and ts > 0:
        ts_adj = 1.0 + 0.5 * (ts / _LEAGUE_TS - 1.0)
        ts_adj = max(0.93, min(1.08, ts_adj))
        mult *= ts_adj
        bits.append(f"TS {ts*100:.0f}%")
    # Final clamp on the combined move.
    mult = max(0.85, min(1.15, mult))
    if abs(mult - 1.0) < 1e-3:
        return 1.0, None
    direction = "more" if mult > 1.0 else "fewer"
    return mult, (f"role/tempo ({', '.join(bits)}) — {direction} "
                  f"opportunities x{mult:.3f}")


def _game_environment_factor(feats, ctx):
    """game_environment: use the GAME LINE (total + this team's spread) as a
    scoring-environment nudge for counting-stat props.

    ctx['game_line'] is {total, spread, ...} from the game-odds feed.
      - TOTAL vs a ~230 league-average game: a high total is a track meet
        (more scoring for everyone -> nudge UP); a low total is a grind
        (nudge DOWN). Scaled gently.
      - SPREAD (this team's own, negative = favored): a BIG margin means
        blowout risk — a heavy favorite rests starters in the 4th quarter,
        capping counting stats. Only large spreads (|s| >= 12) apply a small
        fade; competitive games do nothing.
    Both clamped; combined and capped at +/-8%.
    """
    gl = ctx.get("game_line")
    if not gl:
        return 1.0, None
    total = gl.get("total")
    spread = gl.get("spread")
    LEAGUE_TOTAL = 230.0
    parts = []
    mult = 1.0

    if total is not None and total > 0:
        t = (total - LEAGUE_TOTAL) / LEAGUE_TOTAL        # e.g. +0.04 at 240
        t = max(-0.10, min(0.10, t))
        if abs(t) >= 0.01:
            mult *= 1.0 + t * 0.6                         # dampen to ~+/-6%
            env = "high-scoring" if t > 0 else "low-scoring"
            parts.append(f"{env} game (total {total:g})")

    if spread is not None and abs(spread) >= 12:
        # Blowout risk fade, grows past a 12-point spread, capped at -6%.
        fade = min(0.06, (abs(spread) - 12) * 0.006 + 0.02)
        mult *= 1.0 - fade
        parts.append(f"blowout risk (spread {spread:+g})")

    if not parts or abs(mult - 1.0) < 0.005:
        return 1.0, None
    mult = max(0.92, min(1.08, mult))
    return mult, f"{'; '.join(parts)} -> x{mult:.3f}"


def _home_away_factor(feats, ctx):
    """home_away_split: lean toward the player's split for the game's venue."""
    is_home = ctx.get("upcoming_is_home")
    if is_home is None:
        return 1.0, None
    base = project_stat(feats)
    split = feats.get("home_avg") if is_home else feats.get("away_avg")
    if base is None or not base or split is None:
        return 1.0, None
    ratio = max(0.85, min(1.15, split / base))
    venue = "home" if is_home else "away"
    if abs(ratio - 1.0) < 0.01:
        return 1.0, None
    return ratio, f"{venue} split x{ratio:.3f}"


def _minutes_trend_factor(feats, ctx):
    """minutes_trend: a rising role (more minutes lately) lifts the projection."""
    mt = feats.get("minutes_trend")
    if mt is None or abs(mt) < 1.0:
        return 1.0, None
    # Each extra minute of recent role ~ +1.5% up to a cap.
    pct = max(-0.08, min(0.08, mt * 0.015))
    direction = "rising" if pct > 0 else "falling"
    return 1.0 + pct, f"{direction} role ({mt:+.1f} min) x{1.0 + pct:.3f}"


def _opponent_team_rating_factor(feats, ctx):
    """opponent_team_rating: use the opponent's OFFICIAL balldontlie team
    ratings (defensive rating + pace) as a matchup nudge.

    - Defensive rating is points allowed per 100 possessions. LOWER = tougher
      defense (nudge the player DOWN); HIGHER than league average = weaker
      defense (nudge UP). League average sits near 113.
    - Pace is possessions per 48 min. A faster opponent means MORE possessions
      for everyone, so more chances to pile up a counting stat (nudge UP);
      slower = fewer chances (nudge DOWN). League average sits near 100.

    Both pulls are gentle and clamped. Needs ctx['team_ratings_map'] =
    {team_abbr: {'def':..., 'pace':...}} and ctx['opponent_abbr']. No-op if
    either is missing or the opponent has no rating yet (early season)."""
    opp = ctx.get("opponent_abbr")
    tmap = ctx.get("team_ratings_map")
    if not opp or not tmap:
        return 1.0, None
    row = tmap.get(opp)
    if not row:
        return 1.0, None

    LEAGUE_DEF, LEAGUE_PACE = 113.0, 100.0
    parts, pct = [], 0.0

    dr = row.get("def")
    if dr:
        # Opponent allows more than average -> easier -> up. Scale: a 10-point
        # rating gap ~ 5% move, clamped to +/-6%.
        d = (dr - LEAGUE_DEF) / LEAGUE_DEF
        d = max(-0.06, min(0.06, d * 0.5))
        pct += d
        if abs(d) >= 0.005:
            tag = "weaker" if d > 0 else "tougher"
            parts.append(f"{opp} defense {tag} (rating {dr:g} vs ~{LEAGUE_DEF:g})")

    pace = row.get("pace")
    if pace:
        # Faster than average -> more possessions -> up. Scale: a 10-possession
        # gap ~ 4% move, clamped to +/-4%.
        p = (pace - LEAGUE_PACE) / LEAGUE_PACE
        p = max(-0.04, min(0.04, p * 0.4))
        pct += p
        if abs(p) >= 0.005:
            tag = "fast" if p > 0 else "slow"
            parts.append(f"{opp} plays {tag} (pace {pace:g} vs ~{LEAGUE_PACE:g})")

    if not parts or abs(pct) < 0.005:
        return 1.0, None
    mult = 1.0 + pct
    return mult, "; ".join(parts) + f" x{mult:.3f}"


def _player_vs_team_factor(feats, ctx):
    """player_vs_team: how this player has done vs. this specific opponent.

    NOTE: historical head-to-head is a SMALL sample and a weak predictor (the
    brief warns against leaning on it). We include it because the website does,
    but keep its pull tiny and only when enough prior meetings exist."""
    h2h_avg = ctx.get("h2h_avg")
    season = feats.get("season_avg")
    n = ctx.get("h2h_games", 0)
    if not h2h_avg or not season or n < 3:
        return 1.0, None
    ratio = max(0.90, min(1.10, h2h_avg / season))
    if abs(ratio - 1.0) < 0.01:
        return 1.0, None
    # Deliberately damp hard — small sample.
    damped = 1.0 + (ratio - 1.0) * 0.4
    return damped, f"vs-opponent history ({n} mtgs, {h2h_avg:.1f}) x{damped:.3f}"


@dataclass
class Factor:
    key: str
    label: str
    group: str
    func: Callable
    needs: str = ""  # what extra context it needs, for honest UI hints


# The catalog, grouped like the website's left column (adapted to props).
FACTORS: Dict[str, Factor] = {
    "player_last_n_avg": Factor(
        "player_last_n_avg", "Player Recent Performance", "Player",
        _recent_form_factor),
    "opponent_defense": Factor(
        "opponent_defense", "Opponent Defensive Strength", "Matchup",
        _opponent_defense_factor, needs="opponent (CSV seasons only)"),
    "opponent_defense_by_position": Factor(
        "opponent_defense_by_position", "Opponent Defense vs Position", "Matchup",
        _positional_defense_factor,
        needs="opponent + this player's position group + a per-position "
              "defense map"),
    "opponent_team_rating": Factor(
        "opponent_team_rating", "Opponent Team Rating (off/def/pace)", "Matchup",
        _opponent_team_rating_factor,
        needs="opponent + official balldontlie team ratings (def rating + "
              "pace); updates as the season progresses"),
    "rest_days": Factor(
        "rest_days", "Rest Days", "Situational", _rest_days_factor),
    "injury": Factor(
        "injury", "Injury Status", "Situational", _injury_factor,
        needs="the player's current injury status (from GOAT injuries feed)"),
    "teammate_injuries": Factor(
        "teammate_injuries", "Teammate Injuries (usage boost)", "Situational",
        _teammate_injuries_factor,
        needs="which of the player's teammates are OUT/DOUBTFUL tonight "
              "(from the merged ESPN + GOAT injuries feed)"),
    "confirmed_lineup": Factor(
        "confirmed_lineup", "Confirmed Starting Lineup", "Situational",
        _confirmed_lineup_factor,
        needs="tonight's confirmed lineup (starter/bench, from the balldontlie "
              "lineups feed) + whether the player usually starts"),
    "usage_and_pace": Factor(
        "usage_and_pace", "Usage, Pace & Efficiency (advanced)", "Player",
        _usage_and_pace_factor,
        needs="the player's advanced season averages (usage% + pace, plus true-"
              "shooting% for made-shot stats, from the balldontlie V2 feed)"),
    "game_environment": Factor(
        "game_environment", "Game Line (total + spread)", "Matchup",
        _game_environment_factor,
        needs="tonight's game total + this team's spread (from the balldontlie "
              "game-odds feed)"),
    "home_away_split": Factor(
        "home_away_split", "Home/Away Split", "Situational",
        _home_away_factor, needs="which venue the upcoming game is"),
    "minutes_trend": Factor(
        "minutes_trend", "Player Role / Minutes Trend", "Player",
        _minutes_trend_factor),
    "player_vs_team": Factor(
        "player_vs_team", "Player vs Opponent History", "Matchup",
        _player_vs_team_factor, needs="opponent + prior meetings (weak signal)"),
}


# --------------------------------------------------------------------------- #
# MODEL CONFIG
# --------------------------------------------------------------------------- #

@dataclass
class ModelConfig:
    name: str
    mode: str                       # "simple" or "weighted"
    # factor_key -> importance ("low"/"medium"/"high") in simple mode,
    #               or percentage (0-100) in weighted mode.
    factors: Dict[str, object] = field(default_factory=dict)
    prop_types: List[str] = field(default_factory=lambda: ["points"])

    def normalized_weights(self) -> Dict[str, float]:
        """Return factor_key -> weight in [0,1], summing to 1 over enabled factors.

        Simple mode: map Low/Med/High to 1/2/3 then normalize.
        Weighted mode: use the given percentages (already validated to sum ~100).
        """
        if not self.factors:
            return {}
        if self.mode == "weighted":
            total = sum(float(v) for v in self.factors.values())
            if total <= 0:
                return {}
            return {k: float(v) / total for k, v in self.factors.items()}
        # simple
        raw = {k: IMPORTANCE_WEIGHTS.get(str(v).lower(), 1.0)
               for k, v in self.factors.items()}
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()} if total else {}


def build_config(name: str, mode: str, factors: Dict[str, object],
                 prop_types: Optional[List[str]] = None) -> ModelConfig:
    """Validate the picks (like the website does) and return a ModelConfig.

    Raises ValueError with a plain-English message on bad input, e.g. an
    unknown factor, or weighted percentages that don't sum to 100.
    """
    mode = mode.lower()
    if mode not in ("simple", "weighted"):
        raise ValueError("mode must be 'simple' or 'weighted'")
    if not factors:
        raise ValueError("Select at least one factor.")
    for k in factors:
        if k not in FACTORS:
            raise ValueError(
                f"Unknown factor '{k}'. Available: {list(FACTORS)}")
    if mode == "simple":
        for k, v in factors.items():
            if str(v).lower() not in IMPORTANCE_WEIGHTS:
                raise ValueError(
                    f"Factor '{k}' importance must be Low/Medium/High, got '{v}'")
    else:  # weighted — percentages must sum to 100 (±0.5 tolerance)
        total = sum(float(v) for v in factors.values())
        if abs(total - 100.0) > 0.5:
            raise ValueError(
                f"Weighted percentages must sum to 100%. "
                f"Yours sum to {total:g}% across {len(factors)} factors.")
    return ModelConfig(name=name, mode=mode, factors=dict(factors),
                       prop_types=prop_types or ["points"])


# League-average two-team game total (points). A game's betting TOTAL above this
# implies a faster/higher-scoring game than the teams' season pace alone says.
_LEAGUE_TOTAL = 230.0


def _projected_game_pace(ctx: dict):
    """Per-game projected pace (possessions/48), or None if we can't estimate.

    Combines two signals, both leak-free and already in ctx:
      1. The two teams' SEASON pace (balldontlie team ratings) — the baseline.
      2. Tonight's betting TOTAL vs a league-average total — the market's own
         per-game read on tempo/scoring. A 240 total means a faster night than
         either team's season average; a 210 total, slower.

    We take the season two-team average, then tilt it by the total's deviation
    from league average (half-weighted, capped), so pace becomes GAME-SPECIFIC
    instead of a flat season number. Missing total -> season pace only; missing
    both -> None (the rate model then skips the pace term)."""
    tr = ctx.get("team_ratings_map") or {}
    my_team = (ctx.get("current_team") or "").upper()
    opp = (ctx.get("opponent_abbr") or "").upper()

    # TRUE recent pace (last-N games per team) takes priority over season pace
    # when present. ctx['recent_pace_map'] is {team_abbr: recent_pace} supplied
    # by the dashboard from the team_game_pace collector.
    rpm = ctx.get("recent_pace_map") or {}
    recent = [rpm[t] for t in (my_team, opp) if rpm.get(t)]
    season_paces = [tr[t].get("pace") for t in (my_team, opp)
                    if t in tr and tr[t].get("pace")]
    # Prefer recent pace; fill any missing side from season pace.
    pace_vals = recent if len(recent) == 2 else (
        recent + season_paces if recent else season_paces)
    season_pace = sum(pace_vals) / len(pace_vals) if pace_vals else None

    total = None
    gl = ctx.get("game_line") or {}
    if isinstance(gl, dict):
        total = gl.get("total")

    if season_pace is None and total is None:
        return None
    if total is None:
        return season_pace
    # Total-implied tempo ratio vs league average, half-weighted and capped so a
    # single number can't swing pace wildly.
    ratio = 1.0 + 0.5 * (float(total) / _LEAGUE_TOTAL - 1.0)
    ratio = max(0.90, min(1.10, ratio))
    if season_pace is None:
        # No team pace: scale the league-average pace by the total signal.
        return 100.0 * ratio
    return season_pace * ratio


def _apply_weight(factor_multiplier: float, weight: float) -> float:
    """Scale a factor's nudge by its weight.

    A factor proposes a multiplier (e.g. 1.06 = +6%). Weight (0..1) decides how
    much of that nudge we actually take: full weight applies all of it, half
    weight applies half. So the '+6%' becomes '+6% * weight'."""
    return 1.0 + (factor_multiplier - 1.0) * weight


# --------------------------------------------------------------------------- #
# RUN ONE PROP THROUGH A CONFIG
# --------------------------------------------------------------------------- #

def analyze(config: ModelConfig, player_id: int, prop_type: str, line: float,
            cutoff_date: str, history=None, context: Optional[dict] = None,
            calibrate: bool = True) -> dict:
    """Project + probability for one prop under this config, with reasoning.

    context may carry: opponent_abbr, defense_factor_map, upcoming_is_home,
    h2h_avg, h2h_games. Anything missing simply makes the relevant factor a
    no-op (and the breakdown says so by omitting it) — never a crash.
    """
    ctx = context or {}
    # Let factors know which stat they're adjusting (e.g. usage/pace only moves
    # opportunity-driven stats). Non-destructive copy so the caller's dict is
    # untouched across a multi-prop loop.
    ctx = dict(ctx)
    ctx["_prop_type"] = prop_type
    if history is None:
        history = load_player_history(player_id)
    feats = pregame_features(player_id, prop_type, cutoff_date, history,
                             current_team=ctx.get("current_team"))

    base = project_stat(feats)
    if base is None:
        return {
            "player_id": player_id, "prop_type": prop_type, "line": line,
            "cutoff_date": cutoff_date, "projection": None, "p_over": None,
            "p_under": None, "confidence": "none (no prior games)",
            "adjustments": [], "config": config.name,
        }

    # --- Deeper RATE model: per-minute x projected-minutes x pace ------------
    # Blend the totals projection with a rate projection so a change in ROLE
    # (minutes) or TEMPO (pace) is valued explicitly, not hidden inside the
    # totals average. The rate path is noisier (minutes vary), so by default it
    # gets a MINORITY weight and only applies to opportunity-driven stats.
    # ctx flags (all optional): use_rate_model (default True), rate_blend (0..1,
    # default 0.35), minutes_override (lineup/injury-adjusted minutes).
    base_desc = None
    _RATE_STATS = {"points", "threes_made", "threes_att", "fgm", "fga",
                   "ftm", "fta", "rebounds", "assists", "pra", "pr", "pa",
                   "ra", "steals", "blocks", "turnovers", "stocks"}
    if ctx.get("use_rate_model", True) and prop_type in _RATE_STATS:
        # Per-game projected pace: two-team season pace tilted by tonight's
        # betting total (the market's tempo read). None -> no pace adjustment.
        pace = _projected_game_pace(ctx)
        rate_base = project_stat_rate(
            feats, pace=pace, minutes_override=ctx.get("minutes_override"))
        if rate_base is not None and rate_base > 0:
            blend = ctx.get("rate_blend", 0.35)
            blend = max(0.0, min(1.0, float(blend)))
            blended = (1 - blend) * base + blend * rate_base
            _mo = ctx.get("minutes_override")
            _eff_min = _mo if _mo is not None else feats.get("proj_minutes", 0)
            base_desc = (f"rate model {rate_base:.1f} (per-min "
                         f"{feats.get('per_min_rate', 0):.3f} x "
                         f"{_eff_min:.0f} min"
                         + (" [confirmed-role minutes]" if _mo is not None else "")
                         + (f" x pace {pace:.0f}" if pace else "")
                         + f") blended {blend:.0%} with totals {base:.1f} "
                         f"-> {blended:.1f}")
            base = blended

    weights = config.normalized_weights()
    projection = base
    adjustments = []
    for key, weight in weights.items():
        factor = FACTORS[key]
        mult, reason = factor.func(feats, ctx)
        if mult == 1.0 or reason is None:
            continue  # factor had nothing to say (missing data or neutral)
        applied = _apply_weight(mult, weight)
        before = projection
        projection *= applied
        adjustments.append(
            f"[{factor.label} w={weight:.0%}] {reason} -> net x{applied:.3f} "
            f"({before:.1f} -> {projection:.1f})")

    projection = round(projection, 2)
    probs = over_under_probabilities(projection, line, feats.get("std_recent"))

    raw_over = probs["p_over"]
    raw_under = probs["p_under"]
    p_over = raw_over
    p_under = raw_under
    calibrated = False
    # Apply the empirical calibration map so a stated probability MEANS what it
    # says (the model is overconfident raw). Skipped when calibrate=False — the
    # calibration fitter itself needs the RAW probs, else the fit is circular.
    if calibrate and raw_over is not None:
        try:
            from models.calibration import calibrate_p_over, is_available
            if is_available():
                cal = calibrate_p_over(raw_over, prop_type)
                if cal is not None:
                    p_over = cal
                    p_under = 1.0 - cal - (probs["push"] or 0.0)
                    p_under = max(p_under, 0.0)
                    calibrated = True
        except Exception:
            pass  # never let calibration break a projection

    # --- Side consistency + combo-stat confidence ---
    # Combo stats (PTS+AST etc.) are the sum of correlated components; the
    # per-game Poisson/NB fit and the calibration map are both unreliable for
    # them, which is exactly where calibration flipped a clear projection to the
    # wrong side. Two guards:
    #   1) The probability must never contradict the projection. If proj > line,
    #      p_over stays >= 0.5 (and vice versa) — the pick, win%, and EV all then
    #      agree with the number we show.
    #   2) For combo stats, pull the probability toward 0.5 (less confident) and
    #      flag it, since we genuinely can't price them as tightly.
    _COMBO = {"pa", "pr", "ra", "pra", "points_assists", "points_rebounds",
              "rebounds_assists", "points_rebounds_assists"}
    # Which raw columns each combo sums.
    _COMBO_COLS = {
        "pa": ["points", "assists"], "points_assists": ["points", "assists"],
        "pr": ["points", "rebounds"], "points_rebounds": ["points", "rebounds"],
        "ra": ["rebounds", "assists"], "rebounds_assists": ["rebounds", "assists"],
        "pra": ["points", "rebounds", "assists"],
        "points_rebounds_assists": ["points", "rebounds", "assists"],
    }
    is_combo = prop_type in _COMBO
    combo_note = None
    if p_over is not None and projection is not None:
        push = probs.get("push") or 0.0
        proj_over = projection > line

        # (A) REAL combo win%: the per-game SUM of the components already
        # contains the correlation between them, so the empirical distribution
        # of that sum is the honest probability — no Poisson, no shrink guess.
        if is_combo:
            cols = _COMBO_COLS.get(prop_type, [])
            try:
                if (history is not None and len(history) >= 10
                        and all(c in history.columns for c in cols)):
                    import numpy as _np
                    totals = history[cols].sum(axis=1).to_numpy(dtype=float)
                    # Recency weight: use the last ~30 games (same window spirit
                    # as the projection). Shift the empirical dist so its mean
                    # equals OUR projection (keeps matchup/injury adjustments)
                    # while borrowing the player's real shape/spread.
                    recent = totals[-30:] if len(totals) > 30 else totals
                    shifted = recent - recent.mean() + projection
                    p_over = float((shifted > line).mean())
                    # Guard tiny samples away from 0/1 certainty.
                    p_over = min(max(p_over, 0.02), 0.98)
                    p_under = max(1.0 - p_over, 0.0)
                    combo_note = ("combo win% from this player's real game-by-"
                                  "game totals (captures how the stats move "
                                  "together) — more honest than a formula")
                else:
                    combo_note = ("combo stat — not enough history for a precise "
                                  "win%; trust the hit-rate cards")
            except Exception:
                combo_note = ("combo stat — priced loosely; trust the hit-rate "
                              "cards")

        # (B) Side consistency: the probability must never land on the opposite
        # side of the projection (applies to single AND combo stats).
        if proj_over and p_over < 0.5:
            p_over = max(p_over, 0.5)
            p_under = max(1.0 - p_over - (0.0 if is_combo else push), 0.0)
        elif (not proj_over) and p_over > 0.5:
            p_over = min(p_over, 0.5)
            p_under = max(1.0 - p_over - (0.0 if is_combo else push), 0.0)

        p_over = round(p_over, 4)
        p_under = round(p_under, 4)

    return {
        "player_id": player_id,
        "prop_type": prop_type,
        "line": line,
        "cutoff_date": cutoff_date,
        "base_projection": round(base, 2),
        "base_desc": base_desc,
        "projection": projection,
        "p_over": round(p_over, 4) if p_over is not None else None,
        "p_under": round(p_under, 4) if p_under is not None else None,
        "p_over_raw": round(raw_over, 4) if raw_over is not None else None,
        "calibrated": calibrated,
        "is_combo": is_combo,
        "combo_note": combo_note,
        "push": round(probs["push"], 4) if probs["push"] is not None else None,
        "games_played_before": feats.get("games_played_before", 0),
        "confidence": confidence_label(feats),
        "adjustments": adjustments,
        "config": config.name,
    }


# --------------------------------------------------------------------------- #
# READY-MADE CONFIGS (our equivalent of the website's starter templates)
# --------------------------------------------------------------------------- #

def preset_momentum_tracker() -> ModelConfig:
    """Simple mode: leans on recent form + role, like the 'Momentum Tracker'."""
    return build_config(
        name="Momentum Tracker (Props)", mode="simple",
        factors={"player_last_n_avg": "high",
                 "minutes_trend": "high",
                 "rest_days": "medium"})


def preset_matchup_edge() -> ModelConfig:
    """Weighted mode: balances form against the opponent matchup."""
    return build_config(
        name="Matchup Edge (Props)", mode="weighted",
        factors={"player_last_n_avg": 40,
                 "opponent_defense": 35,
                 "rest_days": 10,
                 "home_away_split": 15})


if __name__ == "__main__":
    # Demo: run the most-played player through a preset config.
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
    cutoff = hist.iloc[-1]["game_date"]
    opp = hist.iloc[-1]["opponent"]
    cfg = preset_matchup_edge()
    dmap = defense_factors(cutoff)
    ctx = {"opponent_abbr": opp, "defense_factor_map": dmap,
           "upcoming_is_home": bool(hist.iloc[-1]["is_home"])
           if hist.iloc[-1]["is_home"] is not None else None}

    print(f"Config: {cfg.name}  (mode={cfg.mode})")
    print(f"Weights: "
          + ", ".join(f"{k} {w:.0%}" for k, w in cfg.normalized_weights().items()))
    print(f"\nDemo: {name} (id={pid}) — points o/u 25.5 as of {cutoff} vs {opp}\n")
    res = analyze(cfg, pid, "points", 25.5, cutoff, hist, ctx)
    for k, v in res.items():
        if k == "adjustments":
            print("  adjustments:")
            for a in v:
                print(f"    - {a}")
        else:
            print(f"  {k}: {v}")
