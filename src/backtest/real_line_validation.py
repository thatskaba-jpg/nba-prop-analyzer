"""
real_line_validation.py — grade the model against REAL stored opening lines.

Replays the model on graded real sportsbook lines (historical_props) and reports,
PER PROP TYPE, what a flat 1-unit bettor would actually have made at the REAL
posted prices (not an assumed -110):

  - "always over" / "always under"  the dumb baselines
  - "every pick"                    bet whichever side the model leans, every time
  - "value flags"                   bet only when the model flags value (edge over
                                    the no-vig line >= 3% AND positive EV — the
                                    same rule the dashboard uses)

each for RAW and CALIBRATED probabilities side by side, so you can see exactly
what the calibration layer changed.

HONESTY
  - One row per stored book line, scored against ITS OWN line, price and outcome.
    (An earlier version averaged the line across books but took the MAX outcome,
    so the label didn't match the line the model saw, and it assumed a flat -110
    even where the book was charging +200/-300.)
  - Players who didn't play are voided upstream (hit_over is NULL) and excluded.
  - "needs" is the hit rate the prices actually paid require to break even; the
    ROI is shown with a 95% interval so a lucky stretch isn't mistaken for skill.
  - OUT OF SAMPLE: run `python src/models/calibration.py fit --holdout DATE`
    first. This script then scores only lines from DATE on, with the holdout map
    that never saw them. Without that, the CAL columns are in-sample and flattering.
  - No leakage: results are read only for already-final games.
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from features.features import load_player_history  # noqa: E402
from models.prop_model_config import analyze  # noqa: E402
from models.calibration import (  # noqa: E402
    _HOLDOUT_PATH, _load, calibrate_p_over, fit_config, historical_context,
    load_fit_file)
from odds.ev_engine import (  # noqa: E402
    american_to_decimal, evaluate_prop, implied_probability)

MARKETS = ["points", "rebounds", "assists", "threes_made", "pra", "pr", "pa", "ra"]
DEFAULT_MAX_ROWS = 6000     # lines scored per market (random sample, fixed seed)
VALUE_THRESHOLD = 0.03      # same edge bar the dashboard uses


class Tally:
    """Flat 1-unit bets, settled at the price actually paid."""

    def __init__(self):
        self.n = 0
        self.wins = 0
        self.profit = 0.0
        self.sq = 0.0
        self.be = 0.0

    def add(self, odds, won):
        dec = american_to_decimal(odds)
        p = (dec - 1.0) if won else -1.0
        self.n += 1
        self.wins += 1 if won else 0
        self.profit += p
        self.sq += p * p
        self.be += implied_probability(odds)   # vig-inclusive break-even

    def hit(self):
        return self.wins / self.n if self.n else None

    def roi(self):
        return self.profit / self.n if self.n else None

    def break_even(self):
        return self.be / self.n if self.n else None

    def roi_ci95(self):
        """Half-width of a 95% interval on the per-bet ROI."""
        if self.n < 2:
            return None
        mean = self.profit / self.n
        var = max(self.sq / self.n - mean * mean, 0.0) * self.n / (self.n - 1)
        return 1.96 * (var / self.n) ** 0.5


def _rows(prop, since=None, max_rows=DEFAULT_MAX_ROWS):
    """Graded opening lines for one prop: one row per book line. DNPs (voided)
    and pushes have hit_over NULL and are excluded."""
    conn = get_connection()
    q = ("SELECT player_id, game_id, game_date, prop_type, line, odds_over, "
         "odds_under, hit_over FROM historical_props "
         "WHERE snapshot='opening' AND graded_at IS NOT NULL "
         "AND hit_over IS NOT NULL AND odds_over IS NOT NULL "
         "AND odds_under IS NOT NULL AND prop_type=? ")
    params = [prop]
    if since:
        q += "AND game_date >= ? "
        params.append(since)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    if len(rows) > max_rows:
        rows = random.Random(11).sample(rows, max_rows)
    return rows


def validate(prop, fit_obj=None, since=None, max_rows=DEFAULT_MAX_ROWS,
             value_threshold=VALUE_THRESHOLD):
    cfg = fit_config()
    hist_cache = {}
    t = {k: Tally() for k in ("over", "under", "raw_pick", "cal_pick",
                              "raw_value", "cal_value")}
    scored = overs = 0

    for r in _rows(prop, since, max_rows):
        oo, ou = int(r["odds_over"]), int(r["odds_under"])
        if abs(oo) < 100 or abs(ou) < 100:
            continue  # blank/invalid price: skip rather than guess one
        pid = r["player_id"]
        hist = hist_cache.get(pid)
        if hist is None:
            hist = hist_cache[pid] = load_player_history(pid)
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        is_home = srow.iloc[0]["is_home"] if len(srow) else None
        res = analyze(cfg, pid, prop, r["line"], r["game_date"], hist,
                      historical_context(r["game_date"], opp, is_home),
                      calibrate=False)
        rp = res.get("p_over")
        if rp is None:
            continue
        push = res.get("push") or 0.0
        went_over = r["hit_over"] == 1

        scored += 1
        overs += 1 if went_over else 0
        t["over"].add(oo, went_over)
        t["under"].add(ou, not went_over)

        cp = calibrate_p_over(rp, prop, fit_obj)
        for tag, p in (("raw", rp), ("cal", cp)):
            pick_over = p > 0.5
            t[f"{tag}_pick"].add(oo if pick_over else ou,
                                 went_over if pick_over else not went_over)
            ev = evaluate_prop(p, max(1.0 - p - push, 0.0), oo, ou,
                               edge_threshold=value_threshold)
            if ev["is_value"]:
                over_side = ev["recommended_side"] == "over"
                t[f"{tag}_value"].add(oo if over_side else ou,
                                      went_over if over_side else not went_over)

    return {"prop": prop, "scored": scored,
            "over_rate": (overs / scored) if scored else 0.0, "tallies": t}


def _fmt(t):
    if not t.n:
        return "     0 bets"
    ci = t.roi_ci95()
    ci_s = f" ±{100 * ci:.1f}" if ci is not None else ""
    return (f"{t.n:>6} bets  hit {100 * t.hit():5.1f}%  "
            f"needs {100 * t.break_even():5.1f}%  "
            f"ROI {100 * t.roi():+6.1f}%{ci_s}")


def _main():
    ap = argparse.ArgumentParser(
        description="Score the prop model against real graded sportsbook lines.")
    ap.add_argument("props", nargs="*", help="prop types (default: all markets)")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="only score lines on/after this date")
    ap.add_argument("--fit", metavar="PATH",
                    help="calibration fit to score (default: the holdout fit if "
                         "it exists, else the live one)")
    ap.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                    help="lines scored per market (random sample)")
    args = ap.parse_args()

    if args.fit:
        fit_obj, label = load_fit_file(args.fit), args.fit
    elif _HOLDOUT_PATH.exists():
        fit_obj, label = (load_fit_file(_HOLDOUT_PATH),
                          "holdout fit (data/calibration_holdout.json)")
    else:
        fit_obj, label = _load(), "LIVE fit (data/calibration.json)"
    fit_before = (fit_obj.get("meta") or {}).get("fit_before") if fit_obj else None
    since = args.since or fit_before

    print(f"Real-line validation, scoring the {label}")
    if fit_before and since and since >= fit_before:
        print(f"OUT OF SAMPLE: calibration trained on lines before {fit_before}; "
              f"scoring lines from {since} on.\n")
    else:
        print("WARNING: IN-SAMPLE. The calibration being scored was trained on "
              "these same lines, so the CAL rows are flattering.\n"
              "         Run `python src/models/calibration.py fit --holdout "
              "YYYY-MM-DD` and rerun this for an honest read.\n")

    for prop in (args.props or MARKETS):
        v = validate(prop, fit_obj=fit_obj, since=since, max_rows=args.max_rows)
        if not v["scored"]:
            print(f"{prop}: no graded lines in that window\n")
            continue
        t = v["tallies"]
        print(f"{prop.upper()}  ({v['scored']} lines, over rate "
              f"{100 * v['over_rate']:.1f}%)")
        print(f"  always OVER            {_fmt(t['over'])}")
        print(f"  always UNDER           {_fmt(t['under'])}")
        print(f"  RAW  every pick        {_fmt(t['raw_pick'])}")
        print(f"  CAL  every pick        {_fmt(t['cal_pick'])}")
        print(f"  RAW  value flags       {_fmt(t['raw_value'])}")
        print(f"  CAL  value flags       {_fmt(t['cal_value'])}\n")

    print("Notes:")
    print("- Prices are the real posted opening prices on each row, not a flat")
    print("  -110. 'needs' is the hit rate those prices require to break even.")
    print("- ROI is per 1-unit bet with a 95% interval. If the interval covers 0,")
    print("  the result can't be told apart from luck.")
    print("- 'value flags' use the dashboard's rule: edge over the no-vig line")
    print("  >= 3% and positive EV. Players who didn't play are voided upstream.")


if __name__ == "__main__":
    _main()
