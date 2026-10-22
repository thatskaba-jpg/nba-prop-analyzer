# Project Progress & Roadmap

Status: **NBA system COMPLETE (Phases 1–9).** NFL is intentionally OUT OF SCOPE.

## The goal
A practical AI sports-betting RESEARCH tool for NBA player props. It projects a
player's stat line, estimates Over/Under probabilities, compares them to
sportsbook odds to find potential value, and HONESTLY backtests and
forward-tracks itself. It does not place bets and makes no guarantees. Core rule
everywhere: no peeking at the future (no data leakage).

## Ground rules held to
- Real, maintainable Python. Free/cheap tools first.
- Error handling, API rate-limit handling, data validation.
- API keys in .env (never in code, never committed).
- Never fabricate stats, odds, lines, or injuries. Distinguish actual data vs.
  model estimates vs. assumptions.
- No automated wagers. Paper-bet and backtest before any real money.

## Data sources (verified, not guessed)
| Need | Source | Cost | Notes |
|------|--------|------|-------|
| Per-game player stats 2020-2024 | NocturneBear/NBA-Data-2010-2024 CSVs (GitHub, MIT) | Free | Offline, network-safe (NBA.com is blocked here). Full stats incl. stl/blk/to. |
| Current seasons 2024-25 / 2025-26 / 2026-27 | balldontlie API (GOAT trial) | Free trial, then paid | 5 req/min on trial. Season # = start year. Current = 2026. |
| Betting odds + props | balldontlie (GOAT) / manual entry | paid / free | EV math works on any odds you type in. Never fabricated. |
| NFL | — | — | **Not doing NFL. Out of scope by decision.** |

## Phase checklist — ALL COMPLETE
- [x] Phase 1 — Data sources chosen & verified
- [x] Phase 2 — Python env + project structure (.venv, folders, .env)
- [x] Phase 3 — Collect, clean, store (CSV + balldontlie loaders, resumable,
      opponent/home-away backfill, steals/blocks/turnovers/fouls)
- [x] Phase 4 — Baseline model: projection blend + probability
- [x] Phase 5 — Matchup/role features: opponent defense, position-aware defense
      (Guard/Wing/Big), rest, home/away, minutes trend
- [x] Phase 6 — Odds -> implied prob -> vig removal -> expected value + verdict
- [x] Phase 7 — Streamlit dashboard: factor/weight builder, opponent picker,
      14 prop types, plain-language cards, EV calculator
- [x] Phase 8 — Honest backtest: accuracy, Brier, log loss, calibration, ROI,
      max drawdown, equity curve, season-by-season, vs. naive baseline
- [x] Phase 9 — Scheduled updates (daily_update.py), value alerts (alerts.py),
      prediction tracking w/ auto-grading (prediction_tracker.py + dashboard tab)
- [—] NFL — NOT doing it. Deliberately out of scope.

## Key modeling notes
- Probability uses the NEGATIVE BINOMIAL (not Poisson) so the tails match how
  NBA stats really swing — fixed an overconfidence problem the backtest exposed.
- Double-Double / Triple-Double are yes/no EVENTS, modeled as event
  probabilities (milestone_props.py), not Over/Under counts.
- Positions for the position-aware defense are INFERRED from each player's stat
  profile; run fetch_positions.py to upgrade to balldontlie's real position field.

## One open data item (optional)
The current seasons (2024-25 / 2025-26 / 2026-27) were loaded BEFORE the
steals/blocks/turnovers columns existed, so those columns are blank for those
rows. Everything works on the complete 2020–2024 data. To fill the new stats
for current seasons, clear the two checkpoints and re-pull:
    rm data/checkpoints/stats_season_2024.cursor data/checkpoints/stats_season_2025.cursor
    python3 src/collect/fetch_balldontlie_stats.py 2024 2025 2026

## How to run
    cd ~/sports-betting-bot && source .venv/bin/activate
    streamlit run dashboard/app.py          # the dashboard

## How to REFRESH (the standard way — do this to update data)
Just run the one launcher. It pulls new games, fills opponents/home-away,
refreshes injuries, and grades your logged predictions — in the right order,
every time. Never run the pieces by hand; this prevents the "opponents missing"
problem after a download.

    ./refresh.command            # from the project folder (or double-click it in Finder)

Under the hood that runs `python3 src/collect/daily_update.py`. To automate it
daily at 9am, add this to `crontab -e`:
    0 9 * * * cd ~/sports-betting-bot && ./.venv/bin/python src/collect/daily_update.py >> data/daily_update.log 2>&1

## Other commands
    python3 src/backtest/backtest.py        # backtest a preset from the CLI
    python3 src/models/clear_predictions.py # reset your prediction track record
