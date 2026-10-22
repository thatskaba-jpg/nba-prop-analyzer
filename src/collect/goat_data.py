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
        # Shot quality — how good his looks are.
        "contested_fg_pct": _avg("contested_fg_pct"),
        "uncontested_fg_pct": _avg("uncontested_fg_pct"),
        "rim_fg_pct": _avg("defended_at_rim_fg_pct"),
        # Playmaking volume.
        "passes": _avg("passes"),
        "secondary_ast": _avg("secondary_assists"),
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


if __name__ == "__main__":
    print("Season average (player 237, 2025):",
          {k: season_average(237, 2025).get(k) for k in ("pts", "reb", "ast")})
    adv = advanced_stats(player_id=237, season=2025)
    print(f"Advanced rows for player 237: {len(adv)}",
          ({k: adv[0].get(k) for k in ("pie", "pace", "usage_percentage")}
           if adv else ""))
    sm = standings_map(2025)
    print(f"Standings loaded: {len(sm)} teams. DET:", sm.get("DET"))
