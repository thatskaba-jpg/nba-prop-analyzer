"""
diagnose.py — time every live feed the Game Lines page and Value Board depend on.

    python3 src/diagnose.py

Prints one line per feed: OK / SLOW / FAIL, seconds, and how much data came
back. A feed that hangs is abandoned after 40s so this always finishes. Paste
the output when something won't load: it shows which feed is the culprit
(rate-limited key, dead network, slow API) instead of guessing.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "collect"))

LIMIT = 40


def _timed(name, fn, size=len):
    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t0 = time.time()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(LIMIT)
    dt = time.time() - t0
    if th.is_alive():
        print(f"HANG  {dt:5.1f}s+  {name}  (no answer in {LIMIT}s)")
    elif "e" in box:
        print(f"FAIL  {dt:5.1f}s   {name}  -> {type(box['e']).__name__}: "
              f"{str(box['e'])[:120]}")
    else:
        try:
            n = size(box["v"])
        except Exception:
            n = "?"
        flag = "SLOW" if dt > 5 else "OK  "
        print(f"{flag}  {dt:5.1f}s   {name}  ({n} items)")


def main():
    from database import nba_today
    day = nba_today()
    season = int(day[:4]) if int(day[5:7]) >= 10 else int(day[:4]) - 1
    print(f"Slate date {day}, season {season}\n")

    from collect import balldontlie_client as bc
    print(f"API key set: {bool(bc.API_KEY)}   pacing: {bc.RATE_NOTE}\n")

    _timed("games today (schedule)",
           lambda: bc._get("games", {"dates[]": day, "per_page": 100})["data"])
    from odds.game_lines import build_game_lines_map
    _timed("game lines (ML/spread/total)", lambda: build_game_lines_map(day))
    from collect import espn_api, nba_api
    # Raw calls (these RAISE, unlike the wrappers, so the real error shows).
    _timed("backup: ESPN scoreboard (games)",
           lambda: espn_api.parse_games(espn_api.scoreboard(day)))
    _timed("backup: ESPN games WITH a line",
           lambda: [g for g in espn_api.parse_games(espn_api.scoreboard(day))
                    if g["odds"]])
    _timed("backup: NBA.com scoreboard",
           lambda: nba_api.scoreboard().get("scoreboard", {}).get("games", []))
    from odds.balldontlie_props import all_player_props
    _timed("player props (Value Board)", lambda: all_player_props(day))
    from collect.goat_data import (team_four_factors_map, lineups)
    _timed("four factors", lambda: team_four_factors_map(season))
    try:
        from collect.goat_data import team_ratings as _tr
        _timed("team ratings (this season)", lambda: _tr(season))
    except Exception:
        pass
    from collect.teammate_injuries import out_players_by_team
    _timed("injuries by team", out_players_by_team)
    try:
        games = bc._get("games", {"dates[]": day, "per_page": 100}).get("data", [])
    except Exception:
        games = []
    if games:
        gid = games[0]["id"]
        _timed(f"lineups (game {gid})", lambda: lineups(gid))
    print("\nDone. HANG/FAIL/SLOW lines are the ones to look at.")


if __name__ == "__main__":
    main()
