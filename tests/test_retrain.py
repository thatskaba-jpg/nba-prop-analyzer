import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import retrain as r  # noqa: E402


def make_table(n=600, days=30):
    rng = np.random.default_rng(0)
    dates = pd.date_range("2026-10-01", periods=days).strftime("%Y-%m-%d")
    t = pd.DataFrame({"game_date": rng.choice(dates, n)})
    for s in r.STATS:
        t[s] = rng.poisson(10, n).astype(float)
        t[f"{s}_r10"] = 10.0
        t[f"{s}_ew10"] = 10.0
    return t


class FakeModels(dict):
    pass


def pred_const(value):
    return lambda models, table, stat: np.full(len(table), value)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "log.json"
        self.p = mock.patch.object(r, "LOG", self.tmp)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def run_gate(self, predict, load_val=None, table=None, **kw):
        saved = []
        res = r.retrain_if_better(
            table=make_table() if table is None else table,
            fit=lambda t, c: FakeModels(),
            save=lambda m, trained_through: saved.append(trained_through),
            load=lambda: load_val, predict_mean=predict, **kw)
        return res, saved

    def test_good_candidate_is_accepted_and_saved(self):
        res, saved = self.run_gate(pred_const(10.0))        # same as the baseline
        self.assertEqual(res["action"], "retrained")
        self.assertEqual(len(saved), 1)

    def test_bad_candidate_is_rejected_and_nothing_saved(self):
        res, saved = self.run_gate(pred_const(25.0))        # far worse than baseline
        self.assertEqual(res["action"], "rejected")
        self.assertEqual(saved, [])

    def test_skips_when_already_trained_through_latest(self):
        t = make_table()
        cur = {"_meta": {"trained_through": str(t["game_date"].max())}}
        res, saved = self.run_gate(pred_const(10.0), load_val=cur, table=t)
        self.assertEqual(res["action"], "skip")
        self.assertEqual(saved, [])

    def test_too_few_holdout_rows_keeps_model(self):
        res, saved = self.run_gate(pred_const(10.0), table=make_table(n=40))
        self.assertEqual(res["action"], "keep")
        self.assertEqual(saved, [])

    def test_errors_never_raise_and_are_logged(self):
        res = r.retrain_if_better(table=make_table(), fit=mock.Mock(side_effect=RuntimeError("boom")),
                                  save=lambda *a, **k: None, load=lambda: None,
                                  predict_mean=pred_const(10.0))
        self.assertEqual(res["action"], "error")
        self.assertTrue(self.tmp.exists())


class WeightsTests(unittest.TestCase):
    def test_backs_up_then_reports_failures_without_raising(self):
        tmp = Path(tempfile.mkdtemp())
        calls = []

        def run(cmd):
            calls.append(cmd[1])
            return 1 if "calibration" in cmd[1] else 0
        with mock.patch.object(r, "LOG", tmp / "log.json"):
            out = r.refit_weights(run=run, backup_dir=tmp / "bk")
        self.assertIn("failed", out["calibration.json"])
        self.assertEqual(out["market_anchor.json"], "ok")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
