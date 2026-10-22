"""
schedule_espn.py — FREE backup schedule source (ESPN public API, no key).

schedule.py uses balldontlie's /games endpoint, which needs the GOAT tier to
see UPCOMING (not-yet-played) games. ESPN's public scoreboard is free, needs NO
key, and returns the same thing — today's and upcoming matchups with clean team
abbreviations — so it's a drop-in backup when balldontlie is down, rate-limited,
or the tier lapses.

Same interface as schedule.py so callers can fall back transparently:
    upcoming_games(days_ahead)      -> [{date, home, visitor}]
    next_game_for_team(abbr)        -> (opponent_abbr, is_home) or None

Endpoint (verified live, no auth):
    https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD
    -> {events:[{date, competitions:[{status:{type:{state}}, competitors:[
          {homeAway:'home'/'away', team:{abbreviation}} ]}]}]}

Only games whose state is 'pre' (scheduled, not started) are returned — we never
report a finished or in-progress game as "upcoming", and never invent a matchup.

ESPN's edge rejects a bare User-Agent with 403, so we send a browser-like one.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    import requests
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
                   "nba/scoreboard")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 "
                   "Safari/537.36"),
    "Accept": "application/json",
}

# ESPN uses a few abbreviations that differ from our database's codes. Map them
# so the backup always reports a team code the rest of the app recognizes.
_ABBR_FIX = {
    "GS": "GSW", "WSH": "WAS", "UTAH": "UTA", "NO": "NOP", "PHX": "PHO",
    "SA": "SAS", "NY": "NYK",
}


def _norm_abbr(abbr: str) -> str:
    return _ABBR_FIX.get(abbr, abbr)


def _fetch_day(day: date):
    """Return the raw ESPN events list for one date ([] on any error)."""
    try:
        r = requests.get(ESPN_SCOREBOARD,
                         params={"dates": day.strftime("%Y%m%d")},
                         headers=_HEADERS, timeout=25)
        r.raise_for_status()
        return (r.json() or {}).get("events", []) or []
    except Exception:
        return []


def upcoming_games(days_ahead: int = 8) -> list:
    """Scheduled games from today through `days_ahead` days out.

    Each item: {date, home, visitor} (team abbreviations). Only games still in
    the 'pre' state (not started) are included.
    """
    out = []
    today = date.today()
    for d in range(days_ahead + 1):
        day = today + timedelta(days=d)
        for ev in _fetch_day(day):
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            state = (((comp.get("status") or {}).get("type") or {})
                     .get("state"))
            if state != "pre":  # only not-yet-started games
                continue
            home = visitor = None
            for c in comp.get("competitors") or []:
                abbr = _norm_abbr((c.get("team") or {}).get("abbreviation"))
                if c.get("homeAway") == "home":
                    home = abbr
                elif c.get("homeAway") == "away":
                    visitor = abbr
            if home and visitor:
                out.append({"date": ev.get("date"), "home": home,
                            "visitor": visitor})
    return out


def next_game_for_team(team_abbr: str, days_ahead: int = 8):
    """(opponent_abbr, is_home) for the team's next scheduled game, or None."""
    if not team_abbr:
        return None
    for g in sorted(upcoming_games(days_ahead), key=lambda x: x["date"] or ""):
        if g["home"] == team_abbr:
            return (g["visitor"], True)
        if g["visitor"] == team_abbr:
            return (g["home"], False)
    return None


if __name__ == "__main__":
    team = sys.argv[1] if len(sys.argv) > 1 else None
    games = upcoming_games()
    print(f"ESPN upcoming scheduled games (next 8 days): {len(games)}")
    for g in games[:12]:
        print(f"  {g['date']}: {g['visitor']} @ {g['home']}")
    if team:
        nxt = next_game_for_team(team)
        if nxt:
            opp, home = nxt
            print(f"\n{team}'s next game: {'vs' if home else '@'} {opp} "
                  f"({'home' if home else 'away'})")
        else:
            print(f"\nNo upcoming scheduled game found for {team}.")
