"""
teammate_injuries.py — game-day usage boost when a player's TEAMMATES are out.

WHY THIS MATTERS (the highest-upside signal). A player's prop line depends
heavily on who else is playing. If a team's primary scorer sits, the remaining
players soak up those shots, minutes, and touches — their points/assists/
rebounds lines should rise. Sportsbooks are often SLOW to fully price a
last-minute injury, which is exactly where a small bettor finds real edge. Our
model was blind to this: the existing injury factor only knew if THE PLAYER
HIMSELF was hurt, not his teammates.

HOW IT WORKS (honest, no fabrication).
  1. Resolve each player's CURRENT team from their most recent game's `matchup`
     field (e.g. "DET vs BOS" -> DET). That's the only team signal in our logs.
  2. Group players by current team to get each team's active roster (as we know
     it from who has played recently).
  3. Cross-reference the `injuries` table (ESPN + balldontlie feeds already
     merged there) for teammates listed OUT / DOUBTFUL.
  4. Produce {player_id: [ (teammate_name, status), ... ]} — the OUT/DOUBTFUL
     teammates for each player. The factor turns that into a gentle usage boost.

We do NOT try to rank teammates by stardom yet (we don't store usage rate), so
the boost scales with the NUMBER of significant teammates out, capped. A future
improvement could weight by the out teammate's own scoring average.

LEAK-SAFE: injuries reflect CURRENT status, used only to project an UPCOMING
game — never applied to historical grading (the backtest passes no injury ctx).
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402

# Only these statuses meaningfully free up usage. "Day-To-Day"/"Questionable"
# are too uncertain to boost on (the player might play), so we ignore them here.
_OUT_STATUSES = ("out", "doubtful")


def _team_from_matchup(matchup: str, player_is_home) -> str:
    """'DET vs BOS' or 'DET @ BOS' -> the player's own team (first token)."""
    if not matchup:
        return None
    # The player's team is always listed first in our stored matchup strings.
    for sep in (" vs ", " @ ", " vs. "):
        if sep in matchup:
            return matchup.split(sep)[0].strip()
    return matchup.strip().split()[0] if matchup.strip() else None


def build_out_teammates_map():
    """{player_id: [(teammate_name, status), ...]} for OUT/DOUBTFUL teammates.

    One efficient pass: find each player's current team from their latest game,
    then join against the injuries table. Returns an empty dict if there are no
    injuries recorded (so the factor is simply a no-op).
    """
    conn = get_connection()
    # Does the injuries table exist and have rows?
    has_inj = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='injuries'"
    ).fetchone()
    if not has_inj:
        conn.close()
        return {}

    # Current team per player, resolved from their MOST RECENT game of all time.
    # We must NOT window this: an injured player (and early in a new season,
    # everyone) may have last played last season, yet their team is still known
    # from that last game. Keyed by player NAME because the same person can hold
    # several player_ids across our data sources — name is the stable identity
    # the injuries feed also uses.
    rows = conn.execute(
        """
        SELECT player_id, player_name, matchup, is_home
        FROM player_game_logs l
        WHERE game_date = (
            SELECT MAX(game_date) FROM player_game_logs
            WHERE player_name = l.player_name)
        """).fetchall()

    team_of = {}            # player_id -> team abbr
    roster = {}             # team abbr -> list of (player_id, player_name)
    name_of = {}
    for r in rows:
        team = _team_from_matchup(r["matchup"], r["is_home"])
        if not team:
            continue
        pid = r["player_id"]
        team_of[pid] = team
        name_of[pid] = r["player_name"]
        roster.setdefault(team, []).append((pid, r["player_name"]))

    # Which players are currently OUT/DOUBTFUL?
    out_by_name = {}
    for r in conn.execute("SELECT player_name, status FROM injuries"):
        st = (r["status"] or "").strip().lower()
        if any(s in st for s in _OUT_STATUSES):
            out_by_name[r["player_name"]] = r["status"]

    # Each OUT teammate's recent scoring average (points per game over their
    # last ~20 played games). This measures how much usage their absence frees
    # up: a 25-ppg star sitting matters far more than a 4-ppg bench player.
    ppg_by_name = {}
    if out_by_name:
        placeholders = ",".join("?" * len(out_by_name))
        for r in conn.execute(
                f"""SELECT player_name, AVG(points) ppg FROM (
                      SELECT player_name, points FROM player_game_logs
                      WHERE player_name IN ({placeholders})
                        AND minutes > 0
                      ORDER BY game_date DESC)
                    GROUP BY player_name""", list(out_by_name)):
            ppg_by_name[r["player_name"]] = r["ppg"] or 0.0
    conn.close()

    if not out_by_name:
        return {}

    # For each player, list OUT teammates (same team, different person).
    # Dedupe the OUT set by name — a teammate with several player_ids must not
    # be counted more than once.
    out_map = {}
    for team, members in roster.items():
        seen = set()
        out_here = []
        for (pid, nm) in members:
            if nm in out_by_name and nm not in seen:
                seen.add(nm)
                out_here.append((nm, out_by_name[nm], ppg_by_name.get(nm, 0.0)))
        if not out_here:
            continue
        for (pid, nm) in members:
            mates = [(onm, ost, oppg) for (onm, ost, oppg) in out_here
                     if onm != nm]
            if mates:
                out_map[pid] = mates
    return out_map


if __name__ == "__main__":
    m = build_out_teammates_map()
    print(f"Players with OUT teammates: {len(m)}")
    shown = 0
    for pid, mates in m.items():
        print(f"  player {pid}: "
              + ", ".join(f"{n} ({s}, {ppg:.1f} ppg)" for n, s, ppg in mates))
        shown += 1
        if shown >= 15:
            break
