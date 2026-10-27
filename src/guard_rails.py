"""
guard_rails.py — warnings that appear BEFORE you bet.

Pure function: pass in how old each live input is and it returns the list of
things to double-check. Nothing here changes a pick; it flags when the inputs
behind a pick are stale or the model is still unproven.
"""
INJURY_MAX_MIN = 30        # injuries older than this are stale on game day
LINES_MAX_MIN = 5          # live lines older than this are stale
PROVEN_AFTER = 300         # graded forward-test games before we call it proven


def warnings(now, injuries_ts=None, lines_ts=None, lineups_ts=None,
             soon_games=0, soon_without_lineup=0, graded_n=0):
    """now / *_ts are epoch seconds (None = never). soon_games = open games that
    tip within 90 minutes; soon_without_lineup = how many of those have no
    posted lineup in the stored check. Returns a list of strings."""
    out = []
    if lines_ts is None:
        out.append("Live lines haven't loaded. Refresh before betting.")
    elif (now - lines_ts) / 60 > LINES_MAX_MIN:
        out.append(f"Lines are {int((now - lines_ts) / 60)} min old. "
                   "Hit Refresh games: prices move.")
    if injuries_ts is None:
        out.append("No injury data yet.")
    elif (now - injuries_ts) / 60 > INJURY_MAX_MIN:
        age = (now - injuries_ts) / 60
        out.append("Injuries are " + (f"{int(age)} min" if age < 180
                                       else f"{age / 60:.0f} h") + " old. "
                   "Press Check all live lineups + injuries.")
    if soon_games:
        if lineups_ts is None:
            out.append(f"{soon_games} game(s) tip within 90 min and you haven't "
                       "checked lineups yet.")
        elif soon_without_lineup:
            out.append(f"{soon_without_lineup} game(s) tip within 90 min with no "
                       "posted lineup yet, so starters are a guess.")
    return out


def proof_note(graded_n) -> str:
    """One honest line about how much evidence exists for the picks."""
    if graded_n >= PROVEN_AFTER:
        return ""
    return (f"Model unproven: only {graded_n} forward-test game(s) graded "
            f"(about {PROVEN_AFTER} needed before any win rate means much).")


def max_stake(bankroll, pct=1.0):
    """Suggested max per bet: pct% of bankroll (0 if no bankroll set)."""
    try:
        b = float(bankroll)
    except (TypeError, ValueError):
        return 0.0
    return round(max(b, 0.0) * pct / 100.0, 2)
