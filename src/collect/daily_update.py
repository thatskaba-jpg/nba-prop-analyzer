"""
daily_update.py — Phase 9: the scheduled refresh.

One command that keeps the system current. Run it daily (by hand, or from cron):

    python3 src/collect/daily_update.py

Each step is safe to repeat, and each one records whether it WORKED in
data/pipeline_status.json (the dashboard shows a warning when data is stale or a
key step failed). The run exits non-zero if the box-score pull itself failed, so
cron mail / your terminal will tell you instead of the app silently going stale.

  1. Pull new games for the CURRENT season from balldontlie (resumable).
  2. Backfill opponent + home/away, refresh injuries (balldontlie + ESPN),
     rosters, per-game team pace and player advanced stats.
  3. Capture real prop prices, grade settled ones (DNPs voided).
  4. Retrain the fitted projection if it is more than a week old.
  5. Grade pending predictions and paper bets.

For prices and news THROUGH the day (the forward test), see intraday.py.

Cron example (every day at 9am), after editing paths for your machine:
    0 9 * * * cd ~/sports-betting-bot && ./.venv/bin/python src/collect/daily_update.py >> data/daily_update.log 2>&1
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

import pipeline_status  # noqa: E402

MODEL_MAX_AGE_DAYS = 7


def current_season_start_year(today: date = None) -> int:
    """NBA season start year. Seasons tip off in October, so Jan-Sep belongs to
    the season that started the PREVIOUS calendar year."""
    today = today or date.today()
    return today.year if today.month >= 10 else today.year - 1


def _model_is_stale():
    """True if the fitted projection is missing or older than a week."""
    from models.fitted_projection import load
    models = load()
    if not models:
        return True
    try:
        trained = datetime.strptime(models["_meta"]["trained_at"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    age = datetime.now(timezone.utc).replace(tzinfo=None) - trained
    return age > timedelta(days=MODEL_MAX_AGE_DAYS)


def run():
    print("=== Daily update ===")
    season = current_season_start_year()
    print(f"Current season start year: {season} "
          f"({season}-{str(season + 1)[-2:]})")
    step = pipeline_status.run_step

    def games():
        from fetch_balldontlie_stats import fetch_season
        fetch_season(season)
    print("\n[1/5] Fetching current-season games...")
    games_ok, _ = step("daily:games", games)
    if not games_ok:
        print("  The data pull did not complete. It's resumable: rerun later. "
              "Continuing with what we have.")

    print("\n[2/5] Backfilling opponent / home-away, injuries, rosters, pace...")

    def backfill():
        from backfill_opponents import backfill, backfill_home_away
        backfill()
        backfill_home_away()
    step("daily:backfill", backfill)

    def injuries():
        from fetch_injuries import fetch_injuries
        fetch_injuries()
    step("daily:injuries", injuries)

    def injuries_espn():
        from fetch_injuries_espn import fetch_injuries_espn
        fetch_injuries_espn()
    step("daily:injuries_espn", injuries_espn)

    def rosters():
        from fetch_rosters import fetch_rosters
        fetch_rosters()
    step("daily:rosters", rosters)

    def pace():
        from fetch_team_pace import fetch_team_pace
        fetch_team_pace(season)
        if date.today().month in (10, 11):      # thin sample early: add last season
            fetch_team_pace(season - 1)
    step("daily:pace", pace)

    def advanced():
        from fetch_player_advanced import fetch_player_advanced
        fetch_player_advanced(season)
        if date.today().month in (10, 11):
            fetch_player_advanced(season - 1)
    step("daily:advanced", advanced)

    def indexes():
        from database import ensure_indexes
        ensure_indexes()
    step("daily:indexes", indexes)

    print("\n[3/5] Capturing real prop prices + grading settled lines...")

    def lines():
        from odds.line_capture import capture_today, grade_settled, coverage
        cap, gr, cov = capture_today(), grade_settled(), coverage()
        print(f"  Captured {cap.get('rows_inserted', 0)} new live lines "
              f"({cap.get('rows_seen', 0)} seen).")
        print(f"  Graded {gr.get('graded', 0)} settled lines "
              f"({gr.get('still_pending', 0)} awaiting results).")
        if gr.get("voided") or gr.get("repaired"):
            print(f"  Voided {gr.get('voided', 0)} DNP lines (refunded by "
                  f"books); repaired {gr.get('repaired', 0)} earlier DNPs "
                  f"that had been graded as unders.")
        print(f"  Real-line coverage so far: {cov['rows_graded']} graded of "
              f"{cov['rows_total']} rows across {cov['distinct_games']} games.")
    step("daily:lines", lines)

    print("\n[4/5] Fitted projection...")

    def retrain():
        if not _model_is_stale():
            return "model is fresh"
        from models.fitted_projection import build_features, fit, save
        table = build_features()
        last = table["game_date"].max()
        cutoff = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
        save(fit(table, cutoff), trained_through=last)
        return f"retrained through {last}"
    step("daily:fitted_model", retrain)

    print("\n[5/5] Grading pending predictions and paper bets...")

    def predictions():
        from models.prediction_tracker import grade_pending, tracking_summary
        graded = grade_pending()
        s = tracking_summary()
        acc = f"{s['accuracy']*100:.1f}%" if s["accuracy"] is not None else "n/a"
        print(f"  Graded {graded} newly-finished predictions.")
        print(f"  Live record: {s['graded']} graded (accuracy {acc}), "
              f"{s['pending']} still pending, {s.get('void', 0)} voided.")
    step("daily:predictions", predictions)

    def paper():
        from models.paper_bets import grade_paper_bets
        r = grade_paper_bets()
        print(f"  Paper bets: graded {r['graded']}, voided {r['voided']}.")
    step("daily:paper_grade", paper)

    failed = [k for k in pipeline_status.failed_steps() if k.startswith("daily:")]
    print("\nDone." + (f" Steps that failed on their last run: {', '.join(failed)}"
                       if failed else ""))
    return 1 if "daily:games" in failed else 0


if __name__ == "__main__":
    sys.exit(run())
