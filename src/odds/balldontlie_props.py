"""
balldontlie_props.py — player prop odds from balldontlie (v2/odds/player_props).

This is the PRIMARY prop-odds source. balldontlie player props are included on
the GOAT tier (the endpoint is v2/odds/player_props?game_id=XXX — the earlier
"404" was simply the wrong path). Advantages over the SportsGameOdds backup:

  - Keyed by balldontlie PLAYER_ID, the SAME id in our game logs, so matching is
    exact — no normalized-name guessing.
  - One API we already pay for and trust for every other feed.
  - 600 req/min on GOAT, so a full slate (one call per game) is comfortable.

Endpoints (verified live on the GOAT key):
  games today   GET /v1/games?dates[]=YYYY-MM-DD
  player props  GET /v2/odds/player_props?game_id=ID
                -> data[]{player_id, prop_type, line_value,
                          market:{type:'over_under', over_odds, under_odds}}

We return the SAME shape the dashboard already consumes from the SGO client:
  {name_key: {our_prop_type: {line, odds_over, odds_under, books}}}
keyed by normalized player NAME (so the existing name-based lookups keep working),
with the name resolved from our DB via player_id. Over/under markets only
(milestone markets like "30+ points" are a different bet and are skipped).
"""

import os
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

from database import get_connection  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_KEY = os.getenv("BALLDONTLIE_API_KEY", "").strip()
_BASE = "https://api.balldontlie.io"

# balldontlie prop_type -> our prop_type. Only the ones we model; the rest
# (quarter/first-3-min/milestone-only) are ignored.
_PROP_MAP = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "threes": "threes_made", "steals": "steals", "blocks": "blocks",
    "points_rebounds_assists": "pra", "points_rebounds": "pr",
    "points_assists": "pa", "rebounds_assists": "ra",
}


class BDLPropsError(Exception):
    pass


def has_key() -> bool:
    return bool(_KEY)


def _get(path, params):
    if not _KEY:
        raise BDLPropsError("No BALLDONTLIE_API_KEY in .env")
    r = requests.get(f"{_BASE}/{path}", params=params,
                     headers={"Authorization": _KEY}, timeout=30)
    if r.status_code == 429:
        raise BDLPropsError("rate limit")
    if r.status_code != 200:
        raise BDLPropsError(f"HTTP {r.status_code}: {r.text[:120]}")
    return r.json()


def _get_all(path, params):
    """Fetch EVERY page of a list endpoint, following meta.next_cursor.

    balldontlie paginates (default/max per_page, 25 on some v2 odds routes), so
    reading a single page silently drops most of a slate's data — the bug that
    hid 8 of 11 games' lines and cut off player props. This keeps asking for the
    next page until there isn't one, so we always get the COMPLETE set."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    rows = []
    for _ in range(100):  # hard cap so a bad cursor can't loop forever
        payload = _get(path, params)
        rows.extend(payload.get("data", []) or [])
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return rows


def _name_key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _todays_game_ids():
    today = date.today().isoformat()
    rows = _get_all("v1/games", {"dates[]": today, "per_page": 100})
    return [g["id"] for g in rows]


def _id_to_name():
    """balldontlie player_id -> our DB player name (for name-keyed output)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT player_id, player_name FROM player_game_logs").fetchall()
    conn.close()
    return {r["player_id"]: r["player_name"] for r in rows}


def all_player_props():
    """Every player's over/under prop lines for today, as
    {name_key: {prop_type: {line, odds_over, odds_under, books}}}.

    Raises BDLPropsError('rate limit') on a 429 so the dashboard can show the
    same 'wait a minute' message it uses for the SGO path.
    """
    if not _KEY:
        return {}
    id_name = _id_to_name()
    # player_id -> prop_type -> {lines, over, under, books}
    acc = defaultdict(lambda: defaultdict(
        lambda: {"lines": [], "over": [], "under": [], "books": set()}))
    for gid in _todays_game_ids():
        for p in _get_all("v2/odds/player_props", {"game_id": gid}):
            prop = _PROP_MAP.get(p.get("prop_type"))
            if not prop:
                continue
            mkt = p.get("market") or {}
            if mkt.get("type") != "over_under":
                continue  # skip milestone markets
            pid = p.get("player_id")
            if pid is None:
                continue
            b = acc[pid][prop]
            try:
                b["lines"].append(float(p["line_value"]))
            except (TypeError, ValueError):
                pass
            if mkt.get("over_odds") is not None:
                b["over"].append(int(mkt["over_odds"]))
            if mkt.get("under_odds") is not None:
                b["under"].append(int(mkt["under_odds"]))
            b["books"].add(p.get("vendor"))

    out = {}
    for pid, props in acc.items():
        name = id_name.get(pid)
        if not name:
            continue  # player not in our DB — can't match to history
        nk = _name_key(name)
        for prop, b in props.items():
            if not b["lines"]:
                continue
            out.setdefault(nk, {})[prop] = {
                "line": round(statistics.median(b["lines"]) * 2) / 2,
                "odds_over": (int(statistics.median(b["over"]))
                              if b["over"] else None),
                "odds_under": (int(statistics.median(b["under"]))
                               if b["under"] else None),
                "books": len([x for x in b["books"] if x]),
            }
    return out


def player_prop_odds(player_name: str, prop_type: str):
    """Single player's prop from the bulk pull (same matching as the scanner)."""
    if prop_type not in _PROP_MAP.values() or not _KEY:
        return None
    try:
        allp = all_player_props()
    except BDLPropsError:
        return None
    return (allp.get(_name_key(player_name)) or {}).get(prop_type)


def todays_player_keys() -> set:
    try:
        return set(all_player_props().keys())
    except BDLPropsError:
        return set()


if __name__ == "__main__":
    if not has_key():
        print("No BALLDONTLIE_API_KEY in .env")
        sys.exit(0)
    allp = all_player_props()
    print(f"players with props: {len(allp)}")
    for nm in list(allp)[:5]:
        print(f"  {nm}: {list(allp[nm].keys())}")
