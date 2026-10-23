"""
intraday.py — the part of the update that has to run SEVERAL times a day.

The nightly update (daily_update.py) loads box scores and rosters once. The
things that change through the day are prices and news, and the whole point of
the forward test is to catch them as they move:

  1. refresh injuries (balldontlie + ESPN) and save any CHANGES with a timestamp
  2. capture every live prop price (one row per book; a moved line is a new row)
  3. rebuild today's fitted projections (they react to who is OUT)
  4. save posted lineups when they appear or change
  5. log tonight's paper bets, using the freshest prices
  6. grade finished paper bets and record closing line value

Run it by hand, or from cron a few times on game days, e.g. (machine time = ET):
    0 12,15,17,18,19 * * * cd ~/sports-betting-bot && ./.venv/bin/python src/collect/intraday.py >> data/intraday.log 2>&1
The last run before tip becomes the "closing" price, so schedule one about an
hour before the earliest tip you care about.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

import pipeline_status  # noqa: E402
from database import nba_today  # noqa: E402


def run():
    print("=== Intraday update ===")
    day = nba_today()
    step = pipeline_status.run_step

    def refresh_injuries():
        from fetch_injuries import fetch_injuries
        fetch_injuries()
        try:
            from fetch_injuries_espn import fetch_injuries_espn
            fetch_injuries_espn()
        except Exception as e:                       # ESPN is a bonus feed
            print(f"  ESPN injuries skipped ({e}).")

    step("intraday:injuries", refresh_injuries)

    def news():
        from collect.news_capture import snapshot_injuries
        return f"{snapshot_injuries()} injury changes saved"
    step("intraday:injury_snapshots", news)

    def lines():
        from odds.line_capture import capture_today
        cap = capture_today()
        return f"{cap.get('rows_inserted', 0)} new price rows ({cap.get('rows_seen', 0)} seen)"
    step("intraday:lines", lines)

    def slate():
        from models.fitted_projection import build_slate
        s = build_slate(day)
        return "no slate (no model/games)" if not s else f"{len(s['meta'])} players projected"
    step("intraday:fitted_slate", slate)

    def lineups():
        from collect.news_capture import snapshot_lineups
        from models.paper_bets import todays_games
        ids = [g["id"] for g in todays_games(day)]
        return f"{snapshot_lineups(ids)} lineups saved"
    step("intraday:lineups", lineups)

    def log_bets():
        from models.paper_bets import log_paper_bets
        return log_paper_bets(day)
    step("intraday:paper_log", log_bets)

    def grade():
        from models.paper_bets import grade_paper_bets
        return grade_paper_bets()
    step("intraday:paper_grade", grade)

    failed = [k for k in pipeline_status.failed_steps() if k.startswith("intraday:")]
    print("Done." + (f" Steps that failed: {', '.join(failed)}" if failed else ""))
    return 1 if "intraday:lines" in failed else 0


if __name__ == "__main__":
    sys.exit(run())
