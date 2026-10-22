"""
sportsgameodds_client.py — pull REAL NBA player-prop lines + odds (free tier).

balldontlie (even on GOAT) does not expose player-prop odds — its prop route
404s. SportsGameOdds does, on its FREE tier: /v2/events returns props for 85+
sportsbooks. This client fetches a player's prop for a chosen stat and returns a
CONSENSUS line and over/under price, which the dashboard uses to auto-fill its
odds boxes (you can still override by hand).

Verified from SportsGameOdds docs:
  base   https://api.sportsgameodds.com/v2
  auth   header  x-api-key: <KEY>
  events GET /events?leagueID=NBA&finalized=false&oddsAvailable=true
  a prop odd carries: statEntityID (player id like 'LEBRON_JAMES_1_NBA'),
    statID (points/rebounds/…), sideID ('over'/'under'),
    byBookmaker[book] -> {overUnder: line, odds: price, available: bool}

HONESTY:
  - Needs a SEPARATE free key in .env as SPORTSGAMEODDS_KEY. Without it, every
    function returns None and the dashboard just keeps manual odds entry.
  - Player props post ~12-24h before tip-off; before then results are empty.
  - We never fabricate: no data -> None, clearly.

Our prop_type -> SportsGameOdds statID mapping (singles only; combos like PRA
are their own markets we can add once we confirm their exact statIDs live).
"""

import os
import sys
import statistics
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

API_BASE = "https://api.sportsgameodds.com/v2"

# Support one OR two keys. SPORTSGAMEODDS_KEY is the primary; SPORTSGAMEODDS_KEY2
# (optional) is a failover used automatically when the primary is rate-limited,
# roughly doubling the per-minute headroom. Each is a separate free account's
# key — this is failover, not round-robin hammering: we only reach for key 2
# when key 1 actually returns a 429.
API_KEYS = [k.strip() for k in (os.getenv("SPORTSGAMEODDS_KEY", ""),
                                os.getenv("SPORTSGAMEODDS_KEY2", ""))
            if k and k.strip()]
# Backward-compatible alias: existing code reads API_KEY as "do we have a key".
API_KEY = API_KEYS[0] if API_KEYS else ""

# Our prop_type -> EXACT SportsGameOdds statID. Must be exact: a substring
# match would conflate 'assists' with 'points+assists', 'rebounds+assists' and
# 'points+rebounds+assists' (all contain 'assists'), which corrupts the line.
STAT_ID_MAP = {
    "points": "points", "rebounds": "rebounds", "assists": "assists",
    "threes_made": "threePointersMade", "steals": "steals", "blocks": "blocks",
    "turnovers": "turnovers",
    "pra": "points+rebounds+assists", "pr": "points+rebounds",
    "pa": "points+assists", "ra": "rebounds+assists",
}


class SGOError(Exception):
    pass


class RateLimited(SGOError):
    """Raised on HTTP 429 — the free tier's per-minute cap was hit. Distinct
    from 'no games posted' so the UI can say 'wait a moment' not 'no props'."""
    pass


def _get(path, params):
    if not API_KEYS:
        raise SGOError("No SPORTSGAMEODDS_KEY in .env")
    last_rl = None
    for key in API_KEYS:
        r = requests.get(f"{API_BASE}/{path}", params=params,
                         headers={"x-api-key": key}, timeout=30)
        if r.status_code == 429:
            # This key is throttled — try the next key before giving up.
            last_rl = RateLimited("Rate limit exceeded — wait a minute and retry.")
            continue
        if r.status_code != 200:
            raise SGOError(f"HTTP {r.status_code}: {r.text[:150]}")
        return r.json()
    # Every key we have was rate-limited.
    raise last_rl or RateLimited("Rate limit exceeded — wait a minute and retry.")


def has_key() -> bool:
    return bool(API_KEYS)


def todays_player_keys() -> set:
    """Normalized name-keys of every player with a prop posted in an upcoming
    event (i.e. playing today/soon). Empty set if no key, none posted, or
    rate-limited."""
    keys = set()
    try:
        events = upcoming_events()
    except RateLimited:
        return set()
    for ev in events:
        for _oid, odd in (ev.get("odds") or {}).items():
            ent = odd.get("statEntityID", "all")
            if ent in ("all", "home", "away"):
                continue
            # statEntityID like 'LEBRON_JAMES_1_NBA' -> 'lebronjames'
            parts = ent.split("_")
            name = "".join(parts[:-2]) if len(parts) > 2 else ent
            keys.add(_name_key(name))
    return keys


def upcoming_events():
    """Upcoming NBA events that have odds posted. [] on error/no key.

    Follows SportsGameOdds' cursor pagination (nextCursor) so the WHOLE slate
    comes back, not just the first page. A RateLimited (429) is allowed to
    propagate so callers can tell a throttle apart from an empty slate; other
    errors still degrade to []."""
    out = []
    params = {"leagueID": "NBA", "finalized": "false",
              "oddsAvailable": "true", "limit": 100}
    try:
        for _ in range(50):  # hard cap against a bad cursor
            data = _get("events", params)
            if not isinstance(data, dict):
                break
            out.extend(data.get("data", []) or [])
            # SGO returns the next page token as nextCursor (top level).
            cursor = data.get("nextCursor") or (data.get("meta") or {}).get(
                "nextCursor")
            if not cursor:
                break
            params["cursor"] = cursor
    except RateLimited:
        raise
    except SGOError:
        return out
    return out


def _name_key(name: str) -> str:
    """Normalize a name for matching to statEntityID ('LeBron James' ->
    'lebronjames')."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _stat_matches(odd_stat_id: str, want: str) -> bool:
    # EXACT match only — a substring match conflates single and combo markets.
    return odd_stat_id.lower() == want.lower()


def _is_game_ou(odd_id: str, odd: dict) -> bool:
    """True only for a FULL-GAME OVER/UNDER market.

    SGO oddIDs are 'statID-statEntityID-periodID-betTypeID-sideID', so a
    full-game over/under ends in '-game-ou-over' or '-game-ou-under'. We check
    the explicit fields when present and fall back to the oddID segments, so a
    first-half ('-1h-') or quarter ('-1q-') or spread ('-sp-') prop is excluded
    rather than polluting the full-game line."""
    period = (odd.get("periodID") or "").lower()
    bettype = (odd.get("betTypeID") or "").lower()
    if period or bettype:
        return period == "game" and bettype == "ou"
    oid = (odd_id or "").lower()
    return "-game-ou-" in oid


def player_prop_odds(player_name: str, prop_type: str):
    """Find the consensus line + over/under American odds for a player's prop.

    Reuses the SAME bulk parse the scanner uses (all_player_props) so the
    single-player lookup matches players identically — the old path used a
    looser raw-statEntityID substring check that missed names the bulk path
    found. Returns {line, odds_over, odds_under, books} or None.
    """
    if prop_type not in STAT_ID_MAP or not API_KEYS:
        return None
    try:
        allp = all_player_props()
    except RateLimited:
        return None
    return (allp.get(_name_key(player_name)) or {}).get(prop_type)


# Reverse of STAT_ID_MAP: SportsGameOdds statID -> our prop_type.
_STAT_ID_REVERSE = {v.lower(): k for k, v in STAT_ID_MAP.items()}


def all_player_props():
    """Fetch EVERY player's prop lines in ONE events call and return a nested
    lookup: {name_key: {prop_type: {line, odds_over, odds_under, books}}}.

    This is the bulk version of player_prop_odds — the value scanner uses it so
    a whole-slate scan is ONE network call instead of one per player per stat.
    Empty dict if no key / nothing posted. Raises RateLimited on a 429 so the
    scanner can show 'wait a minute' instead of 'no props posted'.
    """
    if not API_KEY:
        return {}
    events = upcoming_events()  # may raise RateLimited (429) — let it propagate
    # name_key -> prop_type -> {lines:[], over:[], under:[], books:set()}
    acc = {}
    for event in events:
        for _odd_id, odd in (event.get("odds") or {}).items():
            ent = odd.get("statEntityID", "all")
            if ent in ("all", "home", "away"):
                continue
            # oddID format (per SGO docs): statID-statEntityID-periodID-betTypeID-sideID
            # e.g. 'assists-LEBRON_JAMES_1_NBA-game-ou-over'. Keep ONLY full-GAME
            # OVER/UNDER props — otherwise a first-half or quarter line would be
            # lumped into the full-game bucket and corrupt the consensus line.
            if not _is_game_ou(_odd_id, odd):
                continue
            prop_type = _STAT_ID_REVERSE.get((odd.get("statID") or "").lower())
            if not prop_type:
                continue
            # statEntityID like 'LEBRON_JAMES_1_NBA' -> 'lebronjames'
            parts = ent.split("_")
            name = "".join(parts[:-2]) if len(parts) > 2 else ent
            nk = _name_key(name)
            bucket = acc.setdefault(nk, {}).setdefault(
                prop_type, {"lines": [], "over": [], "under": [],
                            "books": set()})
            side = odd.get("sideID")
            for book, bd in (odd.get("byBookmaker") or {}).items():
                if not bd.get("available", True):
                    continue
                if bd.get("overUnder") is not None:
                    bucket["lines"].append(float(bd["overUnder"]))
                price = bd.get("odds")
                if price is not None:
                    bucket["books"].add(book)
                    try:
                        p = int(str(price).replace("+", ""))
                    except ValueError:
                        continue
                    if side == "over":
                        bucket["over"].append(p)
                    elif side == "under":
                        bucket["under"].append(p)

    # Collapse each bucket to a consensus line + median prices.
    out = {}
    for nk, props in acc.items():
        for prop_type, b in props.items():
            if not b["lines"]:
                continue
            out.setdefault(nk, {})[prop_type] = {
                "line": round(statistics.median(b["lines"]) * 2) / 2,
                "odds_over": (int(statistics.median(b["over"]))
                              if b["over"] else None),
                "odds_under": (int(statistics.median(b["under"]))
                               if b["under"] else None),
                "books": len(b["books"]),
            }
    return out


if __name__ == "__main__":
    if not has_key():
        print("No SPORTSGAMEODDS_KEY in .env — add one to pull live prop odds.")
        print("Get a free key at https://sportsgameodds.com/pricing")
        sys.exit(0)
    evs = upcoming_events()
    print(f"Upcoming NBA events with odds: {len(evs)}")
    name = " ".join(sys.argv[1:]) or "LeBron James"
    r = player_prop_odds(name, "points")
    print(f"{name} points prop:", r)
