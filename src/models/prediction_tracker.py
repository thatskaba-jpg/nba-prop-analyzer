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


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log_prediction(player_id, player_name, game_date, prop_type, line,
                   projection, pick, prob, opponent=None, odds=None,
                   ev_pct=None, model_name="default"):
    """Record one prediction. Idempotent: re-logging the same player/game/prop/
    line/model updates it rather than duplicating."""
    init_schema()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO predictions
          (created_at, model_name, player_id, player_name, game_date, opponent,
           prop_type, line, projection, pick, prob, odds, ev_pct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(player_id, game_date, prop_type, line, model_name)
        DO UPDATE SET projection=excluded.projection, pick=excluded.pick,
                      prob=excluded.prob, odds=excluded.odds,
                      ev_pct=excluded.ev_pct, created_at=excluded.created_at
        """,
        (_now(), model_name, int(player_id), player_name, game_date, opponent,
         prop_type, float(line), projection, pick, prob, odds, ev_pct),
    )
    conn.commit()
    conn.close()


def _actual_stat(conn, player_id, game_date, prop_type):
    """The player's real stat for that game, or None if not in the data yet."""
    row = conn.execute(
        "SELECT points, rebounds, assists, threes_made, steals, blocks, "
        "turnovers FROM player_game_logs WHERE player_id = ? AND game_date = ?",
        (player_id, game_date)).fetchone()
    if not row:
        return None
    if prop_type in COMBO_PARTS:
        return sum((row[c] or 0) for c in COMBO_PARTS[prop_type])
    val = row[prop_type] if prop_type in row.keys() else None
    return None if val is None else float(val)


def grade_pending():
    """Grade every pending prediction whose game result is now available.

    A prediction is 'pending' until the game's box score exists in the data.
    We never grade early — only after the real result is in — so there's no
    look-ahead. Returns how many were graded this run.
    """
    conn = get_connection()
    pending = conn.execute(
        "SELECT id, player_id, game_date, prop_type, line, pick "
        "FROM predictions WHERE result IS NULL").fetchall()
    graded = 0
    for p in pending:
        actual = _actual_stat(conn, p["player_id"], p["game_date"], p["prop_type"])
        if actual is None:
            continue  # game not played / not in data yet — stay pending
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
        "SELECT COUNT(*) FROM predictions WHERE result IS NULL").fetchone()[0]
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
