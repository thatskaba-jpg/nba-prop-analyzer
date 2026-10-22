"""
show_stats.py — a plain-English window into what's stored in the database.

No SQL to type. Just run it:

    python3 src/show_stats.py                 # overview: totals by season + top players
    python3 src/show_stats.py "LeBron James"  # that player's most recent games

It reads data/bot.db (your stored player box scores) and prints a readable
summary. It never changes anything — it's read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from database import get_connection  # noqa: E402


def overview():
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
    players = conn.execute(
        "SELECT COUNT(DISTINCT player_id) FROM player_game_logs").fetchone()[0]
    span = conn.execute(
        "SELECT MIN(game_date), MAX(game_date) FROM player_game_logs").fetchone()

    print("=" * 60)
    print("WHAT'S IN YOUR DATABASE")
    print("=" * 60)
    print(f"Total player-game rows : {total:,}")
    print(f"Distinct players       : {players:,}")
    print(f"Date range             : {span[0]}  ->  {span[1]}")

    print("\nRows by season:")
    rows = conn.execute(
        "SELECT season, COUNT(*) n, MIN(game_date) mn, MAX(game_date) mx "
        "FROM player_game_logs GROUP BY season ORDER BY season").fetchall()
    for r in rows:
        print(f"  {r['season']:<9} {r['n']:>7,} rows   "
              f"({r['mn']}  ->  {r['mx']})")

    print("\nTop 10 players by games stored:")
    top = conn.execute(
        "SELECT player_name, COUNT(*) c, ROUND(AVG(points),1) ppg "
        "FROM player_game_logs GROUP BY player_id "
        "ORDER BY c DESC LIMIT 10").fetchall()
    for t in top:
        print(f"  {t['player_name']:<26} {t['c']:>4} games   avg {t['ppg']} pts")
    conn.close()
    print("\nTip: see one player's recent games with:")
    print('  python3 src/show_stats.py "Nikola Jokic"')


def player(name):
    conn = get_connection()
    rows = conn.execute(
        "SELECT game_date, season, matchup, opponent, is_home, "
        "minutes, points, rebounds, assists, threes_made "
        "FROM player_game_logs "
        "WHERE player_name LIKE ? ORDER BY game_date DESC LIMIT 15",
        (f"%{name}%",)).fetchall()
    if not rows:
        print(f"No games found for a player matching '{name}'.")
        print("Check spelling, or run with no name to see the top players.")
        conn.close()
        return
    actual_name = rows[0]["player_name"] if "player_name" in rows[0].keys() else name
    print("=" * 74)
    print(f"MOST RECENT 15 GAMES — matching '{name}'")
    print("=" * 74)
    print(f"  {'date':<12}{'opp':<5}{'H/A':<5}{'min':>5}{'pts':>5}"
          f"{'reb':>5}{'ast':>5}{'3pm':>5}")
    for r in rows:
        ha = "home" if r["is_home"] == 1 else ("away" if r["is_home"] == 0 else "?")
        opp = r["opponent"] or "?"
        mins = f"{r['minutes']:.0f}" if r["minutes"] is not None else "?"
        print(f"  {r['game_date']:<12}{opp:<5}{ha:<5}{mins:>5}"
              f"{r['points']:>5}{r['rebounds']:>5}{r['assists']:>5}"
              f"{r['threes_made']:>5}")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        player(" ".join(sys.argv[1:]))
    else:
        overview()
