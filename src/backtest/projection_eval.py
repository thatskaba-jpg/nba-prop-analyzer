"""
projection_eval.py — is the FITTED projection better than the rules-based one,
and does either tell us anything the betting line doesn't?

Trains src/models/fitted_projection.py on every game BEFORE the first test line,
then scores real FanDuel opening lines (one line per player-game-market, DNPs
voided) with both models, side by side, on the same lines:

  - how far each projection lands from the actual stat, vs the line itself
  - log loss of each model's P(over) alone, vs the market
  - a market + model logistic blend fit on the EARLIER half of the dates and
    scored on the LATER half: the weight a model earns and the log-loss it adds

Run:  python src/backtest/projection_eval.py [--per-bucket 1500]
"""

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from features.features import load_player_history  # noqa: E402
from models import fitted_projection as fp  # noqa: E402
from models.baseline_model import over_under_probabilities  # noqa: E402
from models.calibration import fit_config, historical_context  # noqa: E402
from models.prop_model_config import analyze  # noqa: E402
from odds.ev_engine import remove_vig  # noqa: E402

CUTOFF = "2025-10-21"            # first date of the stored real lines
BUCKETS = [("wk 1-3   (Oct 21-Nov 10)", "2025-10-21", "2025-11-10"),
           ("wk 4-10  (Nov 11-Dec 31)", "2025-11-11", "2025-12-31"),
           ("wk 11+   (Jan-Apr)", "2026-01-01", "2026-06-30")]
_EPS = 1e-4


def _sample(per_bucket, book="fanduel"):
    conn = get_connection()
    rows = []
    for prop in fp.STATS:
        got = conn.execute(
            "SELECT player_id, game_id, game_date, prop_type, line, odds_over, "
            "odds_under, hit_over, actual FROM historical_props "
            "WHERE snapshot='opening' AND book=? AND prop_type=? "
            "AND graded_at IS NOT NULL AND hit_over IS NOT NULL "
            "AND actual IS NOT NULL AND odds_over IS NOT NULL "
            "AND odds_under IS NOT NULL", (book, prop)).fetchall()
        by = {}
        for r in got:
            by.setdefault((r["game_id"], r["player_id"]), []).append(r)
        single = [v[0] for v in by.values() if len(v) == 1]
        for bi, (_, lo, hi) in enumerate(BUCKETS):
            pool = [r for r in single if lo <= r["game_date"] <= hi]
            random.Random(3 + bi).shuffle(pool)
            rows += [(bi, r) for r in pool[:per_bucket]]
    conn.close()
    return rows


def _logit(p):
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _ll(y, p):
    p = np.clip(p, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main():
    from sklearn.linear_model import LogisticRegression

    ap = argparse.ArgumentParser()
    ap.add_argument("--per-bucket", type=int, default=1500,
                    help="lines per market per period (default 1500)")
    ap.add_argument("--save-anchor", action="store_true",
                    help="fit the market + FITTED-model blend on all scored lines "
                         "and store it in data/market_anchor.json under 'fitted'")
    args = ap.parse_args()
    t0 = time.time()

    print("Building leak-free features for every played game...", flush=True)
    table = fp.build_features()
    print(f"  {len(table)} player-games, {len(fp.feature_columns(table))} features "
          f"({time.time() - t0:.0f}s)", flush=True)
    print(f"Training on games before {CUTOFF}...", flush=True)
    models = fp.fit(table, CUTOFF)
    print(f"  done ({time.time() - t0:.0f}s). Dispersion alpha: "
          + ", ".join(f"{s} {models[s]['alpha']:.3f}" for s in fp.STATS), flush=True)

    index = {(int(r.player_id), str(r.game_id)): i
             for i, r in enumerate(table[["player_id", "game_id"]].itertuples())}
    mu_cache = {s: fp.predict_mean(models, table[table["game_date"] >= CUTOFF], s)
                for s in fp.STATS}
    test = table[table["game_date"] >= CUTOFF]
    test_pos = {(int(p), str(g)): k for k, (p, g) in
                enumerate(zip(test["player_id"], test["game_id"]))}

    cfg, hists, recs = fit_config(), {}, []
    for bi, r in _sample(args.per_bucket):
        oo, ou = int(r["odds_over"]), int(r["odds_under"])
        k = test_pos.get((int(r["player_id"]), str(r["game_id"])))
        if abs(oo) < 100 or abs(ou) < 100 or k is None:
            continue
        pid = r["player_id"]
        if pid not in hists:
            hists[pid] = load_player_history(pid)
        h = hists[pid]
        srow = h[h["game_date"] == r["game_date"]] if not h.empty else h
        if not len(srow):
            continue
        old = analyze(cfg, pid, r["prop_type"], r["line"], r["game_date"], h,
                      historical_context(r["game_date"], srow.iloc[0]["opponent"],
                                         srow.iloc[0]["is_home"]), calibrate=False)
        pm = remove_vig(oo, ou).get("fair_over")
        if old.get("p_over_raw") is None or old.get("projection") is None or pm is None:
            continue
        mu = float(mu_cache[r["prop_type"]][k])
        std = float(fp.std_for(models, r["prop_type"], np.array([mu]))[0])
        p_new = over_under_probabilities(mu, float(r["line"]), std)["p_over"]
        if p_new is None:
            continue
        recs.append((bi, r["game_date"], float(pm), float(old["p_over_raw"]),
                     float(p_new), int(r["hit_over"]), float(old["projection"]),
                     mu, float(r["line"]), float(r["actual"])))
    recs.sort(key=lambda t: t[1])
    A = np.array([(t[0],) + t[2:] for t in recs], dtype=float)
    b, pm, p_old, p_new, y, proj_old, proj_new, line, actual = (A[:, i] for i in range(9))
    print(f"\nScored {len(A)} lines in {time.time() - t0:.0f}s.\n")

    print(f"{'period':<26}{'lines':>6}{'MAE old':>9}{'MAE new':>9}{'MAE line':>9}"
          f"{'LL old':>8}{'LL new':>8}{'LL mkt':>8}")
    for bi, (name, *_ ) in enumerate(BUCKETS):
        m = b == bi
        if m.sum() < 30:
            continue
        print(f"{name:<26}{int(m.sum()):>6}"
              f"{np.mean(np.abs(proj_old[m] - actual[m])):>9.2f}"
              f"{np.mean(np.abs(proj_new[m] - actual[m])):>9.2f}"
              f"{np.mean(np.abs(line[m] - actual[m])):>9.2f}"
              f"{_ll(y[m], p_old[m]):>8.4f}{_ll(y[m], p_new[m]):>8.4f}"
              f"{_ll(y[m], pm[m]):>8.4f}")
    print(f"{'ALL':<26}{len(A):>6}"
          f"{np.mean(np.abs(proj_old - actual)):>9.2f}"
          f"{np.mean(np.abs(proj_new - actual)):>9.2f}"
          f"{np.mean(np.abs(line - actual)):>9.2f}"
          f"{_ll(y, p_old):>8.4f}{_ll(y, p_new):>8.4f}{_ll(y, pm):>8.4f}")

    cut = len(A) // 2
    tr, te = slice(0, cut), slice(cut, None)
    variants = {
        "market only": np.c_[_logit(pm)],
        "market + old model": np.c_[_logit(pm), _logit(p_old)],
        "market + new model": np.c_[_logit(pm), _logit(p_new)],
        "market + both": np.c_[_logit(pm), _logit(p_old), _logit(p_new)],
    }
    print(f"\nBlend fit on the earlier half, scored on the later half (n={len(A) - cut}):")
    base = None
    for name, X in variants.items():
        lr = LogisticRegression(C=1e6).fit(X[tr], y[tr])
        loss = _ll(y[te], lr.predict_proba(X[te])[:, 1])
        base = loss if base is None else base
        w = ", ".join(f"{c:+.3f}" for c in lr.coef_[0])
        print(f"  {name:<22} log loss {loss:.4f}  (gain vs market {base - loss:+.4f})  "
              f"weights [{w}]")
    print("\nHigher gain = the model tells us something the line did not.")

    if args.save_anchor:
        import datetime as _dt
        import json
        from odds.market_anchor import _PATH as ANCHOR_PATH
        X = variants["market + new model"]
        final = LogisticRegression(C=1e6).fit(X, y)
        rng = np.random.default_rng(9)
        boot = [LogisticRegression(C=1e6).fit(X[i], y[i]).coef_[0][1]
                for i in (rng.integers(0, len(y), len(y)) for _ in range(200))]
        lo, hi = np.percentile(boot, [2.5, 97.5])
        try:
            data = json.loads(ANCHOR_PATH.read_text())
        except Exception:
            data = {}
        data["fitted"] = {
            "intercept": round(float(final.intercept_[0]), 4),
            "w_market": round(float(final.coef_[0][0]), 4),
            "w_model": round(float(final.coef_[0][1]), 4),
            "meta": {"n_lines": int(len(y)), "model_weight_95ci": [round(float(lo), 4), round(float(hi), 4)],
                     "model_trained_before": CUTOFF,
                     "fitted_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
        }
        ANCHOR_PATH.write_text(json.dumps(data, indent=2))
        print(f"\nSaved fitted-model blend to {ANCHOR_PATH}: market "
              f"{data['fitted']['w_market']:+.3f}, model {data['fitted']['w_model']:+.3f} "
              f"(95% interval {lo:+.3f} .. {hi:+.3f})")


if __name__ == "__main__":
    main()
