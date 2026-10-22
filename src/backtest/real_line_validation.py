"""
real_line_validation.py — grade the model against REAL stored opening lines.

Reads graded rows from historical_props (the real sportsbook lines we captured
+ backfilled), re-runs the model on each, and reports, PER PROP TYPE:

  - accuracy of the model's over/under PICK vs the break-even bar (52.4% at -110)
  - the naive "always over" rate (baseline)
  - the same, but only on bets the model flags as VALUE (its EV edge), so we can
    see whether calibration makes the value flag more trustworthy.

It reports RAW and CALIBRATED side by side so we can see exactly what the
calibration layer changed. No leakage: results are read only for already-final
games (hit_over is set).
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from features.features import load_player_history  # noqa: E402
from models.prop_model_config import analyze, build_config  # noqa: E402
from collect.goat_data import team_ratings_map  # noqa: E402
from models.calibration import calibrate_p_over  # noqa: E402

BREAK_EVEN = 0.524
MARKETS = ["points", "rebounds", "assists", "threes_made", "pra", "pr", "pa", "ra"]


def _cfg():
    return build_config("Recommended", "weighted", {
        "player_last_n_avg": 35, "opponent_defense": 25,
        "opponent_team_rating": 15, "rest_days": 10,
        "home_away_split": 7, "minutes_trend": 8})


def _rows(prop):
    conn = get_connection()
    rows = conn.execute(
        "SELECT player_id, game_id, game_date, prop_type, AVG(line) line, "
        "MAX(hit_over) hit_over FROM historical_props "
        "WHERE snapshot='opening' AND graded_at IS NOT NULL "
        "AND hit_over IS NOT NULL AND prop_type=? "
        "GROUP BY player_id, game_id, prop_type", (prop,)).fetchall()
    conn.close()
    return rows


def validate(prop, value_threshold=0.03):
    cfg = _cfg()
    tr = team_ratings_map(2025)
    hist_cache = {}
    # tallies: [picks, correct] for raw & calibrated, overall & value-only
    raw = {"n": 0, "correct": 0, "v_n": 0, "v_correct": 0}
    cal = {"n": 0, "correct": 0, "v_n": 0, "v_correct": 0}
    overs = 0
    total = 0

    for r in _rows(prop):
        pid = r["player_id"]
        hist = hist_cache.get(pid)
        if hist is None:
            hist = hist_cache[pid] = load_player_history(pid)
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        res = analyze(cfg, pid, prop, r["line"], r["game_date"], hist,
                      {"opponent_abbr": opp, "team_ratings_map": tr},
                      calibrate=False)
        rp = res.get("p_over")
        if rp is None:
            continue
        cp = calibrate_p_over(rp, prop)
        went_over = 1 if r["hit_over"] == 1 else 0
        total += 1
        overs += went_over

        # RAW pick: over if p_over > 0.5
        for tag, p, store in (("raw", rp, raw), ("cal", cp, cal)):
            pick_over = p > 0.5
            correct = (pick_over and went_over) or (not pick_over and not went_over)
            store["n"] += 1
            store["correct"] += 1 if correct else 0
            # VALUE-only: the model's prob clears the vig by a margin on the
            # side it picks (|p - 0.5| big enough to imply an edge at -110).
            edge = (p - BREAK_EVEN) if pick_over else ((1 - p) - BREAK_EVEN)
            if edge >= value_threshold:
                store["v_n"] += 1
                store["v_correct"] += 1 if correct else 0

    return {"prop": prop, "total": total,
            "over_rate": overs / total if total else 0, "raw": raw, "cal": cal}


def _pct(c, n):
    return f"{100*c/n:5.1f}% (n={n})" if n else "   —   (n=0)"


def _verdict(c, n):
    if not n:
        return ""
    return "ABOVE break-even" if c / n > BREAK_EVEN else "below"


if __name__ == "__main__":
    props = sys.argv[1:] or MARKETS
    print(f"Real-line validation (break-even = {BREAK_EVEN:.1%} at -110)\n")
    print(f"{'PROP':<12}{'always-over':<16}{'RAW pick':<16}"
          f"{'CAL pick':<16}{'RAW value':<18}{'CAL value':<18}")
    print("-" * 96)
    for prop in props:
        v = validate(prop)
        if not v["total"]:
            print(f"{prop:<12}no graded rows")
            continue
        raw, cal = v["raw"], v["cal"]
        print(f"{prop:<12}"
              f"{100*v['over_rate']:5.1f}% (n={v['total']})".ljust(16)
              + _pct(raw["correct"], raw["n"]).ljust(16)
              + _pct(cal["correct"], cal["n"]).ljust(16)
              + _pct(raw["v_correct"], raw["v_n"]).ljust(18)
              + _pct(cal["v_correct"], cal["v_n"]).ljust(18))
    print("\nNotes:")
    print("- 'pick' accuracy = how often the model's over/under call was right.")
    print("- 'value' accuracy = accuracy only on bets the model flags as an EV")
    print("  edge. This is the column calibration should improve/clean up.")
    print("- Calibration does NOT change the over/under PICK at p=0.5, so RAW")
    print("  and CAL pick accuracy are expected to match; the value columns are")
    print("  where the honest-probability fix actually matters.")
