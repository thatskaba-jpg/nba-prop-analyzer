"""
game_lines.py — REAL game-level odds (total, spread, moneyline) per team.

PRIMARY SOURCE: balldontlie /v2/odds (your GOAT key). Verified live — it returns
spread_home/away_value, moneyline_home/away_odds, total_value with per-vendor
rows, so we take a CONSENSUS (median across sportsbooks) for a stable line.
Team abbreviations come from the games feed (game_id -> home/visitor team).

BACKUPS (only if balldontlie returns nothing, e.g. it is rate-limited, so the
GOAT API isn't overwhelmed): ESPN's free public scoreboard (DraftKings line)
first, then NBA.com's odds feed, then the SportsGameOdds events feed parsed from its full-game markets
(points-*-game-{ml,sp,ou}-*).

WHY IT MATTERS FOR PROPS (not just for betting sides):
  - The TOTAL is the scoring environment. A 240 total is a track meet — every
    player's counting-stat line leans up; a 210 total is a grind — leans down.
  - A BIG SPREAD is blowout risk: a heavy favorite rests starters in the 4th,
    capping their counting stats — a genuine fade on overs.

OUTPUT: build_game_lines_map(date=None) -> {TEAM_ABBR: {total, spread,
moneyline, opp, is_home, matchup, source}}. One entry per team playing that day;
`spread` is that team's own spread (negative = favored). Empty dict when no odds
are posted yet (they appear near tip-off), so the factor is simply a no-op.
"""

import statistics
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))


def _num(v):
    try:
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _median(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _from_balldontlie(date):
    """Consensus game lines from balldontlie /v2/odds for a date."""
    try:
        from collect.balldontlie_client import _get, BallDontLieError
    except Exception:
        return {}
    try:
        # The /v2/odds endpoint PAGINATES at 25 rows per page. A single slate
        # of ~11 games across several books is well over 25 rows, so reading
        # only the first page would show just a few games (the bug that hid
        # most of the slate). Follow meta.next_cursor until every page is in.
        rows = []
        params = {"dates[]": date, "per_page": 100}
        for _ in range(50):  # hard cap so a bad cursor can't loop forever
            payload = _get("v2/odds", params)
            rows.extend(payload.get("data", []))
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
            params["cursor"] = cursor
    except BallDontLieError:
        return {}
    if not rows:
        return {}

    # Group every vendor's row by game_id, then take the median per field.
    by_game = {}
    for r in rows:
        g = by_game.setdefault(r["game_id"], {
            "sp_home": [], "sp_away": [], "ml_home": [], "ml_away": [],
            "total": []})
        g["sp_home"].append(_num(r.get("spread_home_value")))
        g["sp_away"].append(_num(r.get("spread_away_value")))
        g["ml_home"].append(_num(r.get("moneyline_home_odds")))
        g["ml_away"].append(_num(r.get("moneyline_away_odds")))
        g["total"].append(_num(r.get("total_value")))

    # Resolve team abbreviations by fetching that date's games (the games feed
    # filters by date, not by an id array — an id array is ignored and returns
    # the default page, which would match nothing).
    from collect.balldontlie_client import _get
    teams = {}
    try:
        payload = _get("games", {"dates[]": date, "per_page": 100})
        for g in payload.get("data", []):
            teams[g["id"]] = (
                (g.get("home_team") or {}).get("abbreviation"),
                (g.get("visitor_team") or {}).get("abbreviation"))
    except Exception:
        pass

    out = {}
    for gid, g in by_game.items():
        home, away = teams.get(gid, (None, None))
        if not home or not away:
            continue
        total = _median(g["total"])
        sp_home = _median(g["sp_home"])
        sp_away = _median(g["sp_away"])
        ml_home = _median(g["ml_home"])
        ml_away = _median(g["ml_away"])
        out[home] = {"total": total, "spread": sp_home, "moneyline": ml_home,
                     "opp": away, "is_home": True,
                     "matchup": f"{away} @ {home}", "source": "balldontlie"}
        out[away] = {"total": total, "spread": sp_away, "moneyline": ml_away,
                     "opp": home, "is_home": False,
                     "matchup": f"{away} @ {home}", "source": "balldontlie"}
    return out


def _from_espn(date):
    """Backup: DraftKings lines from ESPN's public scoreboard (single book)."""
    try:
        from collect.espn_api import game_lines
        return game_lines(date)
    except Exception:
        return {}


def _from_nba(date):
    """Backup: NBA.com's public odds feed (moneyline + spread, no total)."""
    try:
        from collect.nba_api import game_lines
        return game_lines(date)
    except Exception:
        return {}


def _from_sportsgameodds():
    """Fallback: parse full-game markets from the SGO events feed."""
    try:
        from odds.sportsgameodds_client import upcoming_events, has_key
    except Exception:
        return {}
    if not has_key():
        return {}
    try:
        events = upcoming_events() or []
    except Exception:
        return {}
    out = {}
    for e in events:
        teams = e.get("teams") or {}
        home = (teams.get("home") or {}).get("names", {}).get("short")
        away = (teams.get("away") or {}).get("names", {}).get("short")
        odds = e.get("odds") or {}
        if not home or not away or not isinstance(odds, dict):
            continue
        total = _num((odds.get("points-all-game-ou-over") or {})
                     .get("bookOverUnder"))
        sp_home = _num((odds.get("points-home-game-sp-home") or {})
                       .get("bookSpread"))
        sp_away = _num((odds.get("points-away-game-sp-away") or {})
                       .get("bookSpread"))
        ml_home = _num((odds.get("points-home-game-ml-home") or {})
                       .get("bookOdds"))
        ml_away = _num((odds.get("points-away-game-ml-away") or {})
                       .get("bookOdds"))
        out[home] = {"total": total, "spread": sp_home, "moneyline": ml_home,
                     "opp": away, "is_home": True,
                     "matchup": f"{away} @ {home}", "source": "sportsgameodds"}
        out[away] = {"total": total, "spread": sp_away, "moneyline": ml_away,
                     "opp": home, "is_home": False,
                     "matchup": f"{away} @ {home}", "source": "sportsgameodds"}
    return out


def build_game_lines_map(date=None):
    """{team_abbr: {total, spread, moneyline, opp, is_home, matchup, source}}.

    balldontlie first (consensus across books); then ESPN, NBA.com, SportsGameOdds.
    Empty when no odds are posted yet."""
    import datetime as _dt
    if not date:
        try:
            from database import nba_today
            date = nba_today()
        except Exception:
            date = _dt.date.today().isoformat()
    bdl = _from_balldontlie(date)
    if bdl:
        return bdl
    # balldontlie empty (rate-limited, down, or no lines yet): backups. ESPN
    # first (free, no quota), then SportsGameOdds (all books, but quota-limited).
    return _from_espn(date) or _from_nba(date) or _from_sportsgameodds()


if __name__ == "__main__":
    import datetime as _dt
    m = build_game_lines_map(_dt.date.today().isoformat())
    print(f"Teams with game lines: {len(m)}")
    shown = 0
    for team, g in m.items():
        sp = f"{g['spread']:+}" if g["spread"] is not None else "?"
        ml = f"{g['moneyline']:+.0f}" if g["moneyline"] is not None else "?"
        print(f"  {team}: total {g['total']}, spread {sp} vs {g['opp']}, "
              f"ML {ml} [{g['source']}] ({g['matchup']})")
        shown += 1
        if shown >= 10:
            break
