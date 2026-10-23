"""Over/under probability math (Poisson / negative binomial)."""

import unittest

import helpers  # noqa: F401  (puts src/ on sys.path)
from models.baseline_model import over_under_probabilities


class ProbabilityTests(unittest.TestCase):
    def test_half_point_line_has_no_push(self):
        r = over_under_probabilities(22.0, 21.5, std=6.0)
        self.assertEqual(r["push"], 0.0)
        self.assertAlmostEqual(r["p_over"] + r["p_under"], 1.0, places=9)

    def test_whole_number_line_splits_out_the_push(self):
        r = over_under_probabilities(22.0, 22.0, std=6.0)
        self.assertGreater(r["push"], 0.0)
        self.assertAlmostEqual(r["p_over"] + r["p_under"] + r["push"], 1.0,
                               places=9)

    def test_p_over_falls_as_the_line_rises(self):
        lines = (14.5, 17.5, 20.5, 23.5, 26.5)
        probs = [over_under_probabilities(20.0, ln, std=5.0)["p_over"]
                 for ln in lines]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_overdispersed_player_has_fatter_tails_than_poisson(self):
        wide = over_under_probabilities(20.0, 32.5, std=8.0)["p_over"]
        poisson = over_under_probabilities(20.0, 32.5, std=None)["p_over"]
        self.assertGreater(wide, poisson)

    def test_narrow_spread_falls_back_to_poisson(self):
        # std below sqrt(mean) is not overdispersed, so no negative binomial.
        narrow = over_under_probabilities(20.0, 22.5, std=2.0)["p_over"]
        default = over_under_probabilities(20.0, 22.5, std=None)["p_over"]
        self.assertAlmostEqual(narrow, default, places=12)

    def test_negative_binomial_keeps_the_projection_as_its_mean(self):
        # E[X] = sum over k>=1 of P(X >= k); P(X >= k) is p_over at line k-0.5.
        mean = 20.0
        for std in (8.0, None):
            expectation = sum(
                over_under_probabilities(mean, k - 0.5, std=std)["p_over"]
                for k in range(1, 600))
            self.assertAlmostEqual(expectation, mean, places=4, msg=f"std={std}")

    def test_negative_projection_gives_no_probability(self):
        r = over_under_probabilities(-1.0, 10.5)
        self.assertIsNone(r["p_over"])
        self.assertIsNone(r["p_under"])


if __name__ == "__main__":
    unittest.main()
