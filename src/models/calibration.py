"""
calibration.py — make the model's probabilities MEAN what they say.

THE PROBLEM (measured, not assumed). On 6,000 real graded opening lines the raw
model was badly OVERCONFIDENT: when it said "5% over" the prop actually went over
~39% of the time; when it said "85% over" it hit only ~47%. The actual over-rate
barely moved (39%->53%) while the model's stated probability swung 5%->85%. An EV
engine fed those numbers flags coin-flips as "great value".

THE FIX (post-hoc calibration). We DON'T touch the projection. We learn a map
from the model's RAW P(over) to the EMPIRICAL over-rate using the real graded
lines, then apply that map to every probability before it reaches the EV engine.
We use ISOTONIC regression (monotonic, non-decreasing) so the correction is
smooth and never says "a higher raw prob should win less often".

HONESTY
  - Trained ONLY on graded real lines (historical_props). Projections never see
    the result, so there's no leakage into the projection — this only corrects
    the probability AFTER the projection is made.
  - ONE TRAINING ROW PER BOOK LINE. Each row is scored against its own line and
    labelled with its own outcome. (An earlier version averaged the line across
    books but took the MAX outcome, so the label and the line it was scored
    against didn't match.)
  - Players who didn't play (DNP) are voided upstream (hit_over is NULL) and
    never reach the fit. They used to be graded as unders.
  - Historical context is leak-free: opponent defense is built from games before
    the Monday of the game's week, and no end-of-season team ratings are used.
  - fit(fit_before=DATE) trains only on lines BEFORE that date, so
    real_line_validation can score the map on lines it never saw. The live file
    (data/calibration.json) is the final fit on everything; the holdout fit is
    saved separately (data/calibration_holdout.json) and never used live.
  - If no fit exists, calibrate_p_over() is the identity (no silent change).

USAGE
    python src/models/calibration.py fit                       # live fit, all lines
    python src/models/calibration.py fit --holdout 2026-02-01  # honest test fit
"""

import argparse
import datetime as _dt
import json
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_FIT_PATH = _DATA_DIR / "calibration.json"
_HOLDOUT_PATH = _DATA_DIR / "calibration_holdout.json"
# Markets we calibrate (enough liquidity to grade reliably).
_MARKETS = ["points", "rebounds", "assists", "threes_made", "pra", "pr", "pa", "ra"]
_MIN_PER_PROP = 800        # below this, fall back to the pooled map
_MIN_BIN = 25              # ignore sparse probability bins
_MAX_PER_PROP = 12000      # cap on lines scored per market (keeps a fit minutes, not hours)


def _isotonic(xs, ys, ws):
    """Pool-adjacent-violators isotonic regression (non-decreasing), weighted.
    xs sorted ascending; returns the fitted y at each x."""
    # Each block: [sum(w*y), sum(w), mean, count]
    merged = []
    for y, w in zip(ys, ws):
        merged.append([y * w, w, y, 1])
        while len(merged) > 1 and merged[-2][2] > merged[-1][2]:
            b = merged.pop()
            a = merged.pop()
            swy = a[0] + b[0]
            sw = a[1] + b[1]
            merged.append([swy, sw, swy / sw, a[3] + b[3]])
    out_vals = []
    for blk in merged:
        out_vals.extend([blk[2]] * blk[3])
    return out_vals


def _bin_stats(pairs):
    """pairs: list of (raw_p_over, went_over 0/1). Return sorted bin centers,
    empirical over-rate, and weight (n) per 5% bin."""
    bins = {}
    for p, y in pairs:
        b = min(int(p * 20), 19)  # 5% bins
        s = bins.setdefault(b, [0, 0])
        s[0] += 1
        s[1] += y
    xs, ys, ws = [], [], []
    for b in sorted(bins):
        n, ov = bins[b]
        if n < _MIN_BIN:
            continue
        xs.append((b * 5 + 2.5) / 100.0)
        ys.append(ov / n)
        ws.append(n)
    return xs, ys, ws


# --------------------------------------------------------------------------- #
# Leak-free context for a historical line (shared by fit + validation)
# --------------------------------------------------------------------------- #

_DEF_CACHE = {}


def fit_config():
    """The model configuration calibration is fit and validated against. One
    definition, shared by fit() and real_line_validation, so the map is always
    scored on the same model it was learned from."""
    from models.prop_model_config import build_config
    return build_config("CalibrationFit", "weighted", {
        "player_last_n_avg": 35, "opponent_defense": 25,
        "opponent_team_rating": 15, "rest_days": 10,
        "home_away_split": 7, "minutes_trend": 8})


def _week_start(game_date):
    d = _dt.date.fromisoformat(str(game_date)[:10])
    return (d - _dt.timedelta(days=d.weekday())).isoformat()


def _defense_map_for(game_date):
    """Opponent-defense factors for a historical game, built ONLY from games
    strictly before the Monday of that game's week (so never from the game
    itself or later). Cached per week: ~30 builds cover a whole season."""
    wk = _week_start(game_date)
    if wk not in _DEF_CACHE:
        from features.opponent_defense import defense_factors
        _DEF_CACHE[wk] = defense_factors(wk) or {}
    return _DEF_CACHE[wk]


def historical_context(game_date, opponent, is_home=None):
    """The context the prop model sees for a graded historical line.

    Only pre-game facts: the opponent, the venue, and a defense map cut off
    before the game's week. Team-ratings maps are deliberately NOT passed: the
    stored ones are full-season snapshots, which for a past game would include
    that very game and everything after it. Factors that need live feeds
    (injuries, lineups, game total) can't be reconstructed for past games and
    simply stay off, exactly as they do when the feed is missing live."""
    home = None
    try:
        if is_home is not None and is_home == is_home:  # NaN != NaN
            home = bool(int(is_home))
    except (TypeError, ValueError):
        home = None
    opp = opponent if isinstance(opponent, str) and opponent else None
    return {"opponent_abbr": opp,
            "defense_factor_map": _defense_map_for(game_date),
            "upcoming_is_home": home}


def _collect_all(per_prop_limit=_MAX_PER_PROP, fit_before=None, progress=True):
    """Run the model on graded real lines and return
    [(prop_type, raw_p_over, went_over, game_date), ...].

    One row per stored book line (its own line, its own outcome). Pushes and
    voided DNPs have hit_over NULL and are excluded by the query. If fit_before
    is given, only lines dated BEFORE it are used."""
    from features.features import load_player_history
    from models.prop_model_config import analyze

    cfg = fit_config()
    conn = get_connection()
    q = ("SELECT player_id, game_id, game_date, prop_type, line, hit_over "
         "FROM historical_props "
         "WHERE snapshot='opening' AND graded_at IS NOT NULL "
         "AND hit_over IS NOT NULL AND prop_type IN (%s) "
         % ",".join("?" * len(_MARKETS)))
    params = list(_MARKETS)
    if fit_before:
        q += "AND game_date < ? "
        params.append(fit_before)
    rows = conn.execute(q, params).fetchall()
    conn.close()

    # Cap each market so one huge market can't dominate the run time.
    rng = random.Random(7)  # local RNG: don't reseed the global random module
    by_prop = {}
    for r in rows:
        by_prop.setdefault(r["prop_type"], []).append(r)
    chosen = []
    for prop in _MARKETS:
        rs = by_prop.get(prop, [])
        if len(rs) > per_prop_limit:
            rs = rng.sample(rs, per_prop_limit)
        chosen.extend(rs)

    hc = {}
    triples = []
    total = len(chosen)
    for i, r in enumerate(chosen, 1):
        pid = r["player_id"]
        hist = hc.get(pid)
        if hist is None:
            hist = hc[pid] = load_player_history(pid)
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        is_home = srow.iloc[0]["is_home"] if len(srow) else None
        ctx = historical_context(r["game_date"], opp, is_home)
        res = analyze(cfg, pid, r["prop_type"], r["line"], r["game_date"],
                      hist, ctx, calibrate=False)
        if res.get("p_over") is None:
            continue
        triples.append((r["prop_type"], res["p_over"],
                        1 if r["hit_over"] == 1 else 0, r["game_date"]))
        if progress and i % 5000 == 0:
            print(f"  ...{i}/{total} lines scored", flush=True)
    return triples


def fit(save=True, fit_before=None, path=None):
    """Fit per-prop and pooled calibration maps from graded real lines.

    fit_before  'YYYY-MM-DD': train only on lines before that date (for an
                honest out-of-sample test). None = train on everything.
    path        where to save (default: the live data/calibration.json).
    Returns the fit dict."""
    triples = _collect_all(fit_before=fit_before)
    fit_obj = {"pooled": None, "per_prop": {}, "meta": {}}
    if not triples:
        print("No graded real lines to fit on — nothing saved.")
        return fit_obj

    pooled_pairs = [(p, y) for _, p, y, _ in triples]
    xs, ys, ws = _bin_stats(pooled_pairs)
    if xs:
        fit_obj["pooled"] = {"x": xs, "y": _isotonic(xs, ys, ws), "n": sum(ws)}

    for prop in _MARKETS:
        pp = [(p, y) for pr, p, y, _ in triples if pr == prop]
        if len(pp) < _MIN_PER_PROP:
            continue
        xs, ys, ws = _bin_stats(pp)
        if xs:
            fit_obj["per_prop"][prop] = {
                "x": xs, "y": _isotonic(xs, ys, ws), "n": sum(ws)}

    dates = [d for _, _, _, d in triples]
    fit_obj["meta"] = {
        "pooled_n": len(triples),
        "fit_before": fit_before,          # None = fit on every graded line
        "fit_rows_from": min(dates),
        "fit_rows_through": max(dates),
        "label_rule": "one row per book line, own outcome; DNP voided",
        "fitted_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"),
    }
    if save:
        target = Path(path) if path else _FIT_PATH
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(fit_obj, indent=2))
    return fit_obj


_CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        if _FIT_PATH.exists():
            try:
                _CACHE = json.loads(_FIT_PATH.read_text())
            except Exception:
                _CACHE = {}
        else:
            _CACHE = {}
    return _CACHE


def load_fit_file(path):
    """Read a calibration fit from an explicit path (e.g. the holdout fit).
    Returns {} if it's missing or unreadable."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _interp(x, xs, ys):
    """Linear interpolation on the fitted (xs, ys), clamped to the ends."""
    if not xs:
        return x
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def calibrate_p_over(raw_p_over, prop_type=None, fit_obj=None):
    """Map a raw model P(over) to the empirically-calibrated P(over).
    Identity if no fit is loaded. Uses the per-prop map when available, else
    the pooled map. fit_obj lets a caller score a specific fit (e.g. the
    holdout one) instead of the live file."""
    if raw_p_over is None:
        return None
    if fit_obj is None:
        fit_obj = _load()
    if not fit_obj:
        return raw_p_over
    m = (fit_obj.get("per_prop") or {}).get(prop_type) or fit_obj.get("pooled")
    if not m:
        return raw_p_over
    cal = _interp(raw_p_over, m["x"], m["y"])
    return min(max(cal, 1e-4), 1 - 1e-4)


def is_available():
    return bool(_load())


def _main():
    ap = argparse.ArgumentParser(
        description="Fit the probability calibration from graded real lines.")
    ap.add_argument("command", choices=["fit"])
    ap.add_argument("--holdout", metavar="YYYY-MM-DD",
                    help="train only on lines BEFORE this date and save to "
                         "data/calibration_holdout.json (never used live), so "
                         "real_line_validation can score it out of sample")
    args = ap.parse_args()

    if args.holdout:
        _dt.date.fromisoformat(args.holdout)  # fail fast on a bad date
        print(f"Fitting HOLDOUT calibration on lines before {args.holdout} "
              "(re-runs the model on the data; this takes a few minutes)...")
        f = fit(save=True, fit_before=args.holdout, path=_HOLDOUT_PATH)
        target = _HOLDOUT_PATH
    else:
        print("Fitting LIVE calibration on every graded real line (re-runs the "
              "model on the data; this takes a few minutes)...")
        f = fit(save=True)
        target = _FIT_PATH
    if not f.get("meta"):
        return
    print(f"Pooled fit on n={f['meta'].get('pooled_n')} lines "
          f"({f['meta'].get('fit_rows_from')} .. {f['meta'].get('fit_rows_through')}).")
    print(f"Per-prop maps: {list(f['per_prop'].keys())}")
    if f.get("pooled"):
        print("\nPooled map (raw -> calibrated):")
        for x, y in zip(f["pooled"]["x"], f["pooled"]["y"]):
            print(f"  {x:.2f} -> {y:.3f}")
    print(f"\nSaved to {target}")
    if args.holdout:
        print("Now score it on the lines it never saw:\n"
              "  python src/backtest/real_line_validation.py")


if __name__ == "__main__":
    _main()
