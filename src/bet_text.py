"""
bet_text.py — plain-English wording for game bets, so there is no guessing what
to type into a sportsbook. Pure functions (unit-tested).

Inputs are small dicts:
  ml   {"team", "odds", "is_fav"}                  moneyline value bet, or None
  sp   {"team", "line", "cover"}                   spread value bet, or None
  alt  {"team", "line", "cover", "take_at"}        safer alternate spread, or None
`team` is an abbreviation (the dashboard turns it into the full name).
"""
import math


def fmt_line(line: float) -> str:
    """'+6.5', '-3', or, for a blended quarter line like +1.25, the two real
    lines a sportsbook would show: '+1 to +1.5'."""
    n = abs(line)
    frac = round(n % 1, 2)
    sign = "+" if line > 0 else "-" if line < 0 else ""
    if frac in (0.25, 0.75):
        lo, hi = n - 0.25, n + 0.25
        return f"{sign}{lo:g} to {sign}{hi:g}"
    return f"{sign}{n:g}" if line else "0 (pick'em)"


def wins_if(team: str, line: float) -> str:
    """One plain sentence: when does this spread bet win?"""
    n = abs(line)
    if line > 0:   # underdog, gets points
        most = math.ceil(n) - 1
        if most <= 0:
            return f"{team} wins the game."
        return f"{team} wins the game OR loses by {most} or fewer."
    if line < 0:   # favorite, gives points
        need = math.floor(n) + 1
        return f"{team} wins by {need} or more."
    return f"{team} wins the game."


def short_cond(line: float) -> str:
    """Compact win condition, e.g. 'loses by 9 or fewer' / 'wins by 4+'."""
    n = abs(line)
    if line > 0:
        most = math.ceil(n) - 1
        return "wins or loses by %d or fewer" % most if most > 0 else "wins"
    need = math.floor(n) + 1
    return f"wins by {need}+"


def is_quarter(line: float) -> bool:
    return round(abs(line) % 1, 2) in (0.25, 0.75)


def strength_label(p: float) -> str:
    """Plain word for how sure the model is that a team wins."""
    if p < 0.55:
        return "toss-up"
    if p < 0.62:
        return "slight lean"
    if p < 0.70:
        return "solid"
    if p < 0.80:
        return "strong"
    return "heavy favorite"


def _ml_flag(ml, fav) -> str:
    """Marker for a moneyline VALUE bet. If the value is on the favorite it is a
    'good price'; if it is on the underdog, say so (don't imply the favorite's
    price is the good one)."""
    if not ml:
        return ""
    if ml["team"] == fav:
        return " ✅ good price"
    return f" ✅ value on {ml['team']} {int(ml['odds']):+d}"


def title_text(skip, fav, p_fav, ml, sp, alt, vs_market=False) -> str:
    """Collapsed-title summary. ALWAYS leads with the projected moneyline
    winner, then the spread pick and best alt spread if there are any.
    e.g. ' · 💰 ML: IND 66% · 📊 DAL +6.5 · 🔀 Alt: DAL +9.5'"""
    bits = [f"💰 ML: {fav} {p_fav*100:.0f}%" + _ml_flag(ml, fav)]
    if sp:
        bits.append(f"📊 {sp['team']} {fmt_line(sp['line'])}")
    if alt:
        bits.append(f"🔀 Alt: {alt['team']} {fmt_line(alt['line'])}")
    pre = (" · ⚠ book disagrees" if skip
           else " · ⚠ vs market" if vs_market else "")
    return pre + " · " + " · ".join(bits)


def card_text(skip, gap, fav, p_fav, ml_prices, ml, sp, alts,
              vs_market_note="") -> str:
    """The top block of an open game's card.

    ml_prices  (fav_odds, dog_team, dog_odds) current moneyline prices (or None)
    ml         moneyline VALUE bet dict {team, odds} or None
    sp         spread pick {team, line, cover} or None
    alts       ranked list from alt_spreads.rank_alts (+ 'team' key) or []
    """
    out = []
    if skip:
        out.append(f"⚠ **Careful:** our model and the sportsbook disagree by "
                   f"{gap:.0f} points on this game. That usually means our "
                   f"data is off, so treat everything below as low confidence.")
    # ---- moneyline ----
    ml_txt = (f"💰 **Moneyline pick: {fav} to win** · {p_fav*100:.0f}% "
              f"({strength_label(p_fav)})")
    if ml_prices and ml_prices[0] is not None:
        fav_odds, dog, dog_odds = ml_prices
        ml_txt += f"  \nPrices: {fav} {int(fav_odds):+d}"
        if dog_odds is not None:
            ml_txt += f" / {dog} {int(dog_odds):+d}"
    if ml:
        ml_txt += (f"  \n✅ The price is good: bet **{ml['team']} "
                   f"{int(ml['odds']):+d}**.")
    else:
        ml_txt += ("  \nThe price already reflects this, so there's no extra "
                   "edge. Bet it only if you like the payout.")
    out.append(ml_txt)
    # ---- spread ----
    if sp:
        t = (f"📊 **Spread pick: {sp['team']} {fmt_line(sp['line'])}**  \n"
             f"Wins if {wins_if(sp['team'], sp['line'])} Usual price: -110.")
        if is_quarter(sp["line"]):
            t += ("  \n_That number is an average across books. Use the one "
                  "your book shows._")
        out.append(t)
    else:
        out.append("📊 **Spread:** our margin agrees with the line, so no "
                   "spread pick.")
    # ---- alt spreads, best first (only when one looks worth it) ----
    if alts and alts[0].get("star"):
        rows = []
        for i, a in enumerate(alts, 1):
            est = a.get("est_price")
            rows.append(
                f"{'⭐ ' if a.get('star') else ''}**{i}. {a['team']} "
                f"{fmt_line(a['line'])}** (wins if it {short_cond(a['line'])}) "
                f"· we say {a['cover']*100:.0f}%"
                + (f" · book likely ≈ {est:+d}" if est is not None else "")
                + f" · take it at {a['take_at']:+d} or better")
        out.append("🔀 **Alt spreads, best first** (⭐ = best value; book "
                   "prices are our estimate, check yours):  \n"
                   + "  \n".join(rows))
    else:
        out.append("🔀 **Alt spreads:** none look better than the main line.")
    bet = sp or (alts[0] if alts and alts[0].get("star") else None)
    if bet and bet["team"] != fav and bet["line"] > 0:
        out.append(f"_Note: {bet['team']} getting points can win the bet even "
                   f"if {fav} wins the game, as long as {fav} wins by less "
                   f"than the points given._")
    return "\n\n".join(out) + vs_market_note


def _spread_row(sp, sp_lean) -> str:
    """Spread line for the card: value pick, else the model's lean, else why not."""
    if sp:
        return (f"{sp['team']} {fmt_line(sp['line'])} · "
                f"{sp['cover']*100:.0f}% cover  ✅ value")
    if sp_lean:
        return (f"{sp_lean['team']} {fmt_line(sp_lean['line'])} · "
                f"{sp_lean['cover']*100:.0f}% cover (lean, no edge)")
    return "no spread posted yet"


def summary_rows(skip, vs_market, fav, p_fav, ml, sp, alt, sp_lean=None) -> str:
    """The always-visible picks on a game card: ONE row per bet type, each its
    own line, the same three rows for every game."""
    rows = []
    if skip:
        rows.append("⚠ **Book disagrees with us here. Low confidence.**")
    elif vs_market:
        rows.append("⚠ **We lean the opposite way from the book. Close call.**")
    rows.append(f"💰 **Moneyline:** {fav} to win · {p_fav*100:.0f}% "
                f"({strength_label(p_fav)})" + _ml_flag(ml, fav))
    rows.append("📊 **Spread:** " + _spread_row(sp, sp_lean))
    rows.append("🔀 **Alt spread:** " + (f"{alt['team']} {fmt_line(alt['line'])}"
                                       if alt else "—"))
    return "  \n".join(rows)      # hard line breaks: tight, one row per line


# --------------------------------------------------------------------------- #
# Compact tables for the Details view (replace the walls of text)
# --------------------------------------------------------------------------- #

def _cond(line: float) -> str:
    """Very short 'wins if' for a table cell."""
    n = abs(line)
    if line > 0:
        most = math.ceil(n) - 1
        return "wins, or loses by %d or less" % most if most > 0 else "wins"
    return "wins by %d or more" % (math.floor(n) + 1)


def bets_table(fav, p_fav, ml_prices, ml, sp, alts, sp_lean=None) -> str:
    """One small markdown table with every bet: what, who, when it wins, price."""
    rows = ["| Bet | Pick | Wins if | Price |", "|---|---|---|---|"]
    price = "—"
    if ml_prices and ml_prices[0] is not None:
        price = f"{int(ml_prices[0]):+d}"
    rows.append(f"| 💰 Moneyline | **{fav}** ({p_fav*100:.0f}%, "
                f"{strength_label(p_fav)}) | {fav} wins | {price}"
                + (" ✅ good" if (ml and ml["team"] == fav) else "") + " |")
    if ml and ml["team"] != fav:
        rows.append(f"| 💰 Moneyline value | **{ml['team']}** (underdog) | "
                    f"{ml['team']} wins | {int(ml['odds']):+d} ✅ |")
    if sp:
        rows.append(f"| 📊 Spread | **{sp['team']} {fmt_line(sp['line'])}** | "
                    f"{_cond(sp['line'])} | -110 |")
    elif sp_lean:
        rows.append(f"| 📊 Spread (lean only) | {sp_lean['team']} "
                    f"{fmt_line(sp_lean['line'])} ({sp_lean['cover']*100:.0f}%) | "
                    f"{_cond(sp_lean['line'])} | -110 |")
    else:
        rows.append("| 📊 Spread | no spread posted yet | | |")
    if alts and alts[0].get("star"):
        for i, a in enumerate(alts):
            rows.append(f"| 🔀 Alt{' ⭐' if a.get('star') else ''} | "
                        f"**{a['team']} {fmt_line(a['line'])}** | "
                        f"{_cond(a['line'])} | {a['take_at']:+d} or better |")
    else:
        rows.append("| 🔀 Alt spread | — | | |")
    return "\n".join(rows)


def matchup_table(away, home, p_away, p_home, ml_away, ml_home, sp_home,
                  pts_away, pts_home, inj_away, inj_home, total=None,
                  model_total=None) -> str:
    """Away on the left, home on the right, identical rows for both."""
    def odds(o):
        return "—" if o is None else f"{int(o):+d}"

    def spread(v):
        return "—" if v is None else fmt_line(v)
    rows = [f"| | **{away}** (away) | **{home}** (home) |", "|---|---|---|",
            f"| Win chance | {p_away*100:.0f}% | {p_home*100:.0f}% |",
            f"| Moneyline | {odds(ml_away)} | {odds(ml_home)} |",
            f"| Spread | {spread(None if sp_home is None else -sp_home)} | "
            f"{spread(sp_home)} |",
            f"| Projected score | {pts_away:.0f} | {pts_home:.0f} |",
            f"| Injuries | {inj_away or 'none'} | {inj_home or 'none'} |"]
    if total is not None:
        rows.append(f"| Total points | {total:g} (we say "
                    f"{model_total:.0f}) | |" if model_total is not None
                    else f"| Total points | {total:g} | |")
    return "\n".join(rows)
