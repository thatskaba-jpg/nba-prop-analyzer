"""workbooks.py — Excel versions of the exports: a summary tab, an "all" tab and
one tab per date, each with a title, filter dropdowns and color-coded results
(see xlsx_min.py)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xlsx_min import write_xlsx  # noqa: E402
import csv_tables  # noqa: E402


def _stamp():
    return "Generated " + time.strftime("%a %b %-d, %-I:%M %p")


def _by_date(rows, key="Date"):
    out = {}
    for r in rows:
        out.setdefault(r.get(key) or "", []).append(r)
    return out


def _pretty(date):
    try:
        return time.strftime("%a %b %-d", time.strptime(date, "%Y-%m-%d"))
    except Exception:
        return date


def _ml_footer(game_rows, models):
    """RECORD and WIN % rows for a set of game rows (shown UNDER the table)."""
    rec = {"Date": "", "Game": "RECORD (right-wrong)", "Final": ""}
    pct = {"Date": "", "Game": "WIN %", "Final": ""}
    for m in models:
        w = sum(1 for r in game_rows if str(r.get(m, "")).endswith("✅"))
        l = sum(1 for r in game_rows if str(r.get(m, "")).endswith("❌"))
        rec[m] = f"{w}-{l}"
        pct[m] = f"{round(100 * w / (w + l))}%" if (w + l) else ""
    return [rec, pct]


def ml_workbook(detail_rows) -> bytes:
    """Moneyline pick log: Summary, All games, then one tab per date."""
    wide = csv_tables.ml_picks_wide(detail_rows)
    games = [r for r in wide if r.get("Date")]
    models = [k for k in (games[0] if games else {}) if k not in ("Date", "Game", "Final")]
    summary = []
    for m in models:
        w = sum(1 for r in games if str(r.get(m, "")).endswith("✅"))
        l = sum(1 for r in games if str(r.get(m, "")).endswith("❌"))
        summary.append({"Model": m, "Right": w, "Wrong": l,
                        "Win %": round(w / (w + l), 3) if (w + l) else None})
    note = ("✅ = the model picked the winner, ❌ = it didn't. All models share the "
            "same ratings, so they aren't independent opinions.")
    common = {"freeze_cols": 2, "bold_cols": ["Game"]}
    sheets = [
        {"name": "Summary", "title": "Moneyline picks: model record",
         "subtitle": f"{_stamp()}. Win % = right / (right + wrong). Re-graded with "
                     "today's ratings, so treat early games with caution.",
         "percent": ["Win %"], "color_scale": ["Win %"], "bold_cols": ["Model"],
         "rows": summary},
        {"name": "All games", "title": "All games (newest first)", "subtitle": note,
         "rows": games, "footer": _ml_footer(games, models), **common}]
    for date, rows in sorted(_by_date(games).items(), reverse=True):
        sheets.append({"name": date, "title": f"Games on {_pretty(date)}",
                       "subtitle": note, "rows": rows,
                       "footer": _ml_footer(rows, models), **common})
    return write_xlsx(sheets)


def ats_workbook(detail_rows) -> bytes:
    """Spread pick log: Summary, All, then one tab per date."""
    table = csv_tables.ats_picks_table(detail_rows)
    done = [r for r in table if r["Result"] != "⏳ Pending"]
    cov = sum(1 for r in done if r["Result"].endswith("Covered"))
    miss = sum(1 for r in done if r["Result"].endswith("Missed"))
    push = sum(1 for r in done if r["Result"].endswith("Push"))
    summary = [{"Covered": cov, "Missed": miss, "Pushes": push,
                "Pending": len(table) - len(done),
                "Cover %": round(cov / (cov + miss), 3) if (cov + miss) else None,
                "Break-even at -110": 0.524}]
    note = "Covered = our side beat the book's spread. Pending = game not final yet."
    sheets = [
        {"name": "Summary", "title": "Spread picks: did our side cover?",
         "subtitle": f"{_stamp()}. Cover % must beat 52.4% over a big sample to "
                     "mean anything.",
         "percent": ["Cover %", "Break-even at -110"], "color_scale": ["Cover %"],
         "rows": summary},
        {"name": "All", "title": "All spread picks (pending first, then newest)",
         "subtitle": note, "rows": table, "freeze_cols": 2, "bold_cols": ["Game"]}]
    for date, rows in sorted(_by_date(table).items(), reverse=True):
        sheets.append({"name": date or "Undated",
                       "title": f"Spread picks for {_pretty(date)}",
                       "subtitle": note, "rows": rows, "freeze_cols": 2,
                       "bold_cols": ["Game"]})
    return write_xlsx(sheets)


def gamelines_workbook(csv_row_list, top, alts, ml) -> bytes:
    """Today's shareable picks: Picks, Best bets, Alt spreads, Moneyline."""
    note = ("Model picks, not guarantees. Check your book's actual number before "
            "betting: a .75 spread is a blend, real lines are .5 or whole.")
    return write_xlsx([
        {"name": "Picks", "title": "Today's game lines", "subtitle": f"{_stamp()}. {note}",
         "rows": csv_row_list, "bold_cols": ["Game"]},
        {"name": "Best bets", "title": "Best bets (value picks)",
         "subtitle": "Ranked by edge. Treat as 'worth a look'; the model has not "
                     "beaten the sportsbook in past-game tests.", "rows": top},
        {"name": "Alt spreads", "title": "Alt spreads worth a look",
         "subtitle": "Take at = the price your book must beat.", "rows": alts},
        {"name": "Moneyline", "title": "Moneyline: most likely winners",
         "subtitle": "Heavy favorites win often but pay little.", "rows": ml}])


def scorecard_workbook(ml_detail, ats_detail) -> bytes:
    """ONE workbook for 'how is the model doing': moneyline picks + spread
    result per game, with totals under the table."""
    games, footer = csv_tables.scorecard(ml_detail, ats_detail)
    models = [k for k in (games[0] if games else {})
              if k not in ("Date", "Game", "Final", "Spread pick", "Book line", "Spread result")]
    summary = []
    for m in models:
        w = sum(1 for r in games if str(r.get(m, "")).endswith("✅"))
        l = sum(1 for r in games if str(r.get(m, "")).endswith("❌"))
        summary.append({"Measure": f"Moneyline: {m}", "Right": w, "Wrong": l,
                        "Win %": round(w / (w + l), 3) if (w + l) else None})
    cov = sum(1 for r in games if str(r.get("Spread result", "")).endswith("Covered"))
    mis = sum(1 for r in games if str(r.get("Spread result", "")).endswith("Missed"))
    summary.append({"Measure": "Spread: our side covered", "Right": cov, "Wrong": mis,
                    "Win %": round(cov / (cov + mis), 3) if (cov + mis) else None})
    primary = "Balanced" if "Balanced" in models else (models[0] if models else "")
    agree = csv_tables.agreement(games, primary) if primary else []
    note = ("Moneyline cells: the team each model picked, ✅ if it won. Spread: did "
            "our side beat the book's line. 'not tracked' = the spread log only "
            "records games it saw before tip-off.")
    common = {"freeze_cols": 2, "bold_cols": ["Game"]}
    sheets = [
        {"name": "Summary", "title": "Model scorecard: moneyline + spread",
         "subtitle": f"{_stamp()}. Break-even is about 52.4% on spreads at -110. "
                     "Small samples swing a lot.",
         "percent": ["Win %"], "color_scale": ["Win %"], "bold_cols": ["Measure"],
         "rows": summary},
        {"name": "Agreement", "title": f"Moneyline ({primary}) vs spread, game by game",
         "subtitle": "Only games where BOTH are graded.", "percent": ["Share"],
         "bold_cols": ["Combination"], "rows": agree},
        {"name": "All games", "title": "Every game (newest first)", "subtitle": note,
         "rows": games, "footer": footer, **common}]
    for date, rows in sorted(_by_date(games).items(), reverse=True):
        sheets.append({"name": date, "title": f"Games on {_pretty(date)}",
                       "subtitle": note, "rows": rows, **common})
    return write_xlsx(sheets)
