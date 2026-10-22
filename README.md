# Sports Betting Research Bot

A data-driven research tool that analyzes NBA (then NFL) player props, estimates
probabilities, compares them to sportsbook odds, and **honestly backtests itself**.
It does not place bets and makes no guarantees — it helps you make informed decisions.

## The one rule that matters most
Every prediction uses **only data available before that game started.** No peeking
at the future. This is what separates a real model from a fantasy one.

## Project layout
```
sports-betting-bot/
├── .env                  your secret API keys (create from .env.example)
├── requirements.txt      Python libraries
├── data/                 the SQLite database (data/bot.db) lives here
├── src/
│   ├── database.py       storage layer (SQLite)
│   ├── collect/          download & store data        (Phase 3)
│   ├── features/         build prediction inputs       (Phase 5)
│   ├── models/           the models                    (Phase 4)
│   ├── odds/             sportsbook odds + EV math      (Phase 6)
│   └── backtest/         the honesty check             (Phase 8)
└── dashboard/app.py      Streamlit dashboard            (Phase 7)
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
4. Create your secrets file (you can leave it blank until Phase 6):
   ```
   cp .env.example .env
   ```

## Try it — pull real NBA data
With the environment active (`(.venv)` showing):
```
python3 src/collect/fetch_player_logs.py "LeBron James" 2023-24
```
This downloads LeBron's 2023-24 game log from NBA.com and stores it in `data/bot.db`.

## Next time you open Terminal
You only install once. After that, each session just:
```
cd ~/sports-betting-bot
source .venv/bin/activate
```

## Phases
1. Pick data sources ✅ (NBA stats = free via `nba_api`)
2. Project setup ✅
3. Collect & store historical data ← **we are here**
4. Baseline statistical model
5. Matchup / injury / role features
6. Sportsbook odds + expected value
7. Dashboard
8. Honest backtesting
9. Scheduled updates + alerts
