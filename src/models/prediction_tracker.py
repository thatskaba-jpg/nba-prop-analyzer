"""
prediction_tracker.py — Phase 9: log predictions and grade them against reality.

The backtest (Phase 8) tests the model on the PAST. This module tracks it going
FORWARD: every prediction you make is written to the `predictions` table, and
once that game is played and its box score lands in the database, the prediction
is automatically graded (correct / incorrect). Over time this gives you the
model's REAL-WORLD hit rate — the brief's "track predictions even when I do not
place a bet, and use paper betting before risking real money."

Three jobs:
  - log_prediction(...):  record one prediction (idempotent per player/game/prop).
  - grade_pending():      fill in results for predictions whose games are now in
                          the data, with NO leakage (we only grade after the fact).
  - tracking_summary():   live accuracy / record of all graded predictions.

Nothing here fabricates data: results come only from real box scores already
stored in player_game_logs.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from database import get_connection, init_schema  # noqa: E402
from features.features import COMBO_PARTS  # noqa: E402
from grading import PENDING, VOID, settle_stat  # noqa: E402

# Prop types that map straight to one box-score column (combos use COMBO_PARTS).
_SINGLE_COLS = {"points", "rebounds", "assists", "threes_made", "steals",
                "blocks", "turnovers"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_prediction(player_id, player_name, game_date, prop_type, line,
                   projection, pick, prob, opponent=None, odds=None,
                   ev_pct=None, model_name="default"):
    """Record one prediction. FIRST WRITE WINS.

    Re-logging the same player/game/prop/line/model is a no-op: the original
    pick, probability and timestamp stay exactly as first recorded. (It used to
    overwrite them, so a re-scan after tip-off could quietly rewrite history and
    a forward track record is only worth anything if it can't be edited after
    the fact.) Returns True if a new row was written, False if it already
    existed."""
    init_schema()
    conn = get_connection()
    cur = conn.execute(
        """
        INSERT INTO predictions
          (created_at, model_name, player_id, player_name, game_date, opponent,
           prop_type, line, projection, pick, prob, odds, ev_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(player_id, game_date, prop_type, line, model_name)
        DO NOTHING
        """,
        (_now(), model_name, int(player_id), player_name, game_date, opponent,
         prop_type, float(line), projection, pick, prob, odds, ev_pct),
    )
    inserted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def _result_for(conn, player_id, game_date, prop_type):
    """How a logged prediction settled: (status, actual).

      (PENDING, None)  the game isn't in the box-score data yet
      (VOID, None)     the player logged 0 minutes (DNP): books refund the bet
      (FINAL, value)   the player's real stat for that game
    """
    row = conn.execute(
        "SELECT minutes, points, rebounds, assists, threes_made, steals, "
        "blocks, turnovers FROM player_game_logs "
        "WHERE player_id = ? AND game_date = ?",
        (player_id, game_date)).fetchone()
    return settle_stat(row, prop_type, COMBO_PARTS, _SINGLE_COLS)


def grade_pending():
    """Grade every pending prediction whose game result is now available.

    A prediction is 'pending' until the game's box score exists in the data.
    We never grade early — only after the real result is in — so there's no
    look-ahead. A prediction on a player who didn't play (0 minutes) is VOIDED,
    not graded: it is stamped processed so it leaves the pending pile, but
    result and correct stay empty so it never counts as a win or a loss.
    Returns how many were graded this run (voids are not counted).
    """
    conn = get_connection()
    pending = conn.execute(
        "SELECT id, player_id, game_date, prop_type, line, pick "
        "FROM predictions WHERE graded_at IS NULL").fetchall()
    graded = 0
    for p in pending:
        status, actual = _result_for(conn, p["player_id"], p["game_date"],
                                     p["prop_type"])
        if status == PENDING:
            continue  # game not played / not in data yet — stay pending
        if status == VOID:
            conn.execute("UPDATE predictions SET graded_at = ? WHERE id = ?",
                         (_now(), p["id"]))
            continue
        if actual == p["line"]:
            correct = None  # push — no win/loss (rare on half-point lines)
        elif p["pick"] == "over":
            correct = 1 if actual > p["line"] else 0
        else:
            correct = 1 if actual < p["line"] else 0
        conn.execute(
            "UPDATE predictions SET result = ?, correct = ?, graded_at = ? "
            "WHERE id = ?", (actual, correct, _now(), p["id"]))
        graded += 1
    conn.commit()
    conn.close()
    return graded


def tracking_summary(model_name=None):
    """Live record of graded predictions: count, accuracy, and recent rows."""
    conn = get_connection()
    where = "WHERE correct IS NOT NULL"
    params = []
    if model_name:
        where += " AND model_name = ?"
        params.append(model_name)
    rows = conn.execute(
        f"SELECT COUNT(*) n, SUM(correct) hits FROM predictions {where}",
        params).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE graded_at IS NULL").fetchone()[0]
    # Voided = processed but never settled (the player didn't play).
    voided = conn.execute(
        "SELECT COUNT(*) FROM predictions "
        "WHERE graded_at IS NOT NULL AND result IS NULL").fetchone()[0]
    # Pending for more than 3 days with no box score: the game date was probably
    # wrong (e.g. logged against a day the team didn't play), so it never grades.
    stale = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE graded_at IS NULL "
        "AND game_date < date('now', '-3 day')").fetchone()[0]
    recent = conn.execute(
        f"SELECT player_name, game_date, prop_type, line, pick, prob, "
        f"result, correct FROM predictions {where} "
        f"ORDER BY game_date DESC LIMIT 20", params).fetchall()
    conn.close()
    n = rows["n"] or 0
    hits = rows["hits"] or 0
    return {
        "graded": n,
        "pending": pending,
        "void": voided,
        "stale": stale,
        "accuracy": round(hits / n, 4) if n else None,
        "recent": [dict(r) for r in recent],
    }


if __name__ == "__main__":
    # Smoke test: log a prediction for a real past game, grade it, show summary.
    init_schema()
    conn = get_connection()
    row = conn.execute(
        "SELECT player_id, player_name, game_date, opponent, prop_type_dummy "
        "FROM (SELECT player_id, player_name, game_date, opponent, 1 "
        "AS prop_type_dummy FROM player_game_logs ORDER BY game_date DESC "
        "LIMIT 1)").fetchone()
    conn.close()
    if row:
        log_prediction(row["player_id"], row["player_name"], row["game_date"],
                       "points", 19.5, projection=21.0, pick="over", prob=0.58,
                       opponent=row["opponent"], model_name="smoketest")
        g = grade_pending()
        s = tracking_summary("smoketest")
        print(f"Logged 1, graded {g}. Summary: {s['graded']} graded, "
              f"accuracy {s['accuracy']}, {s['pending']} pending.")
