"""
prev_starters.py — who started each team's most recent completed game.

Coaches mostly repeat their starting five, so "same as last game" (minus anyone
ruled out) is a far better projected lineup than guessing from minutes. Pure
logic with the two fetches injected, so it is unit-tested.
"""
import datetime as _d
from concurrent.futures import ThreadPoolExecutor


def _name(row):
    p = row.get("player") or {}
    return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()


def prev_starters(day_iso, get_games, get_lineups, lookback=6, workers=8):
    """{TEAM: [starter names]} from each team's latest FINAL game before
    `day_iso`. get_games(date_iso) -> list of game dicts (balldontlie v1);
    get_lineups(game_id) -> list of lineup rows. Teams with no completed game
    in the window are absent."""
    last_game = {}
    base = _d.date.fromisoformat(day_iso)
    for k in range(1, lookback + 1):
        day = (base - _d.timedelta(days=k)).isoformat()
        try:
            games = get_games(day) or []
        except Exception:
            continue
        for g in games:
            if str(g.get("status") or "").strip().lower() != "final":
                continue
            for key in ("home_team", "visitor_team"):
                ab = ((g.get(key) or {}).get("abbreviation") or "").upper()
                if ab and ab not in last_game:      # first hit = most recent
                    last_game[ab] = g["id"]
    gids = sorted(set(last_game.values()))
    if not gids:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(gids))) as ex:
        rows_by_game = dict(zip(gids, ex.map(lambda x: get_lineups(x) or [], gids)))
    out = {}
    for team, gid in last_game.items():
        names = [_name(r) for r in rows_by_game.get(gid, [])
                 if ((r.get("team") or {}).get("abbreviation") or "").upper() == team
                 and r.get("starter") and _name(r)]
        if names:
            out[team] = names
    return out
