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
  - Per-prop maps where there's enough data; a pooled map as fallback.
  - The fit is saved to data/calibration.json so the dashboard loads it without
    re-fitting. Re-fit whenever more graded lines accumulate.
  - If no fit exists, apply_calibration() is the identity (no silent change).
"""

import json
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402

_FIT_PATH = Path(__file__).resolve().parents[2] / "data" / "calibration.json"
# Markets we calibrate (enough liquidity to grade reliably).
_MARKETS = ["points", "rebounds", "assists", "threes_made", "pra", "pr", "pa", "ra"]
_MIN_PER_PROP = 800        # below this, fall back to the pooled map
_MIN_BIN = 25              # ignore sparse probability bins


def _isotonic(xs, ys, ws):
    """Pool-adjacent-violators isotonic regression (non-decreasing).
    xs sorted ascending; returns fitted y at each x. Weighted by ws."""
    # blocks: [sum_wy, sum_w, value]
    blocks = [[y * w, w, y] for y, w in zip(ys, ws)]
    i = 0
    merged = []
    for blk in blocks:
        merged.append(blk)
        while len(merged) > 1 and merged[-2][2] > merged[-1][2]:
            a = merged.pop()
            b = merged.pop()
            swy = a[0] + b[0]
            sw = a[1] + b[1]
            merged.append([swy, sw, swy / sw])
    # expand back to per-point fitted values
    fitted = []
    for blk in merged:
        # how many original points this block covers is implicit; rebuild by
        # walking: simpler to recompute from scratch below.
        pass
    # Recompute mapping by walking original xs against merged block values.
    # Rebuild block sizes:
    out_vals = []
    bi = 0
    # We need counts per block; redo PAV tracking counts.
    blocks2 = [[y * w, w, y, 1] for y, w in zip(ys, ws)]
    merged2 = []
    for blk in blocks2:
        merged2.append(blk)
        while len(merged2) > 1 and merged2[-2][2] > merged2[-1][2]:
            a = merged2.pop()
            b = merged2.pop()
            swy = a[0] + b[0]
            sw = a[1] + b[1]
            cnt = a[3] + b[3]
            merged2.append([swy, sw, swy / sw, cnt])
    for blk in merged2:
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


def _collect(prop_type=None, limit=60000):
    """Gather (raw_p_over, went_over) by RE-RUNNING the model on graded lines.
    Imported lazily to avoid a heavy import at module load."""
    from features.features import load_player_history
    from models.prop_model_config import analyze, build_config
    from collect.goat_data import team_ratings_map

    cfg = build_config("CalibrationFit", "weighted", {
        "player_last_n_avg": 35, "opponent_defense": 25,
        "opponent_team_rating": 15, "rest_days": 10,
        "home_away_split": 7, "minutes_trend": 8})
    conn = get_connection()
    q = ("SELECT player_id, game_id, game_date, prop_type, AVG(line) line, "
         "MAX(hit_over) hit_over FROM historical_props "
         "WHERE snapshot='opening' AND graded_at IS NOT NULL "
         "AND hit_over IS NOT NULL ")
    params = []
    if prop_type:
        q += "AND prop_type=? "
        params.append(prop_type)
    else:
        q += "AND prop_type IN (%s) " % ",".join("?" * len(_MARKETS))
        params += _MARKETS
    q += "GROUP BY player_id, game_id, prop_type"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    if len(rows) > limit:
        random.seed(7)
        rows = random.sample(rows, limit)

    tr = {}
    hc = {}
    pairs = []
    for r in rows:
        season = 2025
        tr_map = tr.setdefault(season, team_ratings_map(season))
        pid = r["player_id"]
        hist = hc.get(pid)
        if hist is None:
            hist = hc[pid] = load_player_history(pid)
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        res = analyze(cfg, pid, r["prop_type"], r["line"], r["game_date"],
                      hist, {"opponent_abbr": opp, "team_ratings_map": tr_map},
                      calibrate=False)
        if res.get("p_over") is None:
            continue
        pairs.append((res["p_over"], 1 if r["hit_over"] == 1 else 0))
    return pairs


def fit(save=True):
    """Fit per-prop and pooled calibration maps from graded real lines.
    Returns the fit dict. Writes data/calibration.json when save=True."""
    fit_obj = {"pooled": None, "per_prop": {}, "meta": {}}

    pooled_pairs = _collect(None)
    xs, ys, ws = _bin_stats(pooled_pairs)
    if xs:
        fit_obj["pooled"] = {"x": xs, "y": _isotonic(xs, ys, ws), "n": sum(ws)}

    for prop in _MARKETS:
        pp = _collect(prop)
        if len(pp) < _MIN_PER_PROP:
            continue
        xs, ys, ws = _bin_stats(pp)
        if xs:
            fit_obj["per_prop"][prop] = {
                "x": xs, "y": _isotonic(xs, ys, ws), "n": sum(ws)}

    fit_obj["meta"]["pooled_n"] = len(pooled_pairs)
    if save:
        _FIT_PATH.parent.mkdir(exist_ok=True)
        _FIT_PATH.write_text(json.dumps(fit_obj, indent=2))
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


def calibrate_p_over(raw_p_over, prop_type=None):
    """Map a raw model P(over) to the empirically-calibrated P(over).
    Identity if no fit is loaded. Uses the per-prop map when available, else
    the pooled map."""
    if raw_p_over is None:
        return None
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


if __name__ == "__main__":
    import sys as _s
    if len(_s.argv) > 1 and _s.argv[1] == "fit":
        print("Fitting calibration from graded real lines (this re-runs the "
              "model on the data; give it a moment)...")
        f = fit(save=True)
        print(f"Pooled fit on n={f['meta'].get('pooled_n')} props.")
        print(f"Per-prop maps: {list(f['per_prop'].keys())}")
        if f.get("pooled"):
            print("\nPooled map (raw -> calibrated):")
            for x, y in zip(f["pooled"]["x"], f["pooled"]["y"]):
                print(f"  {x:.2f} -> {y:.3f}")
        print(f"\nSaved to {_FIT_PATH}")
    else:
        print("usage: calibration.py fit")
