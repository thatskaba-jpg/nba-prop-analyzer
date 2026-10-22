"""
schedule.py — find a team's NEXT scheduled game (opponent + home/away).

Our box-score database only has games that already happened, so it can't tell
us tonight's matchup. balldontlie's /games endpoint CAN: on the GOAT tier it
returns scheduled (not-yet-played) games with status_state == "scheduled" and
clean home/visitor team abbreviations (verified against a live response).

This module caches the upcoming slate briefly and resolves, for a given team
abbreviation, the next game's opponent and whether that team is home.

HONESTY: this reads the real schedule — it never invents a matchup. If a team
has no upcoming scheduled game in the window (off day, offseason), it returns
None and the dashboard falls back to a manual pick.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect.balldontlie_client import _get, BallDontLieError  # noqa: E402

CURRENT_SEASON = date.today().year if date.today().month >= 10 else date.today().year - 1


def upcoming_games(days_ahead: int = 8) -> list:
    """Return scheduled games from today through `days_ahead` days out.

    Each item: {date, home, visitor} using team abbreviations. Only games still
    marked 'scheduled' (not final) are returned.
    """
    try:
        from database import nba_today
        today = date.fromisoformat(nba_today())
    except Exception:
        today = date.today()
    end = today + timedelta(days=days_ahead)
    out = []
    cursor = None
    today_iso = today.isoformat()
    try:
        while True:
            params = {"seasons[]": CURRENT_SEASON, "per_page": 100,
                      "start_date": today.isoformat(),
                      "end_date": end.isoformat()}
            if cursor:
                params["cursor"] = cursor
            payload = _get("games", params)
            for g in payload.get("data", []):
                gdate = (g.get("date") or "")[:10]
                # Keep TODAY's games no matter their status (live/final) so the
                # app can still surface tonight's matchup; for future days only
                # keep games still scheduled.
                if gdate != today_iso and g.get("status_state") == "final":
                    continue
                home = (g.get("home_team") or {}).get("abbreviation")
                vis = (g.get("visitor_team") or {}).get("abbreviation")
                if home and vis:
                    out.append({"date": g.get("date"), "home": home,
                                "visitor": vis})
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
    except BallDontLieError:
        return out
    return out


def next_game_for_team(team_abbr: str, days_ahead: int = 8):
    """Return (opponent_abbr, is_home) for `team_abbr`'s game.

    Prefers TODAY's game (the one with props posted) even if it has already
    tipped off or finished — bettors researching at night still mean tonight's
    matchup, not tomorrow's. Only when the team has no game today does it roll
    forward to the next scheduled one in the window."""
    if not team_abbr:
        return None
    # Use the Eastern NBA day so a late-night ET session still maps to "tonight".
    try:
        from database import nba_today
        today_iso = nba_today()
    except Exception:
        today_iso = date.today().isoformat()

    games = upcoming_games(days_ahead)
    games_sorted = sorted(games, key=lambda x: x["date"] or "")

    # 1) Today's game first, regardless of whether it's live/final.
    for g in games_sorted:
        gdate = (g["date"] or "")[:10]
        if gdate != today_iso:
            continue
        if g["home"] == team_abbr:
            return (g["visitor"], True)
        if g["visitor"] == team_abbr:
            return (g["home"], False)

    # 2) Otherwise the next upcoming game involving this team.
    for g in games_sorted:
        if g["home"] == team_abbr:
            return (g["visitor"], True)
        if g["visitor"] == team_abbr:
            return (g["home"], False)
    return None


if __name__ == "__main__":
    team = sys.argv[1] if len(sys.argv) > 1 else None
    games = upcoming_games()
    print(f"Upcoming scheduled games (next 8 days): {len(games)}")
    for g in games[:10]:
        print(f"  {g['date']}: {g['visitor']} @ {g['home']}")
    if team:
        nxt = next_game_for_team(team)
        if nxt:
            opp, home = nxt
            print(f"\n{team}'s next game: {'vs' if home else '@'} {opp} "
                  f"({'home' if home else 'away'})")
        else:
            print(f"\nNo upcoming scheduled game found for {team}.")
