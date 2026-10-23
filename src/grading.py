"""
grading.py — the ONE place that decides how a box-score row settles a prop.

Both graders (odds/line_capture.py for stored sportsbook lines, and
models/prediction_tracker.py for logged predictions) used to read a stat
straight out of player_game_logs. That quietly graded a DNP (0 minutes, every
stat 0) as an UNDER. Sportsbooks void a prop when the player doesn't play, so a
DNP is neither an over nor an under. Keeping the rule in one module stops the
two graders from drifting apart again.

settle_stat() returns a (status, value) pair:
  (PENDING, None)   no box-score row yet (game not logged), or the stat is blank
  (VOID, None)      the player logged 0 / missing minutes: the bet is refunded
  (FINAL, value)    the real stat, safe to grade over/under against
"""

PENDING = "pending"
VOID = "void"
FINAL = "final"


def did_play(minutes) -> bool:
    """True if a box-score row is a real appearance (more than 0 minutes)."""
    try:
        return minutes is not None and float(minutes) > 0
    except (TypeError, ValueError):
        return False


def settle_stat(row, prop_type, combo_parts, single_cols):
    """Classify one player-game row for one prop type.

    row          a sqlite3.Row or dict from player_game_logs (or None if the
                 game isn't logged yet).
    combo_parts  {prop_type: (column, ...)} for sum-of-columns props (pra, pr..).
    single_cols  the set of prop types that map straight to one column.

    A blank stat on a game the player DID play is "pending", not 0: we would
    rather leave a line ungraded than grade it against made-up data.
    """
    if row is None:
        return PENDING, None
    if "minutes" in row.keys() and not did_play(row["minutes"]):
        return VOID, None
    try:
        if prop_type in combo_parts:
            return FINAL, float(sum(row[c] for c in combo_parts[prop_type]))
        if prop_type in single_cols:
            value = row[prop_type]
            if value is not None:
                return FINAL, float(value)
    except (KeyError, IndexError, TypeError):
        pass
    return PENDING, None
