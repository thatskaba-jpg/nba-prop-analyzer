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


def _resolve_names_via_api(player_ids):
    """Resolve balldontlie player_ids -> 'First Last' via the players endpoint.

    The props feed carries only player_id (no name), and rookies / recent
    call-ups have ids that aren't in our game-log DB yet — so they'd be dropped.
    This looks those ids up directly so their posted props still surface.
    Batched (balldontlie accepts repeated player_ids[]), best-effort: any id it
    can't resolve is simply skipped."""
    out = {}
    ids = [i for i in player_ids if i is not None]
    if not ids:
        return out
    # Chunk to keep each query small; the endpoint takes repeated player_ids[].
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            rows = _get_all("v1/players",
                            {"player_ids[]": chunk, "per_page": 100})
        except Exception:
            continue
        for p in rows:
            nm = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            if p.get("id") is not None and nm:
                out[p["id"]] = nm
    return out


def _todays_game_ids(date_iso=None):
    try:
        from database import nba_today
        today = date_iso or nba_today()
    except Exception:
        today = date_iso or date.today().isoformat()
    rows = _get_all("v1/games", {"dates[]": today, "per_page": 100})
    return [g["id"] for g in rows]


def games_on_date(date_iso):
    """Scheduled NBA games for a date: [{matchup, home, away, status, time}].
    Pure schedule (no odds) — works for any date the league has games. []
    on error."""
    try:
        rows = _get_all("v1/games", {"dates[]": date_iso, "per_page": 100})
    except Exception:
        return []
    out = []
    for g in rows:
        h = (g.get("home_team") or {}).get("abbreviation")
        a = (g.get("visitor_team") or {}).get("abbreviation")
        if not h or not a:
            continue
        out.append({
            "matchup": f"{a} @ {h}", "home": h, "away": a,
            "status": (g.get("status") or "").strip(),
            "time": g.get("status") if ":" in str(g.get("status")) else None,
        })
    return out


def _id_to_name():
    """balldontlie player_id -> our DB player name (for name-keyed output)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT player_id, player_name FROM player_game_logs").fetchall()
    conn.close()
    return {r["player_id"]: r["player_name"] for r in rows}


def _all_player_props_uncached(date_iso=None):
    """Every player's over/under prop lines for a date (default today), as
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
    # Fetch every game's props in PARALLEL (each is its own paginated call;
    # doing 12 games one after another was ~9 seconds).
    from concurrent.futures import ThreadPoolExecutor
    gids = _todays_game_ids(date_iso)
    fetched = []
    if gids:
        with ThreadPoolExecutor(max_workers=min(6, len(gids))) as ex:
            fetched = list(ex.map(
                lambda g: _get_all("v2/odds/player_props", {"game_id": g}),
                gids))
    for rows in fetched:
        for p in rows:
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
    # Resolve any prop player_ids that aren't in our game-log DB (rookies /
    # recent call-ups) via the players endpoint, so their posted props still
    # show up instead of being silently dropped.
    unknown_ids = [pid for pid in acc if pid not in id_name]
    api_names = _resolve_names_via_api(unknown_ids) if unknown_ids else {}

    for pid, props in acc.items():
        name = id_name.get(pid) or api_names.get(pid)
        if not name:
            continue  # couldn't resolve a name at all — nothing to key on
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


_PROPS_CACHE = {}
_PROPS_TTL = 60    # seconds: live prices; 3 callers share one pull per minute


def all_player_props(date_iso=None):
    """Every player's over/under prop lines for a date (cached 5 min so the
    Analyze tab's day list and the Value Board share ONE pull)."""
    import time as _t
    key = date_iso or ""
    hit = _PROPS_CACHE.get(key)
    if hit and _t.time() - hit[0] < _PROPS_TTL:
        return hit[1]
    out = _all_player_props_uncached(date_iso)
    if out:                      # never cache an empty/failed pull
        _PROPS_CACHE[key] = (_t.time(), out)
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
