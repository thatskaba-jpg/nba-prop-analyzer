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

## What the evidence says (Oct 2026)
Measured on ~570K graded real lines (2025-26 season), out of sample, at the real
posted prices, DNPs voided:
- The old model's "value" flags averaged about -5.8% ROI per bet (about -4.4%
  after calibration), no better than picking at random after the vig. The raw
  model alone scores worse than a coin flip on log loss.
- A projection FITTED to history (src/models/fitted_projection.py) is clearly
  better than the hand-tuned one (error 2.62 vs 2.72 points, line itself 2.58)
  and is the first to add a small, statistically visible amount beyond the line
  (blend weight about 0.18, log-loss gain about 0.0008).
- Shopping across books is worth about +3 points of ROI per bet (the best price
  vs the average); "stale number" flags did not pay (realized ROI about 0).
- Elo (results only) picks game winners 68% on 1,558 held-out games; the trained
  ML model 64%; always-home 55%. The hand-built game model can't be backtested,
  so it is judged by the frozen-picks table on the Game Lines tab.
So probabilities are market-anchored (src/odds/market_anchor.py), and the
forward test (paper bets, frozen picks, closing line value) decides what's real.

## How things are settled
- A player who logs 0 minutes is VOIDED (books refund the bet), never an under.
- Predictions, paper bets and frozen picks are first-write-wins.
- Nothing is called an edge until the forward record, not a backtest, says so.

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
