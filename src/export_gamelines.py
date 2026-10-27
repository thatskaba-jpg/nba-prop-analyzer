"""
export_gamelines.py — turn today's game-line picks into something you can send
to friends: a plain-text message (paste into iMessage/WhatsApp/Discord) and a
CSV file. Pure functions, no I/O, so they're unit-tested.

An "entry" is one OPEN game's already-computed result from the Game Lines tab:
    tip (str, e.g. "6:00 PM ET"), away, home (abbreviations), fav (abbr the
    model favors), margin (model margin, + = home), p_home, p_away,
    ml_pick, sp_pick (strings like "HOU +3.75" / None), skip (bool: model
    disagrees with the market too much), ml_home, ml_away, spread_home, total.
"""
import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from team_names import fullify, full  # noqa: E402
from bet_text import fmt_line  # noqa: E402

DISCLAIMER = ("Model picks for fun, not guarantees. In past-game tests this "
              "model has NOT beaten the sportsbook lines, so treat a pick as "
              "'worth a look'. Lines move and books differ (a .75 spread is a "
              "blend; real lines are .5 or whole): check your book's number. "
              "Bet responsibly.")


def _pick_text(pick, skip, none_text):
    if skip:
        return "skip (model disagrees with the market)"
    return fullify(pick) if pick else none_text


def _lean_text(e):
    """'lean X +3.5 (no edge)' when there is a spread but no value pick."""
    sl = e.get("sp_lean")
    if sl:
        return f"lean {full(sl['team'])} {fmt_line(sl['line'])} (no edge)"
    return "no spread posted" if e.get("spread_home") is None else "no edge"


def build_rows(entries):
    rows = []
    for e in entries:
        away, home = e["away"], e["home"]
        mk = []
        if e.get("ml_home") is not None and e.get("ml_away") is not None:
            mk.append(f"ML {full(away)} {int(e['ml_away']):+} / "
                      f"{full(home)} {int(e['ml_home']):+}")
        if e.get("spread_home") is not None:
            mk.append(f"Spread {full(home)} {e['spread_home']:+g}")
        if e.get("total") is not None:
            mk.append(f"Total {e['total']:g}")
        lean = f"model leans {full(e['fav'])}"
        rows.append({
            "Tip-off": e.get("tip") or "",
            "Away": full(away), "Home": full(home),
            "Moneyline pick": _pick_text(e.get("ml_pick"), e.get("skip"),
                                         f"no edge ({lean})"),
            "Spread pick": _pick_text(e.get("sp_pick"), e.get("skip"),
                                      _lean_text(e)),
            "Model favorite": full(e["fav"]),
            "Model margin": round(abs(e["margin"]), 1),
            "Win chance away": f"{e['p_away_win'] * 100:.0f}%"
            if e.get("p_away_win") is not None else "",
            "Win chance home": f"{e['p_home_win'] * 100:.0f}%"
            if e.get("p_home_win") is not None else "",
            "Market": "  ·  ".join(mk),
        })
    return rows


def _label(edge):
    """Plain words for how big the gap between our number and the price is."""
    if edge is None:
        return ""
    return "Strong" if edge >= 8 else "Solid" if edge >= 5 else "Slight"


def top_picks(entries, limit=5):
    """Value picks ranked by edge (biggest gap between our chance and the fair
    price first). Skipped games (model disagrees with the market) are excluded.
    Each: {Rank, Confidence, Pick, Type, Game, Tip-off, Chance, Edge}."""
    cands = []
    for e in entries:
        if e.get("skip"):
            continue
        game = f"{full(e['away'])} @ {full(e['home'])}"
        if e.get("ml_pick") and e.get("ml_edge") is not None:
            cands.append((e["ml_edge"], "Moneyline", fullify(e["ml_pick"]),
                          e.get("ml_prob"), game, e.get("tip") or ""))
        if e.get("sp_pick") and e.get("sp_edge") is not None:
            cands.append((e["sp_edge"], "Spread", fullify(e["sp_pick"]),
                          e.get("sp_cover"), game, e.get("tip") or ""))
    cands.sort(key=lambda c: -c[0])
    out = []
    for i, (edge, typ, pick, prob, game, tip) in enumerate(cands[:limit], 1):
        out.append({"Rank": i, "Confidence": _label(edge), "Pick": pick,
                    "Type": typ, "Game": game, "Tip-off": tip,
                    "Chance": f"{prob * 100:.0f}%" if prob is not None else "",
                    "Edge": f"{edge:+.1f}%"})
    return out


def top_alts(entries, limit=6):
    """Alternate spreads worth a look: for each game with a spread value pick,
    the safer line our numbers like (see odds.alt_spreads.good_alt)."""
    out = []
    for e in entries:
        a = e.get("alt")
        if e.get("skip") or not a:
            continue
        team = e["home"] if a["side"] == "home" else e["away"]
        out.append({"Pick": f"{full(team)} {a['line']:+g}",
                    "Game": f"{full(e['away'])} @ {full(e['home'])}",
                    "Tip-off": e.get("tip") or "",
                    "Cover": f"{a['cover'] * 100:.0f}%",
                    "Take at": f"{a['take_at']:+d}" if a.get("take_at") is not None else "—",
                    "_p": a["cover"]})
    out.sort(key=lambda r: -r["_p"])
    for r in out:
        r.pop("_p")
    return out[:limit]


def _ml_edge_text(e, winner_abbr):
    """'✅ value' only when the value bet is on THIS winner; if the value is on
    the other side say so; else 'none'."""
    vt = e.get("ml_team")
    if not vt and e.get("ml_pick"):
        vt = winner_abbr              # old entries without ml_team
    if not vt:
        return "none"
    if vt == winner_abbr:
        return "✅ value"
    return f"value on {full(vt)}"


def top_ml(entries, limit=5):
    """Moneyline view: the games where the model is most sure who wins, with
    that team's price. Value is flagged only when the PRICE is also good (a
    heavy favorite wins often but pays little, so win chance alone isn't a bet).
    Skipped games (model disagrees with the market) are excluded."""
    c = [e for e in entries if not e.get("skip")]
    c.sort(key=lambda e: -max(e["p_home_win"], e["p_away_win"]))
    out = []
    for i, e in enumerate(c[:limit], 1):
        home_fav = e["p_home_win"] >= e["p_away_win"]
        team = e["home"] if home_fav else e["away"]
        price = e.get("ml_home") if home_fav else e.get("ml_away")
        out.append({
            "Rank": i, "Winner": full(team),
            "Game": f"{full(e['away'])} @ {full(e['home'])}",
            "Tip-off": e.get("tip") or "",
            "Win chance": f"{max(e['p_home_win'], e['p_away_win']) * 100:.0f}%",
            "Moneyline": f"{int(price):+}" if price is not None else "—",
            "Price edge": _ml_edge_text(e, team)})
    return out


def top_leans(entries, limit=3):
    """When there is no value pick: the games where the model is most sure who
    wins. NOT a price edge; labelled as a lean so nobody mistakes it for one."""
    c = [e for e in entries if not e.get("skip")]
    c.sort(key=lambda e: -max(e["p_home_win"], e["p_away_win"]))
    out = []
    for e in c[:limit]:
        home_fav = e["p_home_win"] >= e["p_away_win"]
        out.append({"Lean": full(e["home"] if home_fav else e["away"]),
                    "Game": f"{full(e['away'])} @ {full(e['home'])}",
                    "Tip-off": e.get("tip") or "",
                    "Win chance": f"{max(e['p_home_win'], e['p_away_win']) * 100:.0f}%"})
    return out


def to_text(rows, day_label, top=None, ml=None, alts=None):
    """Message-friendly text. Only games with a pick are headlined first."""
    if not rows:
        return f"NBA game lines — {day_label}\nNo open games with lines yet."

    def has_pick(r):
        return not (r["Moneyline pick"].startswith(("no edge", "skip"))
                    and r["Spread pick"].startswith(("no edge", "skip")))
    picks = [r for r in rows if has_pick(r)]
    lines = [f"🏀 NBA game lines — {day_label}", ""]
    lines.append(f"{len(picks)} pick(s) out of {len(rows)} open game(s)"
                 if rows else "")
    lines.append("")
    if top:
        lines.append("⭐ Most confident picks")
        for t in top:
            lines.append(f"  {t['Rank']}. {t['Pick']} ({t['Type']}) — "
                         f"{t['Confidence']}, {t['Chance']} chance, "
                         f"edge {t['Edge']}  [{t['Game']}, {t['Tip-off']}]")
        lines.append("")
    if ml:
        lines.append("💰 Moneyline: most likely winners")
        for m in ml:
            lines.append(f"  {m['Rank']}. {m['Winner']} {m['Moneyline']} — "
                         f"{m['Win chance']} to win"
                         + (" ✅ good price" if m["Price edge"].startswith("✅") else "")
                         + f"  [{m['Game']}, {m['Tip-off']}]")
        lines.append("")
    if alts:
        lines.append("🔀 Alt spreads worth a look")
        for a in alts:
            lines.append(f"  • {a['Pick']} — {a['Cover']} cover by our numbers; "
                         f"take it at {a['Take at']} or better  "
                         f"[{a['Game']}, {a['Tip-off']}]")
        lines.append("")
    if top or ml or alts:
        lines.append("All open games")
        lines.append("")
    for r in rows:
        mark = "✅ " if has_pick(r) else ""
        lines.append(f"{mark}{r['Tip-off']} — {r['Away']} @ {r['Home']}"
                     if r["Tip-off"] else f"{mark}{r['Away']} @ {r['Home']}")
        lines.append(f"   Moneyline: {r['Moneyline pick']}")
        lines.append(f"   Spread: {r['Spread pick']}")
        if r["Market"]:
            lines.append(f"   Lines: {r['Market']}")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def to_csv(rows):
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def csv_rows(entries):
    """Tidy rows for the shareable CSV: time, game, moneyline pick with win
    chance, spread, alt spread, and the book's lines (6 columns, was 10)."""
    out = []
    for e in entries:
        fav = e["fav"]
        pfav = max(e["p_home_win"], e["p_away_win"])
        sp = (fullify(e["sp_pick"]) + " (value)") if e.get("sp_pick") else _lean_text(e)
        if e.get("skip"):
            sp = "skip (book disagrees)"
        a = e.get("alt")
        alt = "—"
        if a and not e.get("skip"):
            t = e["home"] if a["side"] == "home" else e["away"]
            alt = f"{full(t)} {fmt_line(a['line'])} (take at {a['take_at']:+d} or better)"
        mk = []
        if e.get("ml_home") is not None and e.get("ml_away") is not None:
            mk.append(f"ML {e['away']} {int(e['ml_away']):+d} / {e['home']} {int(e['ml_home']):+d}")
        if e.get("spread_home") is not None:
            mk.append(f"Spread {e['home']} {fmt_line(e['spread_home'])}")
        if e.get("total") is not None:
            mk.append(f"Total {e['total']:g}")
        out.append({"Time": e.get("tip") or "",
                    "Game": f"{full(e['away'])} @ {full(e['home'])}",
                    "Moneyline pick": f"{full(fav)} ({pfav*100:.0f}%)"
                                      + (" ✅ value" if (e.get("ml_team") == fav) else ""),
                    "Spread": sp, "Alt spread": alt,
                    "Book lines": " · ".join(mk)})
    return out
