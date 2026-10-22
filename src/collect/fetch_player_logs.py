"""
fetch_player_logs.py — Phase 3, first step: pull REAL NBA game logs.

What this does, in plain English:
1. Looks up a player by name (e.g. "LeBron James") to get their NBA ID.
2. Downloads their game-by-game log for a given season from NBA.com.
3. Cleans it into just the columns we care about.
4. Saves it into our local database so we never have to re-download it.

IMPORTANT about nba_api:
- It talks to NBA.com's own (unofficial) endpoints. It's free but has no
  guarantees. NBA.com rate-limits requests, so we add a short pause between
  calls. If you ever get timeouts, wait a minute and try again.

How to run (from the project root folder):
    python3 src/collect/fetch_player_logs.py "LeBron James" 2023-24
"""

import sys
import time
from pathlib import Path

# Make "src" importable so we can reuse database.py.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection, init_schema  # noqa: E402

try:
    from nba_api.stats.static import players
    from nba_api.stats.endpoints import playergamelog
except ImportError:
    print("nba_api is not installed yet. Run:  pip install -r requirements.txt")
    sys.exit(1)

# NBA.com blocks requests that don't look like a real browser, and the
# connection is often slow. These headers make us look like a browser, and
# we use a generous timeout plus retries to survive the flaky connection.
NBA_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
}
TIMEOUT_SECONDS = 60
MAX_RETRIES = 4


def find_player_id(player_name: str):
    """Return the NBA player_id for a full name, or None if not found."""
    matches = players.find_players_by_full_name(player_name)
    if not matches:
        return None
    # find_players_by_full_name returns a list; take the first exact-ish match.
    return matches[0]["id"], matches[0]["full_name"]


def fetch_and_store(player_name: str, season: str):
    """Download one player's game log for one season and store it.

    season format is NBA style: "2023-24".
    """
    init_schema()

    result = find_player_id(player_name)
    if result is None:
        print(f"Could not find a player named '{player_name}'. Check spelling.")
        return
    player_id, full_name = result
    print(f"Found: {full_name} (id={player_id}). Fetching {season} game log...")

    # Be polite to NBA.com — a brief pause avoids rate-limit blocks.
    time.sleep(1)

    # NBA.com is flaky: it times out or blocks often. Retry a few times with
    # growing waits before giving up, so a single hiccup doesn't stop us.
    df = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id,
                season=season,
                headers=NBA_HEADERS,
                timeout=TIMEOUT_SECONDS,
            )
            df = log.get_data_frames()[0]
            break
        except Exception as e:
            wait = attempt * 5  # 5s, 10s, 15s, ...
            print(f"  Attempt {attempt}/{MAX_RETRIES} failed ({type(e).__name__}). "
                  f"Retrying in {wait}s...")
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    if df is None:
        print("All attempts to reach NBA.com failed. This is usually temporary.")
        print("Tips: wait a minute and re-run, check your internet, or try")
        print("turning off any VPN/corporate proxy that may block stats.nba.com.")
        return

    if df.empty:
        print(f"No games found for {full_name} in {season}.")
        return

    conn = get_connection()
    stored = 0
    for _, row in df.iterrows():
        # GAME_DATE comes as "OCT 24, 2023"; convert to YYYY-MM-DD for safe ordering.
        from datetime import datetime
        game_date = datetime.strptime(row["GAME_DATE"], "%b %d, %Y").strftime("%Y-%m-%d")

        matchup = row["MATCHUP"]            # e.g. "LAL vs. BOS" (home) or "LAL @ BOS" (away)
        is_home = 1 if "vs." in matchup else 0
        opponent = matchup.split()[-1]      # last token is the opponent abbreviation

        conn.execute(
            """
            INSERT OR REPLACE INTO player_game_logs
            (player_id, player_name, game_id, game_date, season, matchup,
             is_home, opponent, minutes, points, rebounds, assists, threes_made)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                player_id, full_name, row["Game_ID"], game_date, season, matchup,
                is_home, opponent, float(row["MIN"]), int(row["PTS"]),
                int(row["REB"]), int(row["AST"]), int(row["FG3M"]),
            ),
        )
        stored += 1
    conn.commit()
    conn.close()

    print(f"Stored {stored} games for {full_name} ({season}) in the database.")
    print("Sample of the most recent 5 games:")
    print(df[["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "FG3M"]].head(5).to_string(index=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print('Usage: python3 src/collect/fetch_player_logs.py "Player Name" SEASON')
        print('Example: python3 src/collect/fetch_player_logs.py "LeBron James" 2023-24')
        sys.exit(1)
    fetch_and_store(sys.argv[1], sys.argv[2])
