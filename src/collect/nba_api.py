"""
nba_api.py — schedule + scores from NBA.com's public live-data CDN.

Third in the backup chain (balldontlie -> ESPN -> NBA.com) for the schedule and
final/live scores. Today's slate only: the feed is "todays scoreboard", so for
any other date this returns nothing instead of guessing.

ODDS: odds_todaysGames.json (moneyline + spread, decimal odds, many books).
Its layout is taken from the swar/nba_api docs (github.com/swar/nba_api,
docs/nba_api/live/endpoints/odds.md) -- I could not download the live file from
my environment (HTTP 403), so this parser is tested against the DOCUMENTED shape
only. It returns {} if the shape differs. No total is read from it (the docs
don't show that market), so `total` is None for lines sourced here.
We call the same CDN URLs directly with `requests`: the nba_api package needs
Python 3.10+ and this project's venv is 3.9, so it is not a dependency.
Team tricodes (NYK, SAS, GSW, NOP, WAS, UTA...) already match the bot's.

Unofficial endpoint: every function fails soft (returns empty), never raises.
"""

import requests

import statistics

URL = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
ODDS_URL = "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"
# Same browser-style headers the nba_api package sends to cdn.nba.com. (No
# Accept-Encoding: requests can't decode "br" without extra packages.)
HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
           "Accept": "application/json, text/plain, */*",
           "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.nba.com/",
           "Origin": "https://www.nba.com",
           "Connection": "keep-alive"}


def scoreboard(timeout: float = 4.0) -> dict:
    r = requests.get(URL, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def odds_payload(timeout: float = 4.0) -> dict:
    r = requests.get(ODDS_URL, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _f(v):
    try:
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def decimal_to_american(d):
    """1.37 -> -270, 2.20 -> +120. None if not a valid decimal price."""
    d = _f(d)
    if d is None or d <= 1.0:
        return None
    return int(round((d - 1) * 100)) if d >= 2.0 else int(round(-100 / (d - 1)))


def _med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def parse_odds_game(game: dict):
    """(ml_home, ml_away, spread_home, spread_away) as consensus (median over
    US books when any are US-labelled, else all books). None if nothing usable."""
    ml = {"home": [], "away": []}
    sp = {"home": [], "away": []}
    for m in game.get("markets", []) or []:
        name = (m.get("name") or "").lower()
        books = m.get("books", []) or []
        us = [b for b in books if (b.get("countryCode") or "").upper() == "US"]
        for b in (us or books):
            for o in b.get("outcomes", []) or []:
                side = (o.get("type") or "").lower()
                if side not in ("home", "away"):
                    continue
                if name == "2way":
                    ml[side].append(decimal_to_american(o.get("odds")))
                elif name == "spread":
                    sp[side].append(_f(o.get("spread")))
    mlh, mla = _med(ml["home"]), _med(ml["away"])
    sph, spa = _med(sp["home"]), _med(sp["away"])
    if sph is None and spa is not None:
        sph = -spa
    if spa is None and sph is not None:
        spa = -sph
    if mlh is None and mla is None and sph is None:
        return None
    return (None if mlh is None else int(mlh), None if mla is None else int(mla),
            sph, spa)


def game_lines(date_iso: str) -> dict:
    """{TEAM: {total, spread, moneyline, opp, is_home, matchup, source}} from
    NBA.com odds, joined to the scoreboard by gameId. {} on any failure or when
    the feed is showing a different day."""
    try:
        sb = (scoreboard().get("scoreboard") or {})
        if sb.get("gameDate") and sb["gameDate"] != date_iso:
            return {}
        by_id = {}
        for g in sb.get("games", []) or []:
            h = (g.get("homeTeam") or {}).get("teamTricode")
            a = (g.get("awayTeam") or {}).get("teamTricode")
            if h and a:
                by_id[str(g.get("gameId"))] = (h.upper(), a.upper(), g.get("gameStatus"))
        out = {}
        for og in (odds_payload().get("games") or []):
            ids = by_id.get(str(og.get("gameId")))
            parsed = parse_odds_game(og)
            if not ids or not parsed:
                continue
            h, a, _status = ids
            mlh, mla, sph, spa = parsed
            m = f"{a} @ {h}"
            out[h] = {"total": None, "spread": sph, "moneyline": mlh, "opp": a,
                      "is_home": True, "matchup": m, "source": "nba.com"}
            out[a] = {"total": None, "spread": spa, "moneyline": mla, "opp": h,
                      "is_home": False, "matchup": m, "source": "nba.com"}
        return out
    except Exception:
        return {}


def parse_games(payload: dict, date_iso: str) -> list:
    """[{home, away, status, home_pts, away_pts, final, started}] for the
    requested date, or [] if the feed is showing a different day."""
    sb = (payload or {}).get("scoreboard") or {}
    if sb.get("gameDate") and sb["gameDate"] != date_iso:
        return []
    out = []
    for g in sb.get("games", []) or []:
        h = (g.get("homeTeam") or {}).get("teamTricode")
        a = (g.get("awayTeam") or {}).get("teamTricode")
        if not h or not a:
            continue
        code = g.get("gameStatus")          # 1 scheduled, 2 live, 3 final
        if code == 1:
            status = str(g.get("gameTimeUTC") or "")
        elif code == 3:
            status = "Final"
        else:
            status = str(g.get("gameStatusText") or "").strip() or "Live"
        out.append({
            "home": h.upper(), "away": a.upper(), "status": status,
            "home_pts": (g.get("homeTeam") or {}).get("score"),
            "away_pts": (g.get("awayTeam") or {}).get("score"),
            "final": code == 3, "started": code in (2, 3)})
    return out


def games(date_iso: str) -> list:
    try:
        return parse_games(scoreboard(), date_iso)
    except Exception:
        return []


def schedule(date_iso: str) -> dict:
    return {f"{g['away']} @ {g['home']}": g["status"] for g in games(date_iso)}


def scores(date_iso: str) -> dict:
    out = {}
    for g in games(date_iso):
        if not g["started"]:
            continue
        try:
            out[f"{g['away']} @ {g['home']}"] = {
                "home": int(g["home_pts"]), "away": int(g["away_pts"]),
                "final": g["final"]}
        except (TypeError, ValueError):
            continue
    return out
