"""
goat_data.py — unified client for the verified GOAT balldontlie endpoints.

Every function here was built against a REAL response from your key (field
shapes confirmed live, not guessed). Endpoints covered:

  - season_average(player_id, season)   -> /season_averages   (pts/reb/ast/%/min)
  - advanced_stats(game_id or player)   -> /nba/v2/stats/advanced (PIE, pace,
                                           usage, touches, team-share, matchup)
  - standings(season)                   -> /standings          (records, ranks)

NOT INCLUDED — and why (honest): the tier list names "Player Props", but every
documented route for it (v1/player_props, v1/props, v1/odds/player_props, …)
returns 404 on this key. The endpoint is not reachable, so we do NOT fake prop
odds — the dashboard's value check still takes hand-entered prop odds. The
game-level /odds endpoint works but returns empty away from tip-off.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect.balldontlie_client import _get, BallDontLieError  # noqa: E402


def _get_all(path, params):
    """Fetch EVERY page of a list endpoint, following meta.next_cursor, and
    return the merged list of rows. balldontlie paginates, so a single-page
    read drops data — this guarantees the complete set."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    rows = []
    for _ in range(100):  # hard cap against a bad cursor
        try:
            payload = _get(path, params)
        except BallDontLieError:
            break
        if not isinstance(payload, dict):
            break
        rows.extend(payload.get("data", []) or [])
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return rows


def season_average(player_id: int, season: int) -> dict:
    """Pre-computed season averages for one player (param is singular
    player_id). Returns {} if none (e.g. player hasn't played that season)."""
    rows = _get_all("season_averages",
                    {"season": season, "player_id": int(player_id)})
    return rows[0] if rows else {}


def _min_to_float(m) -> float:
    """balldontlie minutes come as 'MM:SS' or 'MM' or int. Return float minutes."""
    if m is None:
        return 0.0
    try:
        if isinstance(m, (int, float)):
            return float(m)
        s = str(m).strip()
        if ":" in s:
            mm, ss = s.split(":", 1)
            return float(int(mm)) + (float(int(ss)) / 60.0)
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def player_logs(player_id: int, seasons) -> list:
    """Game-by-game stats for ONE player across one or more seasons, LIVE from
    the balldontlie API (the PRIMARY data source), mapped to the local DB
    schema. `seasons` is an int or a list of ints. Period-0 full games only,
    DNPs excluded. Returns a list of dicts ordered oldest-first; [] on error.

    This is the source of truth for a player's history — the local DB is only
    a fallback when the API is unreachable."""
    try:
        season_param = (list(seasons) if isinstance(seasons, (list, tuple, set))
                        else [int(seasons)])
        rows = _get_all("stats",
                        {"seasons[]": season_param, "player_ids[]": int(player_id)})
    except Exception:
        return []
    out = []
    for s in rows:
        g = s.get("game") or {}
        if not g or g.get("date") is None:
            continue
        mins = _min_to_float(s.get("min"))
        if mins <= 0:  # skip DNPs / scratches
            continue
        team_id = (s.get("team") or {}).get("id")
        home_id = g.get("home_team_id")
        is_home = (home_id == team_id) if home_id is not None else None
        out.append({
            "game_date": str(g.get("date"))[:10],
            "season": str(g.get("season") or ""),
            "is_home": (1 if is_home else 0) if is_home is not None else None,
            "opponent": None,
            "matchup": None,
            "game_id": g.get("id"),
            "minutes": mins,
            "points": s.get("pts") or 0,
            "rebounds": s.get("reb") or 0,
            "assists": s.get("ast") or 0,
            "threes_made": s.get("fg3m") or 0,
            "steals": s.get("stl") or 0,
            "blocks": s.get("blk") or 0,
            "turnovers": s.get("turnover") or 0,
            "fouls": s.get("pf") or 0,
        })
    out.sort(key=lambda r: r["game_date"])
    return out


def active_players() -> list:
    """All CURRENTLY ACTIVE NBA players, LIVE from the API. Returns a list of
    {id, name, team} dicts sorted by name. [] on error. This is the source of
    truth for who is playable — not the local DB, which only knows players who
    already have logged games in it."""
    try:
        rows = _get_all("players/active", {})
    except Exception:
        return []
    out = []
    for p in rows:
        fn = (p.get("first_name") or "").strip()
        ln = (p.get("last_name") or "").strip()
        name = f"{fn} {ln}".strip()
        if not name or p.get("id") is None:
            continue
        out.append({"id": int(p["id"]), "name": name,
                    "team": (p.get("team") or {}).get("abbreviation")})
    out.sort(key=lambda r: r["name"])
    return out


def current_season_logs(player_id: int, season: int) -> list:
    """This season's games, LIVE from the API. Thin wrapper over player_logs."""
    return player_logs(player_id, season)


def advanced_stats(game_id: int = None, player_id: int = None,
                   season: int = None) -> list:
    """Advanced stats from the V2 endpoint (nba/v2/stats/advanced): PIE, pace,
    usage %, rebound %, eFG%, A:TO, touches, share of team points/shots, and
    matchup-defense fields. Filter by game_id, or player_id + season. Returns a
    list of rows (ALL pages).

    NOTE: V2 rows carry a `period` field (0 = full game, 1-4 = quarters,
    5-7 = OT). Callers that want per-GAME numbers must keep only period == 0
    rows (see player_advanced_avg), or they'll mix full-game and per-quarter
    rows and corrupt any average."""
    params = {"per_page": 100}
    if game_id is not None:
        params["game_ids[]"] = game_id
    if player_id is not None:
        params["player_ids[]"] = player_id
    if season is not None:
        params["seasons[]"] = season
    return _get_all("nba/v2/stats/advanced", params)


_ADV_AVG_CACHE: dict = {}


def player_advanced_avg(player_id: int, season: int) -> dict:
    """Season-average advanced stats for ONE player: usage%, assist%, PIE,
    true-shooting%. Averaged over the player's games that season.

    Returns {} if the player has no advanced rows that season. Cached per
    (player_id, season) in-process — a tight loop (e.g. weighting many injured
    teammates) fetches each player only once. Field names verified live:
    usage_percentage, assist_percentage, pie, true_shooting_percentage.
    """
    key = (int(player_id), int(season))
    if key in _ADV_AVG_CACHE:
        return _ADV_AVG_CACHE[key]
    rows = advanced_stats(player_id=player_id, season=season)
    # V2 returns per-period rows too; keep FULL-GAME rows only (period 0, or
    # rows with no period field for forward-compat) so averages aren't mixed.
    rows = [r for r in rows if r.get("period", 0) in (0, None)]

    def _avg(field):
        vals = [r.get(field) for r in rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    out = {} if not rows else {
        "usage": _avg("usage_percentage"),
        "assist_pct": _avg("assist_percentage"),
        "pie": _avg("pie"),
        "ts_pct": _avg("true_shooting_percentage"),
        # Extra V2 advanced signals surfaced in the Extra-context panel.
        "reb_pct": _avg("rebound_percentage"),
        "efg_pct": _avg("effective_field_goal_percentage"),
        "ast_to": _avg("assist_to_turnover"),
        "pace": _avg("pace"),
        "poss": _avg("possessions"),
        "touches": _avg("touches"),
        "pct_pts": _avg("pct_points"),      # share of team's points
        "pct_fga": _avg("pct_fga"),         # share of team's shot attempts
        # Matchup DEFENSE — what the player allows to who he guards.
        "matchup_pts": _avg("matchup_player_points"),
        "matchup_fg_pct": _avg("matchup_fg_pct"),
        "matchup_3pt_pct": _avg("matchup_3pt_pct"),
        "matchup_ast": _avg("matchup_assists"),
        # Shot quality — how good his looks are.
        "contested_fg_pct": _avg("contested_fg_pct"),
        "uncontested_fg_pct": _avg("uncontested_fg_pct"),
        "rim_fg_pct": _avg("defended_at_rim_fg_pct"),
        # Raw ATTEMPT + PASS volume (counts, per game) — not just percentages.
        "contested_fga": _avg("contested_fga"),
        "uncontested_fga": _avg("uncontested_fga"),
        "rim_fga": _avg("defended_at_rim_fga"),
        "matchup_fga": _avg("matchup_fga"),       # shots he faces on defense
        "matchup_3pa": _avg("matchup_3pa"),
        # Playmaking volume.
        "passes": _avg("passes"),
        "secondary_ast": _avg("secondary_assists"),
        # Defense & hustle — the gritty stuff box scores miss.
        "matchup_tov": _avg("matchup_turnovers"),     # TOs he forces on his man
        "deflections": _avg("deflections"),
        "charges_drawn": _avg("charges_drawn"),
        "contested_shots": _avg("contested_shots"),
        "box_outs": _avg("box_outs"),
        "screen_ast": _avg("screen_assists"),
        "fouls_drawn": _avg("fouls_drawn"),
        "pct_steals": _avg("pct_steals"),
        "pct_blocks": _avg("pct_blocks"),
        # Scoring profile — where his points come from.
        "pct_pts_paint": _avg("pct_pts_paint"),
        "pct_pts_3pt": _avg("pct_pts_3pt"),
        "pct_pts_ft": _avg("pct_pts_free_throw"),
        "games": len(rows),
    }
    _ADV_AVG_CACHE[key] = out
    return out


def standings(season: int) -> list:
    """Team standings for a season: wins/losses, home/road, conference ranks."""
    return _get_all("standings", {"season": season})


_SHOOT_SPLITS_CACHE: dict = {}

# Distance buckets at/behind the NBA three-point line (22-23.75 ft). The 5ft_range
# shooting splits bucket by raw distance, so 3PT volume ≈ these buckets summed.
_THREE_RANGE = ("20-24", "25-29", "30-34", "35-39", "40+")


def shooting_splits(player_id: int, season: int) -> dict:
    """Shooting splits by distance for ONE player-season, from
    season_averages/shooting type=5ft_range. Returns the raw stats dict
    (keys like '25-29_ft._fga'), or {} if unavailable. Cached per player-season.

    Verified live: category 'shooting', type '5ft_range' yields the distance
    buckets; the 20-24ft bucket straddles the arc, so three-point volume from
    these buckets is approximate (good enough for a context note, not a model
    input)."""
    key = (int(player_id), int(season))
    if key in _SHOOT_SPLITS_CACHE:
        return _SHOOT_SPLITS_CACHE[key]
    try:
        r = _get("nba/v1/season_averages/shooting",
                 {"player_ids[]": int(player_id), "season": int(season),
                  "season_type": "regular", "type": "5ft_range"})
        rows = r.get("data", [])
        out = rows[0].get("stats", {}) if rows else {}
    except Exception:
        out = {}
    _SHOOT_SPLITS_CACHE[key] = out
    return out


def expected_threes(player_id: int, season: int) -> dict:
    """Roughly how many threes a player takes/makes per game, from shooting
    splits. Returns {'fga_3','fgm_3','fg3_pct'} or {} if no data. Approximate
    (the 20-24ft bucket straddles the arc) — for a 3PM-prop context note."""
    s = shooting_splits(player_id, season)
    if not s:
        return {}
    fga = sum(s.get(f"{b}_ft._fga", 0) or 0 for b in _THREE_RANGE)
    fgm = sum(s.get(f"{b}_ft._fgm", 0) or 0 for b in _THREE_RANGE)
    if fga <= 0:
        return {}
    return {"fga_3": round(fga, 1), "fgm_3": round(fgm, 1),
            "fg3_pct": round(fgm / fga, 3)}


def lineups(game_id: int) -> list:
    """Starting lineups for a game (posts near tip-off; empty before that).

    Route verified real (/lineups with game_ids[]). Returns [] when lineups
    aren't posted yet — we never fabricate a lineup."""
    return _get_all("lineups", {"game_ids[]": game_id})


def lineup_for_team(game_id: int, team_abbr: str) -> dict:
    """Confirmed starters/bench for ONE team in a game, from the lineups feed.

    Returns {'starters': [names], 'bench': [names]} using only rows whose team
    abbreviation matches `team_abbr` — so a scrambled/placeholder feed for OTHER
    teams can't leak in. Empty lists when lineups aren't posted yet. Honest: we
    report exactly what the feed says for this team, nothing inferred."""
    starters, bench = [], []
    for r in lineups(game_id):
        t = (r.get("team") or {}).get("abbreviation")
        if t != team_abbr:
            continue
        p = r.get("player") or {}
        nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if not nm:
            continue
        (starters if r.get("starter") else bench).append(nm)
    return {"starters": starters, "bench": bench}


def starters_detailed(game_id: int, team_abbr: str) -> list:
    """Starters for one team with position + player_id, for matchup lookups.
    Returns [{'name', 'position', 'player_id'}, ...] (empty before lineups post).
    Position is the lineup slot ('G'/'F'/'C'); player_id is balldontlie's."""
    out = []
    for r in lineups(game_id):
        t = (r.get("team") or {}).get("abbreviation")
        if t != team_abbr or not r.get("starter"):
            continue
        p = r.get("player") or {}
        nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if nm:
            out.append({"name": nm,
                        "position": (r.get("position")
                                     or p.get("position") or "").upper(),
                        "player_id": p.get("id")})
    return out


# NOTE: /teams/season_averages exists (returns 400, not 404) but its required
# param format for the team id was not resolvable from probing (rejects both
# `team_id` and `id`). Team matchup context is already covered by standings_map
# + opponent-defense factors, so this is left unwired rather than guessed at.


def standings_map(season: int) -> dict:
    """{team_abbr: {wins, losses, win_pct, conf_rank}} for quick lookup."""
    out = {}
    for s in standings(season):
        team = (s.get("team") or {}).get("abbreviation")
        if not team:
            continue
        w, l = s.get("wins", 0), s.get("losses", 0)
        out[team] = {"wins": w, "losses": l,
                     "win_pct": round(w / (w + l), 3) if (w + l) else None,
                     "conf_rank": s.get("conference_rank")}
    return out


def team_ratings(season: int) -> list:
    """Team advanced season averages: off/def/net rating + pace, with built-in
    league ranks. Uses /nba/v1/team_season_averages/general?type=advanced."""
    return _get_all("team_season_averages/general",
                    {"season": season, "season_type": "regular",
                     "type": "advanced", "per_page": 100})


_TEAM_RATINGS_CACHE: dict = {}


def team_ratings_map(season: int) -> dict:
    """{team_abbr: {off, def, net, pace, off_rank, def_rank}} from balldontlie.

    Early in a season the current year has only a few teams, so if fewer than a
    near-full league comes back we fall back to last season (complete, still a
    good read of team quality).

    Cached in-process per season: team ratings don't change within a run, so a
    tight loop (e.g. the calibration fit over hundreds of thousands of graded
    lines) fetches each season only ONCE instead of once per row — the single
    biggest speedup for batch jobs that call this repeatedly.
    """
    if season in _TEAM_RATINGS_CACHE:
        return _TEAM_RATINGS_CACHE[season]

    def _build(rows):
        out = {}
        for r in rows:
            ab = (r.get("team") or {}).get("abbreviation")
            st = r.get("stats") or {}
            if not ab:
                continue
            out[ab] = {
                "off": st.get("off_rating"), "def": st.get("def_rating"),
                "net": st.get("net_rating"), "pace": st.get("pace"),
                "off_rank": st.get("off_rating_rank"),
                "def_rank": st.get("def_rating_rank"),
            }
        return out

    cur = _build(team_ratings(season))
    if len(cur) < 25:
        prior = _build(team_ratings(season - 1))
        if len(prior) > len(cur):
            _TEAM_RATINGS_CACHE[season] = prior
            return prior
    _TEAM_RATINGS_CACHE[season] = cur
    return cur


_TEAM_FF_CACHE: dict = {}


def team_four_factors_map(season: int) -> dict:
    """{team_abbr: {efg, tov_pct, oreb_pct, dreb_pct, ts, efg_rank, tov_rank,
    oreb_rank}} — Dean Oliver's Four Factors (the FT-rate factor isn't exposed
    by this endpoint; eFG/TOV/OREB are the three heaviest and ARE here).

    Same source rows as team_ratings (team advanced), same early-season
    fallback to last year when the current season is too thin."""
    if season in _TEAM_FF_CACHE:
        return _TEAM_FF_CACHE[season]

    def _build(rows):
        out = {}
        for r in rows:
            ab = (r.get("team") or {}).get("abbreviation")
            st = r.get("stats") or {}
            if not ab:
                continue
            out[ab] = {
                "efg": st.get("efg_pct"),
                "tov_pct": st.get("tm_tov_pct"),
                "oreb_pct": st.get("oreb_pct"),
                "dreb_pct": st.get("dreb_pct"),
                "ts": st.get("ts_pct"),
                "efg_rank": st.get("efg_pct_rank"),
                "tov_rank": st.get("tm_tov_pct_rank"),
                "oreb_rank": st.get("oreb_pct_rank"),
            }
        return out

    cur = _build(team_ratings(season))
    if len(cur) < 25:
        prior = _build(team_ratings(season - 1))
        if len(prior) > len(cur):
            _TEAM_FF_CACHE[season] = prior
            return prior
    _TEAM_FF_CACHE[season] = cur
    return cur


_TEAM_STYLE_CACHE: dict = {}


def team_style_map(season: int, category: str, type_: str) -> dict:
    """Team style data for a category/type, keyed by team abbr -> stats dict.

    category: 'playtype' (type: isolation, prballhandler, prrollman, spotup,
    postup, handoff, cut, offscreen, transition, offrebound, misc) or
    'tracking' (type: drives, painttouch, catchshoot, pullupshot, passing,
    posttouch, elbowtouch, rebounding, possessions, efficiency, speeddistance).

    Cached per (season, category, type). Early-season fallback to last year."""
    key = (season, category, type_)
    if key in _TEAM_STYLE_CACHE:
        return _TEAM_STYLE_CACHE[key]

    def _build(yr):
        rows = _get_all(f"team_season_averages/{category}",
                        {"season": yr, "season_type": "regular",
                         "type": type_, "per_page": 100})
        out = {}
        for r in rows:
            ab = (r.get("team") or {}).get("abbreviation")
            if ab:
                out[ab] = r.get("stats") or {}
        return out

    cur = _build(season)
    if len(cur) < 25:
        prior = _build(season - 1)
        if len(prior) > len(cur):
            _TEAM_STYLE_CACHE[key] = prior
            return prior
    _TEAM_STYLE_CACHE[key] = cur
    return cur


if __name__ == "__main__":
    print("Season average (player 237, 2025):",
          {k: season_average(237, 2025).get(k) for k in ("pts", "reb", "ast")})
    adv = advanced_stats(player_id=237, season=2025)
    print(f"Advanced rows for player 237: {len(adv)}",
          ({k: adv[0].get(k) for k in ("pie", "pace", "usage_percentage")}
           if adv else ""))
    sm = standings_map(2025)
    print(f"Standings loaded: {len(sm)} teams. DET:", sm.get("DET"))
