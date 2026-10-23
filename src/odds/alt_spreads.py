"""
alt_spreads.py — what each ALTERNATE spread is worth by our numbers.

Books offer other spreads at other prices (take fewer points for a bigger
payout, or buy points for a safer cover). We don't have the book's alt prices,
so this shows, for each half-point line, the model's cover chance and the
break-even ("fair") American odds. If your book's price on that line is BETTER
than the "worth it at" column, our numbers say it's +EV; if worse, skip it.

Pure functions; `cover_fn(margin, spread_home, std) -> (p_home, p_away)`.
"""
import math


def american_from_prob(p):
    """Fair American odds for win probability p (None if p is 0/1/invalid)."""
    if p is None or not (0.0 < p < 1.0):
        return None
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def fmt_odds(o):
    return "—" if o is None else f"{o:+d}"


def ladder(margin, main_spread_home, std, cover_fn, steps=3, edge=0.03):
    """Rows for home lines around the main line, in half-point steps of 1.

    Each: {home_line, away_line, p_home, p_away, fair_home, fair_away,
           need_home, need_away}.  `need_*` = the odds you must beat to have a
    `edge` (default 3-pt) advantage on that side.
    """
    base = math.floor(main_spread_home) + 0.5      # nearest .5 line
    rows = []
    for k in range(-steps, steps + 1):
        h = base + k
        ph, pa = cover_fn(margin, h, std)
        rows.append({
            "home_line": h, "away_line": -h, "p_home": ph, "p_away": pa,
            "fair_home": american_from_prob(ph),
            "fair_away": american_from_prob(pa),
            "need_home": american_from_prob(min(ph - edge, 0.999)),
            "need_away": american_from_prob(min(pa - edge, 0.999)),
        })
    return rows
