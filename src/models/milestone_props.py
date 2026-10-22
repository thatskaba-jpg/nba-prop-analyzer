"""
milestone_props.py — Double-Double / Triple-Double props (yes/no outcomes).

WHY THIS IS SEPARATE from the Over/Under props:
  Points/rebounds/etc. are COUNTS — "over or under 25.5" — so we model them with
  a count distribution (Poisson / Negative Binomial). A Double-Double is a YES/NO
  EVENT: did the player reach 10+ in at least TWO of points/rebounds/assists/
  steals/blocks? That's a probability-of-an-event question, which the brief
  explicitly says to handle with logistic regression / an event-probability
  model — not an over/under line.

WHAT WE DO (honest baseline):
  The cleanest leak-free estimate of P(double-double) is the player's recent RATE
  of double-doubles, shrunk toward a sensible prior when the sample is small
  (so a player with 3 career games doesn't read as 100% or 0%). This is a proper
  logistic-style base rate; a full logistic regression on matchup features is a
  natural Phase-8 extension, and the code is structured so that can slot in.

DEFINITIONS:
  Double-Double: >= 10 in at least 2 of {points, rebounds, assists, steals, blocks}.
  Triple-Double: >= 10 in at least 3 of them.

No leakage: the rate uses only games strictly before the cutoff date.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from features.features import load_player_history  # noqa: E402

CATS = ("points", "rebounds", "assists", "steals", "blocks")
# Shrinkage: blend the player's rate with a league-ish prior, weighted by games.
PRIOR_RATE = {"double_double": 0.12, "triple_double": 0.01}
PRIOR_STRENGTH = 15  # pseudo-games of prior; more games -> less shrink


def _counts_double(row) -> int:
    """How many categories this game reached 10+ in."""
    return sum(1 for c in CATS if (row.get(c) or 0) >= 10)


def milestone_probability(player_id: int, kind: str, cutoff_date: str,
                          history: pd.DataFrame = None) -> dict:
    """P(double-double) or P(triple-double) for a player, leak-free.

    kind: 'double_double' or 'triple_double'.
    Returns probability, the raw recent rate, sample size, and a confidence note.
    """
    if kind not in ("double_double", "triple_double"):
        raise ValueError("kind must be 'double_double' or 'triple_double'")
    need = 2 if kind == "double_double" else 3

    df = history if history is not None else load_player_history(player_id)
    if df.empty:
        return {"probability": None, "rate": None, "games": 0,
                "confidence": "none (no prior games)"}
    prior = df[df["game_date"] < cutoff_date]
    n = len(prior)
    if n == 0:
        return {"probability": None, "rate": None, "games": 0,
                "confidence": "none (no prior games)"}

    hits = sum(1 for _, row in prior.iterrows() if _counts_double(row) >= need)
    raw_rate = hits / n

    # Shrink toward the prior so tiny samples aren't overconfident.
    p0 = PRIOR_RATE[kind]
    prob = (hits + p0 * PRIOR_STRENGTH) / (n + PRIOR_STRENGTH)

    if n < 10:
        conf = f"low (only {n} prior games)"
    elif n < 30:
        conf = f"moderate ({n} prior games)"
    else:
        conf = f"solid ({n} prior games)"

    return {
        "probability": round(prob, 4),
        "rate": round(raw_rate, 4),       # the unshrunk observed rate
        "hits": hits, "games": n,
        "confidence": conf,
    }


if __name__ == "__main__":
    from database import get_connection
    # Demo on a player likely to post double-doubles (most rebounds).
    row = get_connection().execute(
        "SELECT player_id, player_name FROM player_game_logs "
        "GROUP BY player_id ORDER BY AVG(rebounds) DESC LIMIT 1").fetchone()
    pid, name = row["player_id"], row["player_name"]
    hist = load_player_history(pid)
    cutoff = hist.iloc[-1]["game_date"]
    for kind in ("double_double", "triple_double"):
        r = milestone_probability(pid, kind, cutoff, hist)
        print(f"{name} — {kind}: P={r['probability']} "
              f"(observed {r['hits']}/{r['games']} = {r['rate']}, {r['confidence']})")
