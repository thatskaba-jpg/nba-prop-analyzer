"""Market anchoring: the model may only nudge the line's own probability."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts src/ on sys.path)
from odds import market_anchor as ma
from odds.ev_engine import evaluate_prop, remove_vig

MARKET_ONLY = {"intercept": 0.0, "w_market": 1.0, "w_model": 0.0}
NUDGE = {"intercept": 0.0, "w_market": 0.9, "w_model": 0.1}


class AnchorTests(unittest.TestCase):
    def test_passes_through_without_weights(self):
        r = ma.anchor_to_market(0.8, 0.2, -110, -110, weights={})
        self.assertEqual(r, {"p_over": 0.8, "p_under": 0.2, "anchored": False})

    def test_market_only_weights_reproduce_the_no_vig_line(self):
        r = ma.anchor_to_market(0.9, 0.1, -130, 110, weights=MARKET_ONLY)
        self.assertTrue(r["anchored"])
        self.assertAlmostEqual(r["p_over"], remove_vig(-130, 110)["fair_over"],
                               places=3)

    def test_a_confident_model_only_nudges(self):
        neutral = ma.anchor_to_market(0.5, 0.5, -110, -110, weights=NUDGE)
        bold = ma.anchor_to_market(0.9, 0.1, -110, -110, weights=NUDGE)
        self.assertAlmostEqual(neutral["p_over"], 0.5, places=4)
        self.assertGreater(bold["p_over"], neutral["p_over"])
        self.assertLess(bold["p_over"], 0.56)

    def test_rises_with_the_model(self):
        probs = [ma.anchor_to_market(m, 1 - m, -110, -110, weights=NUDGE)["p_over"]
                 for m in (0.1, 0.3, 0.5, 0.7, 0.9)]
        self.assertEqual(probs, sorted(probs))

    def test_prefers_the_raw_model_probability_when_given(self):
        with_raw = ma.anchor_to_market(0.5, 0.5, -110, -110, p_over_raw=0.9,
                                       weights=NUDGE)
        direct = ma.anchor_to_market(0.9, 0.1, -110, -110, weights=NUDGE)
        self.assertEqual(with_raw["p_over"], direct["p_over"])

    def test_invalid_prices_pass_through(self):
        for over, under in ((0, 0), (-50, 120), (None, -110)):
            r = ma.anchor_to_market(0.7, 0.3, over, under, weights=NUDGE)
            self.assertFalse(r["anchored"])
            self.assertEqual(r["p_over"], 0.7)

    def test_push_probability_is_kept(self):
        # 10% chance of landing exactly on a whole-number line.
        r = ma.anchor_to_market(0.45, 0.45, -110, -110, weights=MARKET_ONLY)
        self.assertAlmostEqual(r["p_over"] + r["p_under"], 0.9, places=3)

    def test_a_balanced_line_cannot_be_flagged_however_sure_the_model_is(self):
        weights = {"intercept": 0.0, "w_market": 0.9, "w_model": 0.04}
        r = ma.anchor_to_market(0.90, 0.10, -110, -110, weights=weights)
        ev = evaluate_prop(r["p_over"], r["p_under"], -110, -110)
        self.assertFalse(ev["is_value"])


class FittedAndConsensusTests(unittest.TestCase):
    BOTH = {"intercept": 0.0, "w_market": 0.9, "w_model": 0.0,
            "fitted": {"intercept": 0.0, "w_market": 0.6, "w_model": 0.4}}

    def test_the_fitted_probability_and_its_weights_are_used_when_given(self):
        base = ma.anchor_to_market(0.5, 0.5, -110, -110, weights=self.BOTH)
        fitted = ma.anchor_to_market(0.5, 0.5, -110, -110, weights=self.BOTH,
                                     p_over_fitted=0.8)
        self.assertAlmostEqual(base["p_over"], 0.5, places=3)   # model weight 0 here
        self.assertGreater(fitted["p_over"], 0.58)               # 0.4 * logit(0.8)

    def test_without_fitted_weights_the_fitted_probability_is_ignored(self):
        w = {"intercept": 0.0, "w_market": 1.0, "w_model": 0.0, "fitted": None}
        r = ma.anchor_to_market(0.5, 0.5, -110, -110, weights=w, p_over_fitted=0.9)
        self.assertAlmostEqual(r["p_over"], 0.5, places=3)

    def test_a_consensus_probability_can_replace_the_books_own_price(self):
        # The book's own -110/-110 says 50%; the other books say 60%.
        r = ma.anchor_to_market(0.5, 0.5, -110, -110, weights=MARKET_ONLY,
                                fair_over=0.60)
        self.assertAlmostEqual(r["p_over"], 0.60, places=3)

    def test_a_consensus_probability_works_without_prices(self):
        r = ma.anchor_to_market(0.5, 0.5, None, None, weights=MARKET_ONLY,
                                fair_over=0.55)
        self.assertTrue(r["anchored"])
        self.assertAlmostEqual(r["p_over"], 0.55, places=3)


class LoadWeightsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "market_anchor.json"
        patcher = mock.patch.object(ma, "_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fresh()

    def fresh(self):
        ma._cache.update(key=None, weights=None)

    def write(self, payload):
        self.path.write_text(payload if isinstance(payload, str)
                             else json.dumps(payload))
        self.fresh()

    def test_missing_file_gives_none(self):
        self.assertIsNone(ma.load_weights())

    def test_reads_the_weights(self):
        self.write({"intercept": -0.05, "w_market": 0.9, "w_model": 0.04})
        self.assertEqual(ma.load_weights(),
                         {"intercept": -0.05, "w_market": 0.9, "w_model": 0.04,
                          "fitted": None})

    def test_reads_the_fitted_block_when_present(self):
        self.write({"intercept": 0, "w_market": 0.9, "w_model": 0.04,
                    "fitted": {"intercept": 0.1, "w_market": 0.7, "w_model": 0.2}})
        self.assertEqual(ma.load_weights()["fitted"],
                         {"intercept": 0.1, "w_market": 0.7, "w_model": 0.2})

    def test_a_broken_fitted_block_is_ignored_not_fatal(self):
        self.write({"intercept": 0, "w_market": 0.9, "w_model": 0.04,
                    "fitted": {"intercept": 0.1}})
        w = ma.load_weights()
        self.assertEqual(w["w_market"], 0.9)
        self.assertIsNone(w["fitted"])

    def test_a_negative_model_weight_is_ignored(self):
        self.write({"intercept": 0, "w_market": 1, "w_model": -0.2})
        self.assertEqual(ma.load_weights()["w_model"], 0.0)

    def test_unusable_files_give_none(self):
        self.write("{not json")
        self.assertIsNone(ma.load_weights())
        self.write({"intercept": 0})
        self.assertIsNone(ma.load_weights())

    def test_the_saved_file_is_used_by_default(self):
        self.write({"intercept": 0.0, "w_market": 1.0, "w_model": 0.0})
        r = ma.anchor_to_market(0.9, 0.1, -110, -110)
        self.assertTrue(r["anchored"])
        self.assertAlmostEqual(r["p_over"], 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
