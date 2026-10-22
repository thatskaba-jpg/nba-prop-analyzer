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
    out = {}
    for t in teams:
        if not t:
            continue
        out[t.upper()] = {
            "recent_margin": recent_margin(t, cutoff, n),
            "rest_days": rest_days(t, cutoff),
        }
    return out
