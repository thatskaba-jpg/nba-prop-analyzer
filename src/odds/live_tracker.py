"""live_tracker.py — live in-game prop tracking.

Pulls live box scores and, for players who have a prop line posted, shows their
CURRENT stat vs the line and whether they're on pace to go over.

Endpoints (verified live):
  - /nba/v1/box_scores/live   -> every in-progress (or just-final) game with
                                 per-player pts/reb/ast/fg3m/stl/blk + minutes.

Key stays SERVER-SIDE via collect.balldontlie_client._get. Degrades to [].

A regulation NBA game is 48 minutes. "On pace" linearly extrapolates the current
stat by minutes played — crude (benched stars, blowouts, foul trouble all break
it) so we label it as a rough pace, not a projection.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect.balldontlie_client import _get, BallDontLieError  # noqa: E402

GAME_MINUTES = 48.0

# Box-score field per our prop stat key.
_STAT_FIELD = {
    "points": "pts", "rebounds": "reb", "assists": "ast",
    "threes_made": "fg3m", "steals": "stl", "blocks": "blk",
}


def _parse_min(m):
    """Box-score minutes come as 'MM' or 'MM:SS' or int. Return float minutes."""
    if m is None:
        return 0.0
    if isinstance(m, (int, float)):
        return float(m)
    s = str(m).strip()
    if not s:
        return 0.0
    if ":" in s:
        mm, ss = s.split(":")[:2]
        try:
            return int(mm) + int(ss) / 60.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def live_games():
    """Raw live box scores (in-progress or just-final). [] on error."""
    try:
        r = _get("nba/v1/box_scores/live", {})
    except BallDontLieError:
        return []
    return r.get("data", []) if isinstance(r, dict) else []


def _iter_players(game):
    """Yield (team_abbr, player_row) for both teams of a box-score game."""
    for side in ("home_team", "visitor_team"):
        blk = game.get(side) or {}
        abbr = (blk.get("team") or {}).get("abbreviation")
        for p in blk.get("players") or []:
            yield abbr, p


def track(prop_lines):
    """Join live box scores to current prop lines.

    prop_lines: {player_name_key: {stat: line_float}} — the lines we already
    fetch for the slate. player_name_key is the lowercased-alnum name (same
    normalization the props feed uses).

    Returns a list of dicts, one per (player, stat) that has BOTH a live stat
    and a posted line:
      {game, status, player, team, stat, current, line, minutes, on_pace,
       hit (bool|None: already cleared the line), pace_value}
    Sorted by game then player."""
    def _nk(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())

    out = []
    for g in live_games():
        status = g.get("status") or g.get("status_state") or ""
        ht = (g.get("home_team", {}).get("team") or {}).get("abbreviation")
        at = (g.get("visitor_team", {}).get("team") or {}).get("abbreviation")
        matchup = f"{at} @ {ht}" if ht and at else "?"
        final = str(status).lower().startswith("final") \
            or g.get("status_state") == "final"
        for abbr, p in _iter_players(g):
            pr = p.get("player") or {}
            name = f"{pr.get('first_name','')} {pr.get('last_name','')}".strip()
            lines = prop_lines.get(_nk(name))
            if not lines:
                continue
            mins = _parse_min(p.get("min"))
            for stat, field in _STAT_FIELD.items():
                line = lines.get(stat)
                if line is None:
                    continue
                cur = p.get(field)
                if cur is None:
                    continue
                cur = float(cur)
                hit = cur >= line  # already cleared the over
                pace = (cur / mins * GAME_MINUTES) if mins > 0 else cur
                on_pace = (final and hit) or (not final and pace >= line)
                out.append({
                    "game": matchup, "status": status,
                    "player": name, "team": abbr,
                    "stat": stat, "current": cur, "line": line,
                    "minutes": round(mins, 1),
                    "pace_value": round(pace, 1),
                    "hit": hit, "final": final, "on_pace": on_pace,
                })
    out.sort(key=lambda r: (r["game"], r["player"], r["stat"]))
    return out
