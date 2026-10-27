"""
backfill_game_lines.py — save the sportsbook's game lines for games that already
happened, so old games can be graded against the market (not just our model).

Why: the book's win probability is only stored when someone opens the Game Lines
tab (or a job runs) BEFORE tip-off. Games before that started have no stored
line, so the model had no market check on them (e.g. the Oct 21 MIN @ MIA game).

This asks the same odds feed for each past date and stores, per game, the
de-vigged home win probability plus the spread / moneyline / total in
data/market_snapshots.json. Honest limits: it only works if the feed still
returns lines for finished games, and what it returns may be the CLOSING line
rather than the number at the time you'd have bet. It never overwrites a line
that was already saved (first write wins).

    python3 src/collect/backfill_game_lines.py                 # last 7 days
    python3 src/collect/backfill_game_lines.py 2026-10-20 2026-10-23
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))


def devig_home(ml_home, ml_away):
    """De-vigged home win probability from the two moneylines (or None)."""
    def imp(o):
        o = float(o)
        return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)
    try:
        ih, ia = imp(ml_home), imp(ml_away)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    t = ih + ia
    return ih / t if t > 0 else None


def backfill_day(day_iso, get_lines, games_before, capture):
    """Store lines for one date. get_lines(day)->{TEAM: line dict};
    games_before(day)->league games played before that date;
    capture(day, home, away, p_home, games_played, extra). Returns
    (games_found, games_stored)."""
    lines = get_lines(day_iso) or {}
    found = stored = 0
    gp = games_before(day_iso)
    for team, g in lines.items():
        if not g.get("is_home"):
            continue
        found += 1
        away = g.get("opp")
        p = devig_home(g.get("moneyline"), (lines.get(away) or {}).get("moneyline"))
        if p is None or not away:
            continue
        capture(day_iso, team, away, p, gp,
                {"spread_home": g.get("spread"), "ml_home": g.get("moneyline"),
                 "ml_away": (lines.get(away) or {}).get("moneyline"),
                 "total": g.get("total"), "source": g.get("source"),
                 "backfilled": True})
        stored += 1
    return found, stored


def backfill_date_live(day_iso):
    """Fetch ONE date's lines from the live odds feed and save them. Returns
    the number of games saved (0 if the feed has nothing for that date)."""
    from database import get_connection
    from odds.game_lines import build_game_lines_map
    from models.profile_scoreboard import capture_market_prob

    def games_before(day):
        c = get_connection()
        try:
            season = int(day[:4]) if int(day[5:7]) >= 10 else int(day[:4]) - 1
            return c.execute(
                "SELECT COUNT(DISTINCT game_id) FROM player_game_logs "
                "WHERE game_date < ? AND season = ?", (day, str(season))
            ).fetchone()[0] or 0
        finally:
            c.close()
    return backfill_day(day_iso, build_game_lines_map, games_before,
                        capture_market_prob)[1]


def run(start, end):
    from database import get_connection
    from odds.game_lines import build_game_lines_map
    from models.profile_scoreboard import capture_market_prob

    def games_before(day):
        c = get_connection()
        try:
            return c.execute(
                "SELECT COUNT(DISTINCT game_id) FROM player_game_logs "
                "WHERE game_date < ? AND season = ?",
                (day, str(int(day[:4]) if int(day[5:7]) >= 10 else int(day[:4]) - 1))
            ).fetchone()[0] or 0
        finally:
            c.close()

    d = date.fromisoformat(start)
    last = date.fromisoformat(end)
    total_found = total_stored = 0
    while d <= last:
        f, s = backfill_day(d.isoformat(), build_game_lines_map, games_before,
                            capture_market_prob)
        print(f"  {d}: {f} games with lines, {s} saved")
        total_found += f
        total_stored += s
        d += timedelta(days=1)
    print(f"Done. {total_stored} game lines saved ({total_found} found).")
    if total_found == 0:
        print("The odds feed returned nothing for these dates: it probably "
              "doesn't keep lines for finished games. Lines will be saved going "
              "forward whenever the Game Lines tab (or intraday.py) runs "
              "before tip-off.")
    return total_stored


if __name__ == "__main__":
    args = sys.argv[1:]
    end = args[1] if len(args) > 1 else date.today().isoformat()
    start = args[0] if args else (date.today() - timedelta(days=7)).isoformat()
    run(start, end)
