"""line_movement.py — opening lines vs current, a sharp-money signal.

When a line moves from where it OPENED, that move is information: it's where the
money (often sharp money) pushed the number. A total that opened 228.5 and sits
at 231.5 now has been bet toward the over. We show open → now beside the current
line so you can see which way the market leaned.

Endpoints (GOAT tier, verified live):
  - /nba/v2/odds/opening            -> opening game ML/spread/total per book
  - /nba/v2/odds/player_props/opening -> opening prop lines per game

Key stays SERVER-SIDE via collect.balldontlie_client._get. Degrades to {}.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect.balldontlie_client import _get, BallDontLieError  # noqa: E402


def opening_game_odds(date_iso):
    """Opening game odds for a date, consensus per game (median across books).
    Returns {game_id: {'spread_home','total','ml_home','ml_away'}} or {}."""
    try:
        r = _get("nba/v2/odds/opening", {"dates[]": date_iso, "per_page": 100})
    except BallDontLieError:
        return {}
    by_game = {}
    for row in r.get("data", []) if isinstance(r, dict) else []:
        gid = row.get("game_id")
        if gid is None:
            continue
        by_game.setdefault(gid, []).append(row)

    def _med(vals):
        vals = sorted(v for v in vals if v is not None)
        return vals[len(vals) // 2] if vals else None

    out = {}
    for gid, rows in by_game.items():
        def _f(key):
            xs = []
            for r2 in rows:
                v = r2.get(key)
                try:
                    xs.append(float(v))
                except (TypeError, ValueError):
                    pass
            return _med(xs)
        out[gid] = {
            "spread_home": _f("spread_home_value"),
            "total": _f("total_value"),
            "ml_home": _f("moneyline_home_odds"),
            "ml_away": _f("moneyline_away_odds"),
        }
    return out


def opening_props_for_game(game_id):
    """Opening prop lines for a game, consensus per (player_id, prop_type):
    {(player_id, prop_type): opening_line_float}. {} on error."""
    try:
        r = _get("nba/v2/odds/player_props/opening", {"game_id": int(game_id)})
    except BallDontLieError:
        return {}
    buckets = {}
    for row in r.get("data", []) if isinstance(r, dict) else []:
        pid = row.get("player_id")
        pt = row.get("prop_type")
        try:
            lv = float(row.get("line_value"))
        except (TypeError, ValueError):
            continue
        if pid is None or pt is None:
            continue
        buckets.setdefault((pid, pt), []).append(lv)
    out = {}
    for k, vals in buckets.items():
        vals.sort()
        out[k] = vals[len(vals) // 2]  # median opening line
    return out


def movement_label(open_v, now_v, kind="total"):
    """Plain-English open->now movement, or '' if no move / missing data.
    kind: 'total' (points), 'spread' (home line), 'line' (prop line)."""
    if open_v is None or now_v is None:
        return ""
    delta = now_v - open_v
    if abs(delta) < 0.5:
        return f"opened {open_v:g} → now {now_v:g} (flat)"
    arrow = "↑" if delta > 0 else "↓"
    return f"opened {open_v:g} → now {now_v:g} ({arrow}{abs(delta):g})"
