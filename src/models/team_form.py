"""
team_form.py — recent, game-specific TEAM signals for the game model.

The game model used to lean almost entirely on SEASON-AVERAGE off/def ratings,
which makes every projection bland and timid: it can't tell a team that's red-
hot from one that's been coasting, and it treats a rested team the same as one
on the second night of a back-to-back. This module adds the missing signals,
all reconstructed LEAK-FREE from the local game logs (no new API load):

  - recent_margin(team, cutoff, n): average point margin over the team's last n
    games before `cutoff` (hot/cold form the season average misses).
  - rest_days(team, cutoff): days since the team's last game; flags a
    back-to-back (fatigue) vs. extra rest.

Scores are rebuilt from player_game_logs: each game row carries the player's
team (matchup) and points, so summing points per (game_id, team) gives both
teams' scores, hence the margin. De-duplicated per game.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

# Default home edge for teams we have no split for (league-average ~2.2 pts).
_DEFAULT_HOME_EDGE = 2.2


def home_edge_map(season=2026, prior_season=2025):
    """Per-team HOME-COURT value in points: how much better a team plays at
    home than on the road, from REAL home/road margin splits in the game logs.
    A good home team (POR +3.4) gets more; a poor one (BOS -0.3, PHI -1.0) gets
    little or none — replacing the old flat +2.5 for every team.

    Built from the prior full season (82-game sample) because the new season is
    far too thin, then nudged by this season's splits as they accumulate. Values
    are clamped to a sane [-1.0, 4.0] band. Returns {ABBR: edge_pts}."""
    def _splits(conn, s):
        rows = conn.execute(
            "SELECT game_id, substr(matchup,1,3) AS team, is_home, "
            "SUM(points) AS pts FROM player_game_logs "
            "WHERE season=? AND matchup IS NOT NULL "
            "GROUP BY game_id, team, is_home", (str(s),)).fetchall()
        from collections import defaultdict
        games = defaultdict(dict)
        for gid, team, ih, pts in rows:
            games[gid][team] = (ih, pts or 0)
        hm, rm = defaultdict(list), defaultdict(list)
        for teams in games.values():
            if len(teams) != 2:
                continue
            (t1, (h1, p1)), (t2, (h2, p2)) = list(teams.items())
            if h1 == h2:
                continue
            if h1:
                hm[t1].append(p1 - p2); rm[t2].append(p2 - p1)
            else:
                hm[t2].append(p2 - p1); rm[t1].append(p1 - p2)
        return hm, rm

    conn = get_connection()
    try:
        phm, prm = _splits(conn, prior_season)   # full prior season
        chm, crm = _splits(conn, season)         # thin current season
    finally:
        conn.close()
    import statistics as _st
    teams = set(phm) | set(prm) | set(chm) | set(crm)
    out = {}
    for t in teams:
        # Prior-season home edge = half the home-minus-road margin swing.
        if phm.get(t) and prm.get(t):
            prior = (_st.mean(phm[t]) - _st.mean(prm[t])) / 2.0
        else:
            prior = _DEFAULT_HOME_EDGE
        # Current-season edge if we have both sides; else fall back to prior.
        if chm.get(t) and crm.get(t):
            cur = (_st.mean(chm[t]) - _st.mean(crm[t])) / 2.0
            n = min(len(chm[t]), len(crm[t]))
            w = n / (n + 10.0)   # games-weighted blend toward current
            edge = w * cur + (1 - w) * prior
        else:
            edge = prior
        out[t] = round(max(-1.0, min(4.0, edge)), 2)
    return out


def record_strength_map(season=2026, prior_season=2025):
    """Per-team REAL-RECORD strength in margin points, from actual wins/losses
    in the game logs. A team's record is the single cleanest 'are they good'
    signal once games exist. Win% is centered at 0.5 and scaled to points
    (70% team -> +~2.4, 30% -> -~2.4), and the gap feeds the margin.

    The new season is far too thin, so we blend the prior FULL season with the
    current season by games played (w_cur = g/(g+10)); early on it's almost all
    prior-season record, shifting to this year's as real games pile up. Returns
    {ABBR: strength_pts}."""
    def _winpct(conn, s):
        rows = conn.execute(
            "SELECT game_id, substr(matchup,1,3) AS team, SUM(points) AS pts "
            "FROM player_game_logs WHERE season=? AND matchup IS NOT NULL "
            "GROUP BY game_id, team", (str(s),)).fetchall()
        from collections import defaultdict
        games = defaultdict(dict)
        for gid, team, pts in rows:
            games[gid][team] = pts or 0
        wins, tot = defaultdict(int), defaultdict(int)
        for teams in games.values():
            if len(teams) != 2:
                continue
            (t1, p1), (t2, p2) = list(teams.items())
            tot[t1] += 1; tot[t2] += 1
            if p1 > p2:
                wins[t1] += 1
            elif p2 > p1:
                wins[t2] += 1
        return {t: (wins[t] / tot[t], tot[t]) for t in tot}

    conn = get_connection()
    try:
        prior = _winpct(conn, prior_season)
        cur = _winpct(conn, season)
    finally:
        conn.close()
    out = {}
    for t in set(prior) | set(cur):
        pw = prior.get(t, (0.5, 0))[0]
        cw, cn = cur.get(t, (0.5, 0))
        w = cn / (cn + 10.0)                 # weight toward current season
        wp = w * cw + (1 - w) * pw
        out[t] = round((wp - 0.5) * 12.0, 2)   # center at 0.5, scale to points
    return out


def _games_by_team(conn, cutoff):
    """{TEAM: [(game_date, margin), ...]} for EVERY team, oldest first, strictly
    before cutoff. One pass over the game log. (team_form_map used to rescan the
    whole table twice per team, ~50 full scans for a 12-game slate.)"""
    rows = conn.execute(
        """
        SELECT game_id, game_date,
               SUBSTR(matchup, 1, INSTR(matchup, ' ') - 1) AS team_abbr,
               SUM(points) AS pts
        FROM player_game_logs
        WHERE game_date < ? AND minutes > 0
        GROUP BY game_id, team_abbr
        """,
        (cutoff,)).fetchall()
    by_game = {}
    for r in rows:
        slot = by_game.setdefault(r["game_id"], {"date": r["game_date"], "pts": {}})
        if r["team_abbr"]:
            slot["pts"][r["team_abbr"].upper()] = r["pts"] or 0
    out = {}
    for g in by_game.values():
        pts = g["pts"]
        if len(pts) != 2:
            continue                      # need both sides to form a margin
        (a, pa), (b, pb) = pts.items()
        out.setdefault(a, []).append((g["date"], pa - pb))
        out.setdefault(b, []).append((g["date"], pb - pa))
    for games in out.values():
        games.sort(key=lambda x: x[0])
    return out


def _team_games(conn, team, cutoff):
    """Return [(game_date, margin), ...] for `team`, oldest first, STRICTLY
    before cutoff. margin = team points - opponent points for that game.
    Rebuilt from player_game_logs by summing each side's points per game."""
    rows = conn.execute(
        """
        SELECT game_id, game_date,
               SUBSTR(matchup, 1, INSTR(matchup, ' ') - 1) AS team_abbr,
               SUM(points) AS pts
        FROM player_game_logs
        WHERE game_date < ? AND minutes > 0
        GROUP BY game_id, team_abbr
        """,
        (cutoff,)).fetchall()
    # game_id -> {team_abbr: pts, date}
    by_game = {}
    for r in rows:
        gid = r["game_id"]
        slot = by_game.setdefault(gid, {"date": r["game_date"], "pts": {}})
        if r["team_abbr"]:
            slot["pts"][r["team_abbr"].upper()] = r["pts"] or 0
    out = []
    tu = team.upper()
    for gid, g in by_game.items():
        pts = g["pts"]
        if tu not in pts or len(pts) != 2:
            continue  # need both sides to form a margin
        opp = next(t for t in pts if t != tu)
        out.append((g["date"], (pts[tu] or 0) - (pts[opp] or 0)))
    out.sort(key=lambda x: x[0])
    return out


def recent_margin(team, cutoff, n=10):
    """Average point margin over the team's last n games before cutoff, or None
    if we have no games. A simple, robust 'recent form' signal: positive means
    the team has been beating people, negative means losing."""
    if not team:
        return None
    conn = get_connection()
    try:
        games = _team_games(conn, team, cutoff)
    finally:
        conn.close()
    if not games:
        return None
    last = [m for _, m in games[-n:]]
    return round(sum(last) / len(last), 2) if last else None


def rest_days(team, cutoff):
    """Days between the team's most recent game and the cutoff date, or None.
    0-1 days = back-to-back (tired); 3+ = well rested."""
    if not team:
        return None
    conn = get_connection()
    try:
        games = _team_games(conn, team, cutoff)
    finally:
        conn.close()
    if not games:
        return None
    import datetime as _dt
    try:
        last = _dt.date.fromisoformat(games[-1][0][:10])
        cut = _dt.date.fromisoformat(cutoff[:10])
        return (cut - last).days
    except Exception:
        return None


def team_form_map(teams, cutoff, n=10):
    """{team: {'recent_margin':..., 'rest_days':...}} for a set of teams, in one
    pass. Used by the game model / scoreboard to pass form into project_game."""
    import datetime as _dt
    conn = get_connection()
    try:
        by_team = _games_by_team(conn, cutoff)      # ONE scan for all the teams
    finally:
        conn.close()
    out = {}
    for t in teams:
        if not t:
            continue
        games = by_team.get(t.upper(), [])
        margin = rest = None
        if games:
            last = [m for _, m in games[-n:]]
            margin = round(sum(last) / len(last), 2) if last else None
            try:
                rest = (_dt.date.fromisoformat(cutoff[:10])
                        - _dt.date.fromisoformat(games[-1][0][:10])).days
            except Exception:
                rest = None
        out[t.upper()] = {"recent_margin": margin, "rest_days": rest}
    return out
