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


def good_alt(margin, main_spread_home, std, cover_fn, side, min_cover=0.65,
             max_juice=-250, steps=8):
    """The alternate spread worth a look for the side we already like.

    `side` is "home" or "away" (the side of our spread value pick). Among the
    half-point lines, take the one with the SMALLEST cushion (best payout) whose
    cover chance is still >= `min_cover` and whose fair price is no worse than
    `max_juice` (so we never recommend paying -400 for a "safe" line).
    Returns {side, line, cover, fair, take_at} or None.
    """
    best = None
    for r in ladder(margin, main_spread_home, std, cover_fn, steps=steps):
        p = r["p_home"] if side == "home" else r["p_away"]
        line = r["home_line"] if side == "home" else r["away_line"]
        fair = r["fair_home"] if side == "home" else r["fair_away"]
        need = r["need_home"] if side == "home" else r["need_away"]
        if p < min_cover or fair is None or fair < max_juice:
            continue
        if best is None or p < best["cover"]:
            best = {"side": side, "line": line, "cover": p, "fair": fair,
                    "take_at": need}
    return best


BOOK_VIG_HALF = 0.024      # half of a typical -110/-110 hold, in probability


def rank_alts(margin, main_spread_home, std, cover_fn, side, top=3,
              min_cover=0.55, max_juice=-250, steps=8):
    """Alternate spreads for `side` ranked best-first.

    We can't see a book's alt prices, so we ESTIMATE them: assume the book
    prices every alt line off ITS OWN margin (= minus the posted spread, same
    spread of outcomes as ours) plus the usual hold. For each half-point line:
        est_price_prob = book's chance of covering + BOOK_VIG_HALF
        est_edge       = our cover chance - est_price_prob   (probability pts)
    Best = biggest est_edge. The main line itself (within a quarter point) is
    skipped, since that is the normal spread bet, not an alt.
    Each row: {side, line, cover, est_prob, est_price, est_edge, take_at, star}.
    `take_at` is the price needed for a 3-point edge by our numbers.
    """
    mkt_margin = -main_spread_home
    out = []
    for r in ladder(margin, main_spread_home, std, cover_fn, steps=steps):
        h = r["home_line"]
        if abs(h - main_spread_home) < 0.3:
            continue                                  # that's the main line
        ours = r["p_home"] if side == "home" else r["p_away"]
        mph, mpa = cover_fn(mkt_margin, h, std)
        book = (mph if side == "home" else mpa) + BOOK_VIG_HALF
        fair = r["fair_home"] if side == "home" else r["fair_away"]
        need = r["need_home"] if side == "home" else r["need_away"]
        if ours < min_cover or book >= 0.995 or fair is None or fair < max_juice:
            continue
        out.append({"side": side,
                    "line": h if side == "home" else r["away_line"],
                    "cover": ours, "est_prob": book,
                    "est_price": american_from_prob(book),
                    "est_edge": ours - book, "take_at": need, "star": False})
    out.sort(key=lambda x: -x["est_edge"])
    out = out[:top]
    if out and out[0]["est_edge"] > 0:
        out[0]["star"] = True
    return out
