"""
csv_tables.py — turn the raw pick logs into CSVs a human can read.

ml_picks_wide   one row per GAME, one column per model profile (was 6 rows per
                game), with a final RECORD row.
ats_picks_table plain-English spread results: what we picked, the book's line,
                what happened, covered or not.
Pure functions, unit-tested.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from team_names import full  # noqa: E402
from bet_text import fmt_line  # noqa: E402

# Short column names for the model profiles (long names stay in the app).
PROFILE_SHORT = {
    "Balanced": "Balanced",
    "Trust the ratings (bold)": "Trust ratings",
    "Cautious (toward coin flip)": "Cautious",
    "Hot hand (recent form)": "Hot hand",
    "Matchup hunter": "Matchup hunter",
    "Favorite slammer (chalk)": "Favorite slammer",
}
ORDER = list(PROFILE_SHORT.values())


def _short(name: str) -> str:
    return PROFILE_SHORT.get(name, name.split(" (")[0])


def ml_picks_wide(rows):
    """rows: dicts with Date, Game 'AWAY @ HOME', Model, Model pick 'BOS by 0.4',
    Actual result 'DET won by 8 (121-113)', Hit? '✅ WIN'/'❌ LOSS'.
    Returns one dict per game (newest first) plus a RECORD row."""
    games = {}
    for r in rows:
        key = (r["Date"], r["Game"])
        g = games.setdefault(key, {"result": r["Actual result"], "cells": {}})
        pick_team = str(r["Model pick"]).split(" by ")[0]
        g["cells"][_short(r["Model"])] = (pick_team, "WIN" in str(r["Hit?"]))
    profiles = [p for p in ORDER if any(p in g["cells"] for g in games.values())]
    profiles += sorted({p for g in games.values() for p in g["cells"]} - set(profiles))
    out, tally = [], {p: [0, 0] for p in profiles}
    for (date, game), g in sorted(games.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True):
        away, home = [t.strip() for t in game.split("@")]
        winner = str(g["result"]).split(" won")[0]
        score = str(g["result"]).split("(")[-1].rstrip(")")
        row = {"Date": date, "Game": f"{full(away)} @ {full(home)}",
               "Final": f"{full(winner)} won {score}"}
        for p in profiles:
            cell = g["cells"].get(p)
            if cell is None:
                row[p] = ""
                continue
            team, hit = cell
            row[p] = f"{team} {'✅' if hit else '❌'}"
            tally[p][0 if hit else 1] += 1
        out.append(row)
    if out:
        # Totals live UNDER the table (one row each), not in a side column.
        rec = {"Date": "", "Game": "RECORD (right-wrong)", "Final": ""}
        pct = {"Date": "", "Game": "WIN %", "Final": ""}
        for p in profiles:
            w, l = tally[p]
            rec[p] = f"{w}-{l}"
            pct[p] = f"{round(100 * w / (w + l))}%" if (w + l) else ""
        out.extend([rec, pct])
    return out


def ats_picks_table(rows):
    """rows from spread_scoreboard.get_detail(). Newest first, pending on top."""
    out = []
    for r in rows:
        gid = str(r.get("game_id", ""))
        date = gid.split("|")[1] if "|" in gid else ""
        home, away = r.get("home"), r.get("away")
        sp = r.get("spread_home")
        if home is None or away is None or sp is None:
            continue
        side_home = r.get("our_side") == "home"
        pick = f"{full(home if side_home else away)} {fmt_line(sp if side_home else -sp)}"
        pm = r.get("proj_margin") or 0
        ours = f"{home} by {abs(pm):.1f}" if pm >= 0 else f"{away} by {abs(pm):.1f}"
        am = r.get("actual_margin")
        if am is None:
            final = "—"
        elif am == 0:
            final = "tie"
        else:
            final = f"{home if am > 0 else away} won by {abs(am)}"
        res = {"win": "✅ Covered", "loss": "❌ Missed", "push": "➖ Push",
               "pending": "⏳ Pending"}.get(r.get("result"), str(r.get("result")))
        out.append({"Date": date, "Game": f"{full(away)} @ {full(home)}",
                    "Our pick": pick,
                    "Book line": f"{home} {fmt_line(sp)}",
                    "Our margin": ours, "Final": final, "Result": res})
    pending = [o for o in out if o["Result"] == "⏳ Pending"]
    done = [o for o in out if o["Result"] != "⏳ Pending"]
    key = lambda o: (o["Date"], o["Game"])
    return (sorted(pending, key=key, reverse=True)
            + sorted(done, key=key, reverse=True))


# --------------------------------------------------------------------------- #
# Combined scorecard: moneyline + spread on one row per game
# --------------------------------------------------------------------------- #

def scorecard(ml_rows, ats_rows):
    """One row per game with every moneyline model's pick AND the spread result.

    ml_rows  scoreboard_detail() rows (Date, Game 'AWAY @ HOME', Model, ...)
    ats_rows spread_scoreboard.get_detail() rows
    Returns (games, footer): `games` newest first; `footer` = RECORD and WIN %
    rows meant to sit UNDER the table. Games only one log knows about still
    appear, with '—' where the other has no record."""
    games = {}
    for r in ml_rows:
        key = (r["Date"], r["Game"])
        g = games.setdefault(key, {"ml": {}, "result": r["Actual result"], "ats": None})
        team = str(r["Model pick"]).split(" by ")[0]
        g["ml"][_short(r["Model"])] = (team, "WIN" in str(r["Hit?"]))
    for a in ats_rows:
        gid = str(a.get("game_id", ""))
        if "|" not in gid or a.get("home") is None or a.get("spread_home") is None:
            continue
        key = (gid.split("|")[1], f"{a['away']} @ {a['home']}")
        games.setdefault(key, {"ml": {}, "result": None, "ats": None})["ats"] = a
    profiles = [p for p in ORDER if any(p in g["ml"] for g in games.values())]
    profiles += sorted({p for g in games.values() for p in g["ml"]} - set(profiles))
    out = []
    tally = {p: [0, 0] for p in profiles}
    sp = [0, 0]
    for (date, game), g in sorted(games.items(), reverse=True):
        away, home = [t.strip() for t in game.split("@")]
        a = g["ats"]
        if g["result"]:
            winner = str(g["result"]).split(" won")[0]
            final = f"{full(winner)} won {str(g['result']).split('(')[-1].rstrip(')')}"
        elif a and a.get("actual_margin") is not None:
            am = a["actual_margin"]
            final = f"{full(home if am > 0 else away)} won by {abs(am)}" if am else "tie"
        else:
            final = "—"
        row = {"Date": date, "Game": f"{full(away)} @ {full(home)}", "Final": final}
        for p in profiles:
            cell = g["ml"].get(p)
            if cell is None:
                row[p] = "—"
                continue
            row[p] = f"{cell[0]} {'✅' if cell[1] else '❌'}"
            tally[p][0 if cell[1] else 1] += 1
        if a:
            side_home = a.get("our_side") == "home"
            sph = a["spread_home"]
            row["Spread pick"] = (f"{full(home if side_home else away)} "
                                  f"{fmt_line(sph if side_home else -sph)}")
            row["Book line"] = f"{home} {fmt_line(sph)}"
            res = {"win": "✅ Covered", "loss": "❌ Missed", "push": "➖ Push",
                   "pending": "⏳ Pending"}.get(a.get("result"), str(a.get("result")))
            row["Spread result"] = res
            if res.endswith("Covered"):
                sp[0] += 1
            elif res.endswith("Missed"):
                sp[1] += 1
        else:
            row["Spread pick"] = row["Book line"] = "—"
            row["Spread result"] = "not tracked"
        out.append(row)
    rec = {"Date": "", "Game": "RECORD (right-wrong)", "Final": ""}
    pct = {"Date": "", "Game": "WIN %", "Final": ""}
    for p in profiles:
        w, l = tally[p]
        rec[p] = f"{w}-{l}"
        pct[p] = f"{round(100 * w / (w + l))}%" if (w + l) else ""
    rec.update({"Spread pick": "", "Book line": "", "Spread result": f"{sp[0]}-{sp[1]}"})
    pct.update({"Spread pick": "", "Book line": "",
                "Spread result": f"{round(100 * sp[0] / (sp[0] + sp[1]))}%" if (sp[0] + sp[1]) else ""})
    return out, [rec, pct]


def agreement(rows, primary="Balanced"):
    """How the moneyline pick and the spread pick lined up, game by game, for
    games where BOTH are graded. Returns [{Combination, Games, Share}]."""
    cells = {("✅", "✅"): 0, ("✅", "❌"): 0, ("❌", "✅"): 0, ("❌", "❌"): 0}
    for r in rows:
        ml = str(r.get(primary, ""))[-1:]
        sp = str(r.get("Spread result", ""))
        sp = "✅" if sp.endswith("Covered") else "❌" if sp.endswith("Missed") else ""
        if ml in ("✅", "❌") and sp:
            cells[(ml, sp)] += 1
    n = sum(cells.values())
    names = {("✅", "✅"): "Winner right AND covered the spread",
             ("✅", "❌"): "Winner right, but did NOT cover",
             ("❌", "✅"): "Winner wrong, but covered the spread",
             ("❌", "❌"): "Winner wrong AND missed the spread"}
    return [{"Combination": names[k], "Games": v,
             "Share": round(v / n, 3) if n else None} for k, v in cells.items()]
