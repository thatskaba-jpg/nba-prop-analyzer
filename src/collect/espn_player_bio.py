"""
espn_player_bio.py — FREE player bio + headshot from ESPN (no key).

Pure cosmetics for the dashboard card: a player's photo, position, height and
weight. ESPN's public roster API serves these with no auth. We build a one-time
name -> bio map from all 30 team rosters and cache it for the session, then look
players up BY NAME (ESPN uses its own athlete IDs).

Endpoints (verified live, no auth):
  teams list   https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams
  team roster  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}/roster
               -> athletes[]{displayName, position.abbreviation, displayHeight,
                             displayWeight, headshot.href}

HONESTY: ESPN is an unofficial public API; a name we can't match returns None
(no photo), never a wrong player. Needs a browser-like User-Agent or ESPN 403s.
The roster map is fetched once and cached — 30 calls on first use, then free.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    import requests
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 "
                   "Safari/537.36"),
    "Accept": "application/json",
}
_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
_ROSTER = ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
           "teams/{tid}/roster")

# Session cache: name_key -> bio dict. Built once on first lookup.
_BIO_MAP = None


def _name_key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _team_ids():
    r = requests.get(_TEAMS, headers=_HEADERS, timeout=25)
    r.raise_for_status()
    out = []
    for t in r.json()["sports"][0]["leagues"][0]["teams"]:
        tid = (t.get("team") or {}).get("id")
        if tid:
            out.append(tid)
    return out


def _build_map():
    """Fetch all 30 rosters once and return name_key -> bio dict."""
    m = {}
    try:
        tids = _team_ids()
    except Exception:
        return m
    for tid in tids:
        try:
            r = requests.get(_ROSTER.format(tid=tid), headers=_HEADERS,
                             timeout=25)
            if r.status_code != 200:
                continue
            for a in r.json().get("athletes", []):
                name = a.get("displayName")
                if not name:
                    continue
                m[_name_key(name)] = {
                    "name": name,
                    "position": (a.get("position") or {}).get("abbreviation"),
                    "height": a.get("displayHeight"),
                    "weight": a.get("displayWeight"),
                    "headshot": (a.get("headshot") or {}).get("href"),
                }
        except Exception:
            continue
    return m


def player_bio(player_name: str):
    """Return {name, position, height, weight, headshot} for a player, or None.

    Cosmetic only; safe to call from the UI — any failure yields None, never an
    exception that would break the card."""
    global _BIO_MAP
    if _BIO_MAP is None:
        _BIO_MAP = _build_map()
    return _BIO_MAP.get(_name_key(player_name))


if __name__ == "__main__":
    name = " ".join(sys.argv[1:]) or "LeBron James"
    print(f"{name}:", player_bio(name))
