"""
line_shopping.py — compare the SAME player prop across every sportsbook and
find the best available price for each side.

WHY THIS IS THE REAL EDGE:
  A box-score model does NOT out-predict the sportsbooks' consensus line — we
  proved that in the backtest (points ~50% vs the 52.4% break-even). But the
  books do NOT all post the same number. On one game tonight, the SAME player's
  points prop was 20.5 at Caesars and 25.5 at FanDuel — a 5-point gap. That gap
  is a genuine, exploitable inefficiency: you take the OVER at the book with the
  LOW line and the UNDER at the book with the HIGH line. You're not out-guessing
  the market, you're buying the best number the market itself is offering.

WHAT THIS DOES:
  For every player prop on today's slate, it collects each book's line + odds,
  then reports:
    - best_over:  the book offering the lowest line / best over price
    - best_under: the book offering the highest line / best under price
    - line_spread: how far apart the books are (the size of the opportunity)
    - consensus:  the median line across books (the "fair" number)

  Books disagreeing a lot (a big line_spread) is where the value is. A prop
  every book agrees on has no shopping edge.

DATA: balldontlie v2/odds/player_props, ALL books, ALL pages (the pagination
fix — otherwise we'd only see a few books and miss the gaps).
"""

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Reuse the paginated prop fetch + prop-name map + DB name resolution that the
# primary props client already builds (so names/ids match the rest of the app).
from odds.balldontlie_props import (  # noqa: E402
    _PROP_MAP, _get_all, _todays_game_ids, _id_to_name, _name_key, has_key,
    BDLPropsError,
)


def _american_to_prob(odds):
    """Convert American odds to the implied win probability (0-1).
    -110 -> 0.524, +120 -> 0.455. Used to compare prices fairly."""
    try:
        o = int(odds)
    except (TypeError, ValueError):
        return None
    if o < 0:
        return (-o) / ((-o) + 100.0)
    return 100.0 / (o + 100.0)


def shop_all_props():
    """Every player prop on today's slate, with each book's line/odds and the
    best price per side.

    Returns {name_key: {prop_type: {
        consensus_line, line_spread, books (int),
        best_over:  {book, line, odds},
        best_under: {book, line, odds},
        all: [{book, line, over, under}, ...],
    }}}
    Empty dict if no key / no props / rate-limited (raises BDLPropsError on 429).
    """
    if not has_key():
        return {}
    id_name = _id_to_name()
    # (player_id, prop_type) -> list of per-book quotes
    acc = defaultdict(lambda: defaultdict(list))
    for gid in _todays_game_ids():
        for p in _get_all("v2/odds/player_props", {"game_id": gid}):
            prop = _PROP_MAP.get(p.get("prop_type"))
            if not prop:
                continue
            mkt = p.get("market") or {}
            if mkt.get("type") != "over_under":
                continue
            pid = p.get("player_id")
            if pid is None:
                continue
            try:
                line = float(p["line_value"])
            except (TypeError, ValueError):
                continue
            acc[pid][prop].append({
                "book": p.get("vendor"),
                "line": line,
                "over": mkt.get("over_odds"),
                "under": mkt.get("under_odds"),
            })

    out = {}
    for pid, props in acc.items():
        name = id_name.get(pid)
        if not name:
            continue
        nk = _name_key(name)
        for prop, quotes in props.items():
            quotes = [q for q in quotes if q["book"]]
            if len(quotes) < 2:
                continue  # need at least two books to "shop"
            lines = [q["line"] for q in quotes]
            consensus = round(statistics.median(lines) * 2) / 2

            # BEST OVER = lowest line; tie-break on the better (higher) over
            # price. A lower number is easier to go over.
            def over_key(q):
                return (q["line"], -(_american_to_prob(q["over"]) or 1))
            best_over = min(quotes, key=over_key)

            # BEST UNDER = highest line; tie-break on the better under price.
            def under_key(q):
                return (-q["line"], -(_american_to_prob(q["under"]) or 1))
            best_under = min(quotes, key=under_key)

            out.setdefault(nk, {})[prop] = {
                "player": name,
                "consensus_line": consensus,
                "line_spread": round(max(lines) - min(lines), 1),
                "books": len(quotes),
                "best_over": {"book": best_over["book"],
                              "line": best_over["line"],
                              "odds": best_over["over"]},
                "best_under": {"book": best_under["book"],
                               "line": best_under["line"],
                               "odds": best_under["under"]},
                "all": sorted(quotes, key=lambda q: q["line"]),
            }
    return out


def biggest_gaps(limit=25):
    """The props with the widest disagreement between books (the best shopping
    opportunities), as a flat sorted list, biggest line_spread first."""
    try:
        shopped = shop_all_props()
    except BDLPropsError:
        return []
    flat = []
    for nk, props in shopped.items():
        for prop, info in props.items():
            flat.append({"prop_type": prop, **info})
    flat.sort(key=lambda x: x["line_spread"], reverse=True)
    return flat[:limit]


if __name__ == "__main__":
    if not has_key():
        print("No BALLDONTLIE_API_KEY in .env")
        sys.exit(0)
    gaps = biggest_gaps(15)
    print(f"Biggest cross-book line gaps today ({len(gaps)} shown):\n")
    for g in gaps:
        bo, bu = g["best_over"], g["best_under"]
        print(f"{g['player']:22} {g['prop_type']:12} "
              f"consensus {g['consensus_line']:>5} | spread {g['line_spread']:>4} "
              f"across {g['books']} books")
        print(f"    best OVER  {bo['line']:>5} @ {bo['odds']} ({bo['book']})")
        print(f"    best UNDER {bu['line']:>5} @ {bu['odds']} ({bu['book']})")
