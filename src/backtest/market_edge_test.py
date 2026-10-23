"""
market_edge_test.py — does the model know anything the betting market doesn't?

Takes real graded opening lines (one FanDuel line per player-game-market, DNPs
voided) and compares three probabilities that the prop goes OVER:

  - MARKET  the no-vig probability implied by the posted prices
  - MODEL   the model's raw probability for that same line
  - BLEND   a logistic mix of the two, fit on the EARLIER half of the dates and
            scored on the LATER half (lines it never saw)

If the model carried real information beyond the line, the blend would put a
clearly positive weight on the model and beat the market alone out of sample.
A weight near zero means the model is a noisy copy of what the line already says,
and any "edge" it shows against that line is model error.

Run:  python src/backtest/market_edge_test.py [--per-market 1200] [--book fanduel]
"""

import argparse
import random
import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from features.features import load_player_history  # noqa: E402
from models.calibration import fit_config, historical_context  # noqa: E402
from models.prop_model_config import analyze  # noqa: E402
from odds.ev_engine import remove_vig  # noqa: E402
from odds.market_anchor import _PATH as ANCHOR_PATH  # noqa: E402

MARKETS = ["points", "rebounds", "assists", "threes_made"]
_EPS = 1e-4


def _sample(book, per_market, seed=11):
    """One line per (game, player, market) that the book posted exactly once."""
    conn = get_connection()
    rows = []
    for prop in MARKETS:
        got = conn.execute(
            "SELECT player_id, game_id, game_date, prop_type, line, odds_over, "
            "odds_under, hit_over FROM historical_props "
            "WHERE snapshot='opening' AND book=? AND prop_type=? "
            "AND graded_at IS NOT NULL AND hit_over IS NOT NULL "
            "AND odds_over IS NOT NULL AND odds_under IS NOT NULL",
            (book, prop)).fetchall()
        by = {}
        for r in got:
            by.setdefault((r["game_id"], r["player_id"]), []).append(r)
        single = [v[0] for v in by.values() if len(v) == 1]
        random.Random(seed).shuffle(single)
        rows += single[:per_market]
    conn.close()
    return rows


def _score(rows):
    cfg = fit_config()
    hists, out = {}, []
    for r in rows:
        oo, ou = int(r["odds_over"]), int(r["odds_under"])
        if abs(oo) < 100 or abs(ou) < 100:
            continue
        pid = r["player_id"]
        if pid not in hists:
            hists[pid] = load_player_history(pid)
        hist = hists[pid]
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        is_home = srow.iloc[0]["is_home"] if len(srow) else None
        res = analyze(cfg, pid, r["prop_type"], r["line"], r["game_date"], hist,
                      historical_context(r["game_date"], opp, is_home),
                      calibrate=False)
        p_model = res.get("p_over_raw")
        p_market = remove_vig(oo, ou).get("fair_over")
        if p_model is None or p_market is None:
            continue
        out.append((r["game_date"], r["prop_type"], float(p_market),
                    float(p_model), int(r["hit_over"]), oo, ou))
    out.sort(key=lambda t: t[0])
    return out


def _logit(p):
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def _logloss(y, p):
    p = np.clip(p, _EPS, 1 - _EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _brier(y, p):
    return float(np.mean((p - y) ** 2))


def _main():
    from sklearn.linear_model import LogisticRegression

    ap = argparse.ArgumentParser(
        description="Does the model add information beyond the betting line?")
    ap.add_argument("--per-market", type=int, default=1200,
                    help="lines sampled per market (default 1200)")
    ap.add_argument("--book", default="fanduel")
    ap.add_argument("--save", action="store_true",
                    help="after testing, refit the market+model blend on ALL "
                         "sampled lines and save it to data/market_anchor.json "
                         "(the weights the app uses to anchor props to the line)")
    args = ap.parse_args()

    scored = _score(_sample(args.book, args.per_market))
    if len(scored) < 400:
        print(f"Only {len(scored)} usable lines; need a few hundred. Nothing to test.")
        return
    dates = [t[0] for t in scored]
    y = np.array([t[4] for t in scored], dtype=float)
    pm = np.array([t[2] for t in scored])
    pr = np.array([t[3] for t in scored])
    cut = len(scored) // 2
    tr, te = slice(0, cut), slice(cut, None)

    print(f"{len(scored)} {args.book} opening lines "
          f"({', '.join(MARKETS)}), {dates[0]} .. {dates[-1]}; DNPs voided.")
    print(f"Fit on {dates[0]} .. {dates[cut - 1]} (n={cut}); "
          f"scored on {dates[cut]} .. {dates[-1]} (n={len(scored) - cut}).")
    print(f"Over rate {100 * y.mean():.1f}%  vs market-implied "
          f"{100 * pm.mean():.1f}%\n")

    X_mkt = _logit(pm)[:, None]
    X_both = np.c_[_logit(pm), _logit(pr)]
    m_mkt = LogisticRegression(C=1e6).fit(X_mkt[tr], y[tr])
    m_both = LogisticRegression(C=1e6).fit(X_both[tr], y[tr])

    # Bootstrap the weight on the model so a lucky split can't fool us.
    rng = np.random.default_rng(5)
    coefs = []
    for _ in range(200):
        idx = rng.integers(0, cut, cut)
        coefs.append(LogisticRegression(C=1e6).fit(X_both[tr][idx], y[tr][idx])
                     .coef_[0][1])
    lo, hi = np.percentile(coefs, [2.5, 97.5])

    print("Scored on the later half (lower is better; coin flip = 0.6931):")
    print(f"  market alone, as posted     log loss {_logloss(y[te], pm[te]):.4f}   "
          f"brier {_brier(y[te], pm[te]):.4f}")
    print(f"  model alone (raw)           log loss {_logloss(y[te], pr[te]):.4f}   "
          f"brier {_brier(y[te], pr[te]):.4f}")
    print(f"  market, recalibrated        log loss "
          f"{_logloss(y[te], m_mkt.predict_proba(X_mkt[te])[:, 1]):.4f}")
    print(f"  market + model blend        log loss "
          f"{_logloss(y[te], m_both.predict_proba(X_both[te])[:, 1]):.4f}")
    print(f"\nBlend weights: market {m_both.coef_[0][0]:+.3f}, "
          f"model {m_both.coef_[0][1]:+.3f}  "
          f"(95% bootstrap interval for the model weight: {lo:+.3f} .. {hi:+.3f})")

    # What would betting these probabilities have done at the real prices?
    try:                      # run as a script: this folder is sys.path[0]
        from real_line_validation import Tally, _fmt
    except ImportError:       # imported as a package module
        from backtest.real_line_validation import Tally, _fmt
    from odds.ev_engine import evaluate_prop

    def _bets(p_over, threshold=0.03):
        t = Tally()
        for p, row in zip(p_over, scored[cut:]):
            oo, ou, went_over = row[5], row[6], bool(row[4])
            ev = evaluate_prop(float(p), 1.0 - float(p), oo, ou,
                               edge_threshold=threshold)
            if ev["is_value"]:
                over_side = ev["recommended_side"] == "over"
                t.add(oo if over_side else ou,
                      went_over if over_side else not went_over)
        return t

    blend_te = m_both.predict_proba(X_both[te])[:, 1]
    print("\nBetting the later half at the posted prices (1 unit, 3% edge bar):")
    print(f"  raw model value flags       {_fmt(_bets(pr[te]))}")
    print(f"  market + model blend flags  {_fmt(_bets(blend_te))}")

    # Pocket check: only the model's most confident leans, bet at the posted
    # price. The cut-offs are fixed here, not tuned, and each half is shown on
    # its own, so a pocket that exists in only one half shows up as such.
    def _leans(p_raw, rows, lo=0.25, hi=0.75):
        under, over = Tally(), Tally()
        for p, row in zip(p_raw, rows):
            oo, ou, went_over = row[5], row[6], bool(row[4])
            if p < lo:
                under.add(ou, not went_over)
            elif p > hi:
                over.add(oo, went_over)
        return under, over

    print("\nPocket check (raw P(over) < 25%: bet UNDER, > 75%: bet OVER):")
    for label, sl in (("earlier half", tr), ("later half  ", te)):
        u, o = _leans(pr[sl], scored[sl])
        print(f"  {label}  strong under lean  {_fmt(u)}")
        print(f"  {label}  strong over lean   {_fmt(o)}")

    print("\nRaw model confidence vs what happened (all sampled lines):")
    print(f"  {'raw P(over)':<14}{'lines':>7}{'model says':>12}"
          f"{'market says':>13}{'went over':>11}")
    edges = [0.0, 0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90, 1.0001]
    for a, b in zip(edges[:-1], edges[1:]):
        m = (pr >= a) & (pr < b)
        if m.sum() >= 30:
            print(f"  {a:.2f}-{min(b, 1.0):.2f}{'':<7}{int(m.sum()):>7}"
                  f"{100 * pr[m].mean():>11.1f}%{100 * pm[m].mean():>12.1f}%"
                  f"{100 * y[m].mean():>10.1f}%")

    print("\nRead it like this: if the interval on the model weight covers 0 and")
    print("the blend barely beats the recalibrated market, the model adds nothing")
    print("the line didn't already contain. If the model's confident buckets don't")
    print("go over any more often than the market's, its extra confidence is noise.")

    if args.save:
        import datetime as _dt
        import json

        final = LogisticRegression(C=1e6).fit(X_both, y)
        rng2 = np.random.default_rng(9)
        boot = []
        for _ in range(200):
            idx = rng2.integers(0, len(y), len(y))
            boot.append(LogisticRegression(C=1e6).fit(X_both[idx], y[idx])
                        .coef_[0][1])
        lo2, hi2 = np.percentile(boot, [2.5, 97.5])
        payload = {
            "intercept": round(float(final.intercept_[0]), 4),
            "w_market": round(float(final.coef_[0][0]), 4),
            "w_model": round(float(final.coef_[0][1]), 4),
            "meta": {
                "n_lines": int(len(y)),
                "fit_from": dates[0],
                "fit_through": dates[-1],
                "book": args.book,
                "markets": MARKETS,
                "model_weight_95ci": [round(float(lo2), 4), round(float(hi2), 4)],
                "fitted_at": _dt.datetime.now(_dt.timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"),
            },
        }
        ANCHOR_PATH.parent.mkdir(exist_ok=True)
        try:   # keep the separately-fitted "fitted" block if it's already there
            existing = json.loads(ANCHOR_PATH.read_text())
        except Exception:
            existing = {}
        existing.update(payload)
        ANCHOR_PATH.write_text(json.dumps(existing, indent=2))
        print(f"\nSaved blend weights to {ANCHOR_PATH}")
        print(f"  intercept {payload['intercept']:+.3f}, market weight "
              f"{payload['w_market']:+.3f}, model weight "
              f"{payload['w_model']:+.3f} (95% interval {lo2:+.3f} .. {hi2:+.3f})")


if __name__ == "__main__":
    _main()
