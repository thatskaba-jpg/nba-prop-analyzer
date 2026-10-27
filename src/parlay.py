"""
parlay.py — a quick view of the most likely winners, plus the parlay math.

Legs are the model's most likely WINNER in each game (highest win chance,
skipping games where the book disagrees with us by too much), priced at that
team's moneyline. Pure functions; unit-tested.

Reality check baked into the numbers: a parlay multiplies every leg's win
chance (so it hits less often than any single leg) AND every leg's built-in
sportsbook cut. The summary shows the model's combined chance next to the
break-even chance the payout implies, so you can see what you're paying.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from team_names import full  # noqa: E402


def to_decimal(american):
    a = float(american)
    return 1 + (a / 100.0 if a > 0 else 100.0 / -a)


def to_american(decimal):
    if decimal <= 1.0:
        return None
    return int(round((decimal - 1) * 100)) if decimal >= 2.0 else int(round(-100 / (decimal - 1)))


def candidates(entries, worst_price=None):
    """Most-likely-winner legs, best first.

    entries: the per-game dicts the Game Lines tab builds (away, home,
    p_home_win, p_away_win, ml_home, ml_away, tip, skip). worst_price: e.g. -300
    to drop heavy favorites (price worse than that). Games with no price for the
    favorite are left out (no payout can be computed)."""
    legs = []
    for e in entries:
        if e.get("skip"):
            continue
        home_fav = e["p_home_win"] >= e["p_away_win"]
        abbr = e["home"] if home_fav else e["away"]
        odds = e.get("ml_home") if home_fav else e.get("ml_away")
        if odds is None:
            continue
        if worst_price is not None and float(odds) < worst_price:
            continue
        legs.append({"team": full(abbr), "abbr": abbr,
                     "opponent": full(e["away"] if home_fav else e["home"]),
                     "p": max(e["p_home_win"], e["p_away_win"]),
                     "odds": int(odds), "tip": e.get("tip") or ""})
    legs.sort(key=lambda l: -l["p"])
    return legs


def summarize(legs, stake=100.0):
    """Combined numbers for a parlay of `legs` (empty -> None)."""
    if not legs:
        return None
    p = 1.0
    dec = 1.0
    for l in legs:
        p *= l["p"]
        dec *= to_decimal(l["odds"])
    return {"legs": len(legs), "model_chance": p, "book_breakeven": 1.0 / dec,
            "odds": to_american(dec), "decimal": dec,
            "payout": round((dec - 1) * stake, 2), "stake": stake}
