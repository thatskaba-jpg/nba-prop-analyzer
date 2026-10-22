"""
fetch_odds.py — Phase 6 (live hookup): pull sportsbook odds from balldontlie
and feed them into the EV engine.

This is deliberately SEPARATE from ev_engine.py: the math is proven on its own,
and this thin adapter just gets real odds into it. It's written defensively —
balldontlie's /v1/odds shape isn't fully pinned down (the trial's 5 req/min
limit was busy with the stats download when this was written), so the parser
adapts to the fields that are present and tells you plainly when a field is
missing instead of crashing or inventing a number.

IMPORTANT — never fabricate odds. If the API returns nothing for a game/date
(common: player-prop odds often populate only near tip-off, and the free/trial
tier may not include props at all), this prints "no odds available" and stops.
It does not make up a line. You then either wait until closer to game time, or
type the odds you see on a sportsbook into evaluate_prop() by hand.

Run (AFTER the stats download finishes, so you're not fighting the rate limit):

    python3 src/odds/fetch_odds.py 2025-11-01     # odds for games on a date
    python3 src/odds/fetch_odds.py --game 18444567 # odds for one game id
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect.balldontlie_client import _get, get_all  # noqa: E402  (reuses auth+rate guard)
from odds.ev_engine import evaluate_prop  # noqa: E402


def fetch_raw_odds(date: str = None, game_id: int = None) -> list:
    """Call /v1/odds and return the raw 'data' list (possibly empty).

    Pass either a date (YYYY-MM-DD) or a game_id. Returns [] when the API has
    no odds for that query — a normal, honest outcome, not an error.
    """
    params = {}
    if game_id is not None:
        params["game_id"] = game_id
    elif date is not None:
        params["date"] = date
    payload = get_all("odds", params)
    return payload if isinstance(payload, list) else []


def normalize_market(raw: dict) -> dict:
    """Pull the fields we need out of one odds record, defensively.

    We look for a two-way over/under market with American prices. balldontlie's
    exact keys may vary, so we try the common ones and report what we found.
    Returns None if we can't find a usable over/under pair.
    """
    # Common shapes seen in odds APIs: {type, over_under, odds_over, odds_under}
    # or nested under 'markets'. We probe gently.
    def pick(d, *keys):
        for k in keys:
            if isinstance(d, dict) and d.get(k) is not None:
                return d[k]
        return None

    line = pick(raw, "over_under", "line", "total", "point")
    odds_over = pick(raw, "odds_over", "over_odds", "over")
    odds_under = pick(raw, "odds_under", "under_odds", "under")
    bookmaker = pick(raw, "bookmaker", "book", "sportsbook", "vendor")

    if odds_over is None or odds_under is None:
        return None
    try:
        return {
            "line": float(line) if line is not None else None,
            "odds_over": int(odds_over),
            "odds_under": int(odds_under),
            "bookmaker": bookmaker,
            "type": pick(raw, "type", "market", "prop_type"),
        }
    except (TypeError, ValueError):
        return None


def odds_with_value(date: str = None, game_id: int = None,
                    our_probs: dict = None) -> list:
    """Fetch odds and, when we have our own probability for a market, attach an
    EV verdict. our_probs maps a market key -> (p_over, p_under).

    Without our_probs this just returns the normalized market odds (still
    useful: it shows the fair no-vig line via the EV engine with no edge call).
    """
    raw_list = fetch_raw_odds(date=date, game_id=game_id)
    out = []
    for raw in raw_list:
        m = normalize_market(raw)
        if not m:
            continue
        entry = dict(m)
        if our_probs:
            key = m.get("type") or "total"
            probs = our_probs.get(key)
            if probs:
                entry["evaluation"] = evaluate_prop(
                    our_p_over=probs[0], our_p_under=probs[1],
                    odds_over=m["odds_over"], odds_under=m["odds_under"])
        out.append(entry)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    date = game_id = None
    if not args:
        print("Usage: fetch_odds.py <YYYY-MM-DD> | --game <game_id>")
        sys.exit(0)
    if args[0] == "--game" and len(args) > 1:
        game_id = int(args[1])
    else:
        date = args[0]

    print(f"Fetching odds (date={date}, game_id={game_id})...")
    print("NOTE: run this AFTER the stats download finishes, or the 5 req/min "
          "trial limit may return HTTP 429.\n")
    try:
        markets = odds_with_value(date=date, game_id=game_id)
    except Exception as e:
        print(f"Request failed: {e}")
        print("If this is HTTP 429, the stats download is using the rate limit "
              "— let it finish, then retry.")
        sys.exit(1)

    if not markets:
        print("No odds available for that query. This is normal when:")
        print("  - player-prop odds haven't posted yet (they appear near tip-off)")
        print("  - the trial/free tier doesn't include this market")
        print("We do NOT fabricate a line. Try a date with games close to tip, "
              "or type real odds into evaluate_prop() by hand.")
        sys.exit(0)

    for m in markets:
        print(f"- {m.get('type','market')} line={m.get('line')} "
              f"over={m['odds_over']:+d} under={m['odds_under']:+d} "
              f"book={m.get('bookmaker')}")
        if "evaluation" in m:
            print(f"    {m['evaluation']['verdict']}")
