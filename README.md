# Sports Betting Research Bot

A data-driven research tool that analyzes NBA player props, estimates
probabilities, compares them to sportsbook odds, and **honestly tests itself**.
It does not place bets and makes no guarantees. It helps you make informed decisions.

## The rules that matter most
1. Every prediction uses **only data available before that game started.**
2. A "value" label is only worth what it did on games that hadn't happened yet.
   The app keeps a forward record (paper bets, frozen picks) that can't be
   backfilled or edited, and shows it next to the claims.

## Project layout
```
sports-betting-bot/
├── .env                  your secret API keys (create from .env.example)
├── requirements.txt      Python libraries
├── data/                 the SQLite database (data/bot.db), calibration + model files
├── src/
│   ├── database.py       storage layer (SQLite)
│   ├── grading.py        the ONE rule for settling a prop (a DNP is voided)
│   ├── collect/          download & store data; daily_update.py, intraday.py
│   ├── features/         leak-free pre-game inputs
│   ├── models/           projections, calibration, fitted_projection, paper_bets, elo
│   ├── odds/             sportsbook prices, EV math, market_anchor
│   └── backtest/         the honesty checks (see "Is the model any good?")
├── tests/                unit tests (python -m unittest discover -s tests)
├── dashboard/app.py      Streamlit dashboard
└── api/main.py           small JSON API (bind to 127.0.0.1; it has no auth)
```

## Setup (one time)

1. Open Terminal and go to the project:
   ```
   cd ~/sports-betting-bot
   ```
2. Create an isolated Python environment (keeps this project's libraries separate):
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   ```
   You'll know it worked when your prompt shows `(.venv)`.
3. Install the libraries:
   ```
   pip install -r requirements.txt
   ```
4. Create your secrets file:
   ```
   cp .env.example .env
   ```

## Next time you open Terminal
```
cd ~/sports-betting-bot
source .venv/bin/activate
streamlit run dashboard/app.py
```

## Run the tests
```
python -m unittest discover -s tests
```

## Keeping it fresh (two scheduled jobs)
`crontab -e`, with the machine on US-Eastern time:
```
# nightly: box scores, rosters, injuries, grading, weekly model retrain
0 9 * * *            cd ~/sports-betting-bot && ./.venv/bin/python src/collect/daily_update.py >> data/daily_update.log 2>&1
# game days: prices, injury/lineup news, paper bets, closing lines (the last run before tip is the "close")
0 12,15,17,18,19 * * * cd ~/sports-betting-bot && ./.venv/bin/python src/collect/intraday.py >> data/intraday.log 2>&1
```
Both record what worked in `data/pipeline_status.json`; the dashboard warns when
data is stale or a key step failed. `refresh.command` runs the nightly job by hand.

## Is the model any good? (run these, don't trust the labels)
| Question | Command |
|---|---|
| Does the model know anything the betting line doesn't? | `python src/backtest/market_edge_test.py` (add `--save` to refit the weights the app uses) |
| Is the fitted projection better than the rules-based one? | `python src/backtest/projection_eval.py` (`--save-anchor` to store its weights) |
| How did flagged bets do at real prices, out of sample? | `python src/models/calibration.py fit --holdout 2026-02-01`, then `python src/backtest/real_line_validation.py` |
| What is the best price across books worth? | `python src/backtest/line_shopping_test.py` |
| Which way of picking game winners holds up? | `python src/backtest/game_model_eval.py` |
| How are the forward paper bets doing? | `python src/models/paper_bets.py` (also on the Track Record tab) |

After changing anything that feeds a probability, refit: `python src/models/calibration.py fit`
and `python src/backtest/market_edge_test.py --save`.

## Status
Phases 1 to 9 are built (data, projections, odds + EV, dashboard, backtests,
scheduled updates). The current work is evidence: see `PROGRESS.md`.
