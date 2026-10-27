"""
retrain.py — keep the models current, but only accept a new fit if it earns it.

Retraining every night on a handful of new games changes almost nothing, and a
bad refit can quietly make things worse. So every retrain goes through a GATE:

  1. Train a candidate on everything except the most recent `window_days`.
  2. Score it on those held-out days against a dumb baseline (the player's own
     10-game average). If the candidate isn't at least as good as that baseline
     it is rejected and the current model stays.
  3. Only if it passes, train the real model on ALL data and save it.

Every attempt is logged to data/retrain_log.json (accepted or not, and why).
The market-blend / calibration weights are refit weekly, with the old files
backed up first so a bad refit can be rolled back.

    python3 src/models/retrain.py nightly      # fitted projection (gated)
    python3 src/models/retrain.py weekly       # calibration + market blend
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "data" / "retrain_log.json"
STATS = ["points", "rebounds", "assists", "threes_made"]


def _log(entry):
    try:
        rows = json.loads(LOG.read_text()) if LOG.exists() else []
    except Exception:
        rows = []
    rows.append(entry)
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(json.dumps(rows[-60:], indent=1))


def holdout_scores(models, hold, predict_mean):
    """(model_mae, naive_mae) averaged over the stats, on held-out rows."""
    import numpy as np
    m, n = [], []
    for s in STATS:
        y = hold[s].to_numpy(dtype=float)
        pred = np.asarray(predict_mean(models, hold, s), dtype=float)
        naive_col = f"{s}_r10" if f"{s}_r10" in hold.columns else f"{s}_ew10"
        naive = hold[naive_col].fillna(hold[f"{s}_ew10"]).to_numpy(dtype=float)
        m.append(float(np.mean(np.abs(pred - y))))
        n.append(float(np.mean(np.abs(naive - y))))
    return sum(m) / len(m), sum(n) / len(n)


def retrain_if_better(table=None, window_days=14, min_holdout=300, tolerance=1.0,
                      fit=None, save=None, load=None, predict_mean=None, now=None):
    """Retrain the fitted projection if there are new games AND the candidate
    passes the gate. Returns a dict {action, reason, ...}; never raises."""
    import pandas as pd
    from models import fitted_projection as fp
    fit = fit or fp.fit
    save = save or fp.save
    load = load or fp.load
    predict_mean = predict_mean or fp.predict_mean
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now or time.time()))
    try:
        table = fp.build_features() if table is None else table
        latest = str(table["game_date"].max())
        cur = load()
        trained_through = ((cur or {}).get("_meta") or {}).get("trained_through")
        if cur and trained_through and trained_through >= latest:
            res = {"action": "skip", "reason": f"already trained through {latest}"}
        else:
            cutoff = (pd.to_datetime(latest) - pd.Timedelta(days=window_days)
                      ).strftime("%Y-%m-%d")
            hold = table[table["game_date"] >= cutoff]
            if len(hold) < min_holdout:
                res = {"action": "keep", "reason": (
                    f"only {len(hold)} held-out rows in the last {window_days} "
                    f"days (need {min_holdout}); too little to validate a refit, "
                    "keeping the current model")}
            else:
                cand = fit(table, cutoff)
                mae, naive = holdout_scores(cand, hold, predict_mean)
                if mae <= naive * tolerance:
                    full = fit(table, (pd.to_datetime(latest) + pd.Timedelta(days=1)
                                       ).strftime("%Y-%m-%d"))
                    save(full, trained_through=latest)
                    res = {"action": "retrained", "reason": (
                        f"passed the gate: MAE {mae:.3f} vs 10-game-average "
                        f"{naive:.3f} on {len(hold)} held-out rows"),
                        "trained_through": latest}
                else:
                    res = {"action": "rejected", "reason": (
                        f"failed the gate: MAE {mae:.3f} is worse than the "
                        f"10-game average {naive:.3f}; kept the current model")}
    except Exception as e:  # noqa: BLE001
        res = {"action": "error", "reason": f"{type(e).__name__}: {str(e)[:150]}"}
    res["at"] = stamp
    _log(res)
    return res


def refit_weights(run=None, backup_dir=None):
    """Weekly: refit calibration + market blend, backing up the old files first.
    `run(cmd)` is injectable for tests. Returns {file: "ok"/"failed"}."""
    run = run or (lambda cmd: subprocess.run(cmd, cwd=str(ROOT), timeout=3600,
                                             capture_output=True).returncode)
    backup_dir = Path(backup_dir or ROOT / "data" / "weights_backup")
    backup_dir.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    out = {}
    jobs = [("calibration.json", [sys.executable, "src/models/calibration.py", "fit"]),
            ("market_anchor.json", [sys.executable, "src/backtest/market_edge_test.py", "--save"])]
    for fname, cmd in jobs:
        src = ROOT / "data" / fname
        if src.exists():
            shutil.copy2(src, backup_dir / f"{day}_{fname}")
        try:
            out[fname] = "ok" if run(cmd) == 0 else "failed (old file kept in weights_backup/)"
        except Exception as e:  # noqa: BLE001
            out[fname] = f"failed: {type(e).__name__}"
    _log({"action": "weights", "result": out,
          "at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())})
    return out


def main(argv):
    mode = argv[1] if len(argv) > 1 else "nightly"
    if mode == "weekly":
        print(refit_weights())
    else:
        print(retrain_if_better())


if __name__ == "__main__":
    main(sys.argv)
