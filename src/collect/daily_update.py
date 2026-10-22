"""
daily_update.py — Phase 9: the scheduled refresh.

One command that keeps the system current. Run it daily (by hand, or from cron):

    python3 src/collect/daily_update.py

It does three things, in order, each safe to repeat:
  1. Pulls new games for the CURRENT season from balldontlie (resumable; picks
     up where it left off, respects the trial's rate limit).
  2. Backfills opponent + home/away on any new rows.
  3. Grades every pending prediction whose game has now been played, updating
     the live prediction-tracking record.

HONEST LIMITS:
  - On the balldontlie TRIAL (5 req/min) the data pull is slow; on a paid tier
    it's fast. Either way this script is the right place to run it on a schedule.
  - "Current season" is computed from today's date (NBA seasons start in Oct;
    before October, the season start year is the previous calendar year).
  - It does NOT place bets or fetch live odds automatically — it keeps DATA and
    PREDICTION GRADES current. Value alerts live in alerts.py.

Cron example (every day at 9am), after editing paths for your machine:
    0 9 * * * cd ~/sports-betting-bot && ./.venv/bin/python src/collect/daily_update.py >> data/daily_update.log 2>&1
"""

import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))


def current_season_start_year(today: date = None) -> int:
    """NBA season start year. Seasons tip off in October, so Jan-Sep belongs to
    the season that started the PREVIOUS calendar year."""
    today = today or date.today()
    return today.year if today.month >= 10 else today.year - 1


def run():
    print("=== Daily update ===")
    season = current_season_start_year()
    print(f"Current season start year: {season} "
          f"({season}-{str(season + 1)[-2:]})")

    # 1) Pull new games for the current season (resumable).
    print("\n[1/3] Fetching current-season games...")
    try:
        from fetch_balldontlie_stats import fetch_season
        fetch_season(season)
    except Exception as e:
        print(f"  Data pull did not complete ({e}). It's resumable — rerun "
              f"later. Continuing with grading on what we have.")

    # 2) Backfill opponent + home/away on any new rows.
    print("\n[2/3] Backfilling opponent / home-away...")
    try:
        from backfill_opponents import backfill, backfill_home_away
        backfill()
        backfill_home_away()
    except Exception as e:
        print(f"  Backfill skipped ({e}).")

    # 2b) Refresh the injuries snapshot (GOAT tier). Best-effort.
    print("\n[2b] Refreshing injuries snapshot...")
    try:
        from fetch_injuries import fetch_injuries
        fetch_injuries()
    except Exception as e:
        print(f"  Injuries refresh skipped ({e}).")

    # 2b-ESPN) Merge ESPN's FREE injury report (no key, fast news). Best-effort.
    print("\n[2b-ESPN] Merging ESPN free injury report...")
    try:
        from fetch_injuries_espn import fetch_injuries_espn
        fetch_injuries_espn()
    except Exception as e:
        print(f"  ESPN injuries skipped ({e}).")

    # 2c) Refresh official rosters (current team per player). Best-effort.
    print("\n[2c] Refreshing official rosters...")
    try:
        from fetch_rosters import fetch_rosters
        fetch_rosters()
    except Exception as e:
        print(f"  Roster refresh skipped ({e}).")

    # 2d) Capture today's REAL prop lines and grade any settled stored lines.
    #      This is how we accumulate a real-line results set for honest
    #      validation (vs. the backtest's season-avg proxy). Best-effort.
    print("\n[2d] Capturing real prop lines + grading settled...")
    try:
        sys.path.append(str(Path(__file__).resolve().parents[1] / "odds"))
        from odds.line_capture import capture_today, grade_settled, coverage
        cap = capture_today()
        gr = grade_settled()
        cov = coverage()
        print(f"  Captured {cap.get('rows_inserted', 0)} new live lines "
              f"({cap.get('rows_seen', 0)} seen).")
        print(f"  Graded {gr.get('graded', 0)} settled lines "
              f"({gr.get('still_pending', 0)} awaiting results).")
        print(f"  Real-line coverage so far: {cov['rows_graded']} graded of "
              f"{cov['rows_total']} rows across {cov['distinct_games']} games.")
    except Exception as e:
        print(f"  Line capture skipped ({e}).")

    # 3) Grade pending predictions against newly-arrived results.
    print("\n[3/3] Grading pending predictions...")
    try:
        sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
        from models.prediction_tracker import grade_pending, tracking_summary
        graded = grade_pending()
        s = tracking_summary()
        acc = f"{s['accuracy']*100:.1f}%" if s["accuracy"] is not None else "n/a"
        print(f"  Graded {graded} newly-finished predictions.")
        print(f"  Live record: {s['graded']} graded (accuracy {acc}), "
              f"{s['pending']} still pending.")
    except Exception as e:
        print(f"  Grading skipped ({e}).")

    print("\nDone.")


if __name__ == "__main__":
    run()
