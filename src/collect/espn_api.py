"""
espn_api.py — free NBA schedule, scores and game odds from ESPN's public API.

Used as the BACKUP when balldontlie is rate-limited (HTTP 429) or down:

    https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD

No key needed. Unofficial, so every function here fails soft (returns empty)
and the caller keeps going. Shape checked against the live feed:
events[].competitions[0] has competitors (homeAway + team.abbreviation + score)
and, for games with a line posted, an `odds` list (DraftKings) holding
pointSpread / moneyline / total blocks.

WHAT ESPN CANNOT DO: player props. There is no ESPN source for those, so the
Value Board still needs balldontlie or SportsGameOdds.

ABBREVIATIONS: ESPN says NY / SA / GS / NO / WSH / UTAH. The bot uses
NYK / SAS / GSW / NOP / WAS / UTA, so every team is mapped through ABBR.
"""

import time

import requests

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
# ESPN answers 403 to non-browser User-Agents, so look like a normal browser.
HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
           "Accept": "application/json"}

ABBR = {"NY": "NYK", "SA": "SAS", "GS": "GSW", "NO": "NOP", "WSH": "WAS",
        "UTAH": "UTA"}


def team(abbr: str) -> str:
    a = (abbr or "").upper()
    return ABBR.get(a, a)


def _num(v):
    try:
        return float(str(v).strip().lstrip("+"))
    except (TypeError, ValueError):
        return None


def scoreboard(date_iso: str, timeout: float = 8.0) -> dict:
    """Raw ESPN scoreboard JSON for a date ('YYYY-MM-DD'). Raises on failure."""
    r = requests.get(URL, params={"dates": date_iso.replace("-", "")},
                     headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _iso_start(s: str) -> str:
    """ESPN gives '2026-10-23T02:00Z'; the dashboard expects seconds present."""
    s = (s or "").strip()
    if len(s) == 17 and s.endswith("Z"):
        return s[:-1] + ":00Z"
    return s


def parse_games(payload: dict) -> list:
    """[{home, away, state, status, home_pts, away_pts, final, odds}] from a
    scoreboard payload. `status` mirrors the balldontlie convention: an ISO
    start time before tip-off, otherwise the live/final text."""
    out = []
    for ev in (payload or {}).get("events", []) or []:
        comp = ((ev.get("competitions") or [{}])[0]) or {}
        home = away = None
        hp = ap = None
        for c in comp.get("competitors", []) or []:
            ab = team((c.get("team") or {}).get("abbreviation"))
            pts = c.get("score")
            if c.get("homeAway") == "home":
                home, hp = ab, pts
            elif c.get("homeAway") == "away":
                away, ap = ab, pts
        if not home or not away:
            continue
        st = ((ev.get("status") or {}).get("type") or {})
        state = st.get("state")           # pre / in / post
        if state == "pre":
            status = _iso_start(ev.get("date"))
        else:
            status = st.get("shortDetail") or st.get("description") or ""
            if st.get("completed"):
                status = "Final"
        out.append({
            "home": home, "away": away, "state": state, "status": status,
            "start": _iso_start(ev.get("date")),
            "home_pts": int(hp) if str(hp or "").isdigit() else None,
            "away_pts": int(ap) if str(ap or "").isdigit() else None,
            "final": bool(st.get("completed")),
            "odds": ((comp.get("odds") or [None])[0]),
        })
    return out


def parse_odds(odds: dict):
    """(spread_home, spread_away, ml_home, ml_away, total) or None.

    Reads the explicit pointSpread/moneyline/total 'close' blocks (home and away
    spread are given separately, so there is no sign guessing). Falls back to
    the flat `spread`/`overUnder` fields (home-perspective) if blocks are
    missing."""
    if not odds:
        return None

    def close(block, side, key):
        return (((block or {}).get(side) or {}).get("close") or {}).get(key)

    ps, ml, tot = odds.get("pointSpread"), odds.get("moneyline"), odds.get("total")
    sp_h, sp_a = _num(close(ps, "home", "line")), _num(close(ps, "away", "line"))
    if sp_h is None and odds.get("spread") is not None:
        sp_h = _num(odds.get("spread"))
        sp_a = -sp_h if sp_h is not None else None
    ml_h, ml_a = _num(close(ml, "home", "odds")), _num(close(ml, "away", "odds"))
    total = _num(str(close(tot, "over", "line") or "").lstrip("ou"))
    if total is None:
        total = _num(odds.get("overUnder"))
    if sp_h is None and ml_h is None and total is None:
        return None
    return sp_h, sp_a, ml_h, ml_a, total


def games(date_iso: str) -> list:
    """Parsed games for a date. [] on any failure (never raises)."""
    try:
        return parse_games(scoreboard(date_iso))
    except Exception:
        return []


def game_lines(date_iso: str) -> dict:
    """Same shape as odds.game_lines.build_game_lines_map:
    {TEAM: {total, spread, moneyline, opp, is_home, matchup, source}}.
    Only games that actually have a line posted appear."""
    out = {}
    for g in games(date_iso):
        parsed = parse_odds(g.get("odds"))
        if not parsed:
            continue
        sp_h, sp_a, ml_h, ml_a, total = parsed
        h, a = g["home"], g["away"]
        m = f"{a} @ {h}"
        out[h] = {"total": total, "spread": sp_h, "moneyline": ml_h,
                  "opp": a, "is_home": True, "matchup": m, "source": "espn"}
        out[a] = {"total": total, "spread": sp_a, "moneyline": ml_a,
                  "opp": h, "is_home": False, "matchup": m, "source": "espn"}
    return out


def start_times(date_iso: str) -> dict:
    """{"AWAY @ HOME": ISO start time (UTC)}."""
    return {f"{g['away']} @ {g['home']}": g["start"] for g in games(date_iso)
            if g.get("start")}


def schedule(date_iso: str) -> dict:
    """{"AWAY @ HOME": status} — ISO start time before tip, else live/final text."""
    return {f"{g['away']} @ {g['home']}": g["status"] for g in games(date_iso)}


def scores(date_iso: str) -> dict:
    """{"AWAY @ HOME": {"home": int, "away": int, "final": bool}} for games that
    have a score."""
    out = {}
    for g in games(date_iso):
        if g["home_pts"] is None or g["away_pts"] is None:
            continue
        if g["state"] == "pre":
            continue
        out[f"{g['away']} @ {g['home']}"] = {
            "home": g["home_pts"], "away": g["away_pts"], "final": g["final"]}
    return out


if __name__ == "__main__":
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")
    t0 = time.time()
    m = game_lines(day)
    print(f"ESPN {day}: {len(m) // 2} games with lines ({time.time() - t0:.1f}s)")
    for tm, g in list(m.items())[:6]:
        print(f"  {tm}: spread {g['spread']} ML {g['moneyline']} total {g['total']}")
