"""
ev_engine.py — Phase 6 (core): turn odds + our probability into EXPECTED VALUE.

This is the math that decides whether a prop is worth betting. It needs NO
internet — it works on any odds you hand it (from balldontlie, a screenshot you
read off a sportsbook, or typed by hand), so it's testable on its own.

THE CHAIN, in plain English:
  1. Odds -> implied probability.  American odds like -110 or +150 (and decimal
     odds like 1.91) encode the sportsbook's price. We convert that price into
     the probability the book is effectively charging you for.
  2. Remove the vig (the "juice").  Both sides' implied probabilities add up to
     MORE than 100% — that overflow is the book's built-in margin. We strip it
     proportionally to get the book's FAIR (no-vig) probability, which is the
     honest number to compare our model against.
  3. Expected value (EV).  Given OUR estimated probability and the actual payout
     odds, EV = how much we expect to win or lose per 1 unit staked, on average,
     if this exact bet were repeated forever. EV > 0 means the odds are in our
     favor *if our probability is right* — that's a "value" bet.
  4. Verdict.  We only call something value when our edge clears a margin, and
     we always print the assumptions so nothing looks more certain than it is.

HONESTY: EV is only as good as OUR probability. A positive EV from an
overconfident model is a mirage — which is why the backtest's calibration check
(Phase 8) matters before trusting any EV number for real money.
"""


def american_to_decimal(american: int) -> float:
    """-110 -> 1.909..., +150 -> 2.50. Decimal odds = total return per 1 staked."""
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    """Inverse of the above, rounded to the nearest whole American price."""
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100)
    return round(-100.0 / (decimal - 1.0))


def implied_probability(american: int) -> float:
    """The raw (with-vig) probability baked into a single side's price.

    -110 -> 0.5238, +150 -> 0.40. These intentionally sum to >1 across a market.
    """
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def remove_vig(odds_over: int, odds_under: int) -> dict:
    """Strip the book's margin from a two-way market to get FAIR probabilities.

    Returns the raw implied probs, the total overround (vig), and the no-vig
    fair probabilities for each side (which sum to 1.0).
    """
    p_over_raw = implied_probability(odds_over)
    p_under_raw = implied_probability(odds_under)
    overround = p_over_raw + p_under_raw           # > 1.0; the excess is the vig
    if overround <= 0:
        return {"fair_over": None, "fair_under": None, "vig": None}
    return {
        "p_over_raw": round(p_over_raw, 4),
        "p_under_raw": round(p_under_raw, 4),
        "overround": round(overround, 4),
        "vig_pct": round((overround - 1.0) * 100, 2),
        "fair_over": round(p_over_raw / overround, 4),
        "fair_under": round(p_under_raw / overround, 4),
    }


def expected_value(our_prob: float, american: int, stake: float = 1.0) -> float:
    """EV per `stake` units for a bet at `american` odds we think wins `our_prob`.

    Win returns profit = stake * (decimal - 1); loss costs the stake. So
      EV = our_prob * profit  -  (1 - our_prob) * stake.
    EV > 0 means a long-run edge AT OUR PROBABILITY.
    """
    if our_prob is None:
        return None
    dec = american_to_decimal(american)
    profit_if_win = stake * (dec - 1.0)
    return round(our_prob * profit_if_win - (1.0 - our_prob) * stake, 4)


def ev_percent(our_prob: float, american: int) -> float:
    """EV expressed as a percentage of the stake (per 1 unit)."""
    ev = expected_value(our_prob, american, 1.0)
    return None if ev is None else round(ev * 100, 2)


def evaluate_prop(our_p_over: float, our_p_under: float,
                  odds_over: int, odds_under: int,
                  edge_threshold: float = 0.03) -> dict:
    """Full Phase-6 verdict for one two-way prop market.

    Compares OUR probability to the book's FAIR (no-vig) probability on each
    side, computes EV at the actual payout odds, and flags value only when our
    edge over the fair line clears `edge_threshold` (default 3%).
    """
    # Valid American odds are <= -100 or >= +100 (never 0, never -99..+99).
    # Anything else (e.g. an empty/zero odds box) would divide by zero in the
    # decimal/implied math, so bail out with a clean "can't evaluate" verdict.
    def _valid(o):
        return isinstance(o, (int, float)) and (o >= 100 or o <= -100)

    if not _valid(odds_over) or not _valid(odds_under):
        return {
            "fair_over": None, "fair_under": None, "vig_pct": None,
            "our_p_over": our_p_over, "our_p_under": our_p_under,
            "ev_over_pct": None, "ev_under_pct": None,
            "edge_over_pct": None, "edge_under_pct": None,
            "recommended_side": None, "is_value": False,
            "verdict": "enter valid odds (e.g. -110 or +120) to check value",
        }

    fair = remove_vig(odds_over, odds_under)
    ev_over = expected_value(our_p_over, odds_over)
    ev_under = expected_value(our_p_under, odds_under)

    edge_over = (our_p_over - fair["fair_over"]) if (our_p_over is not None
                and fair["fair_over"] is not None) else None
    edge_under = (our_p_under - fair["fair_under"]) if (our_p_under is not None
                 and fair["fair_under"] is not None) else None

    # Pick the better side by EV, but only call it VALUE if the edge clears the bar.
    best_side, best_ev, best_edge, best_odds = None, None, None, None
    if ev_over is not None and (ev_under is None or ev_over >= ev_under):
        best_side, best_ev, best_edge, best_odds = "over", ev_over, edge_over, odds_over
    elif ev_under is not None:
        best_side, best_ev, best_edge, best_odds = "under", ev_under, edge_under, odds_under

    is_value = (best_ev is not None and best_ev > 0 and
                best_edge is not None and best_edge >= edge_threshold)

    if best_side is None:
        verdict = "no odds to evaluate"
    elif is_value:
        verdict = (f"VALUE on {best_side.upper()} — our edge "
                   f"{best_edge*100:+.1f}% over the fair line, EV "
                   f"{best_ev*100:+.1f}% per unit at {best_odds:+d}")
    else:
        verdict = (f"no value — best side is {best_side} but edge "
                   f"{(best_edge or 0)*100:+.1f}% / EV {(best_ev or 0)*100:+.1f}% "
                   f"doesn't clear the {edge_threshold*100:.0f}% bar")

    return {
        "fair_over": fair["fair_over"], "fair_under": fair["fair_under"],
        "vig_pct": fair["vig_pct"],
        "our_p_over": our_p_over, "our_p_under": our_p_under,
        "ev_over_pct": ev_percent(our_p_over, odds_over),
        "ev_under_pct": ev_percent(our_p_under, odds_under),
        "edge_over_pct": round(edge_over * 100, 2) if edge_over is not None else None,
        "edge_under_pct": round(edge_under * 100, 2) if edge_under is not None else None,
        "recommended_side": best_side if is_value else None,
        "is_value": is_value,
        "verdict": verdict,
    }


if __name__ == "__main__":
    print("Phase 6 EV engine — worked examples (no API needed)\n")

    # Example 1: standard -110/-110 market, our model slightly likes the over.
    print("Example 1: line priced -110 both sides; model says 58% over")
    r = evaluate_prop(our_p_over=0.58, our_p_under=0.42,
                      odds_over=-110, odds_under=-110)
    for k, v in r.items():
        print(f"  {k}: {v}")

    print("\nExample 2: book -130 over / +110 under; model says 52% over "
          "(no real edge)")
    r = evaluate_prop(our_p_over=0.52, our_p_under=0.48,
                      odds_over=-130, odds_under=110)
    for k, v in r.items():
        print(f"  {k}: {v}")

    print("\nSanity checks:")
    print(f"  -110 -> implied {implied_probability(-110):.4f} (expect ~0.5238)")
    print(f"  +150 -> implied {implied_probability(150):.4f} (expect 0.4000)")
    print(f"  -110 -> decimal {american_to_decimal(-110):.4f} (expect ~1.9091)")
    print(f"  fair of a -110/-110 market: "
          f"{remove_vig(-110,-110)['fair_over']} each (expect 0.5)")
