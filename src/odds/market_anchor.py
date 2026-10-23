"""
market_anchor.py — keep the model honest by anchoring it to the betting line.

WHY. Out of sample (src/backtest/market_edge_test.py, ~20K real graded lines) the
prop model carried no measurable information beyond the posted line: its weight
in a logistic blend with the market was +0.04 (95% interval -0.02 to +0.09). Fed
to the EV engine on its own, its probabilities marked ~80% of all lines as
"value", and those bets lost 4-6% each, no better than picking at random after
the vig.

WHAT THIS DOES. For a prop with real two-way prices it returns a probability that
starts from the market's no-vig number and lets the model move it only as far as
the evidence supports:

    logit(p) = intercept + w_market * logit(market no-vig P(over))
                         + w_model  * logit(model raw P(over))

The weights are learned from graded real lines and stored in
data/market_anchor.json (refit with `market_edge_test.py --save`). If the model
starts to earn a bigger weight, its influence grows automatically. With no
weights file, or with unusable prices, probabilities pass through unchanged.

PROPS ONLY. The weights were learned on player props. Game lines don't use this.
"""

import json
import math
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from odds.ev_engine import remove_vig  # noqa: E402

_PATH = Path(__file__).resolve().parents[2] / "data" / "market_anchor.json"
_EPS = 1e-4
_cache = {"key": None, "weights": None}


def load_weights():
    """The saved blend weights, or None if the file is missing or unusable.
    Re-read automatically when the file changes (e.g. after a refit)."""
    try:
        key = (str(_PATH), _PATH.stat().st_mtime)
    except OSError:
        return None
    if _cache["key"] == key:
        return _cache["weights"]
    try:
        d = json.loads(_PATH.read_text())
        weights = _parse(d)
        # Optional second set, learned for the FITTED projection (see
        # models/fitted_projection.py). Used instead of the base set whenever a
        # fitted probability is supplied.
        try:
            weights["fitted"] = _parse(d["fitted"]) if d.get("fitted") else None
        except (KeyError, ValueError, TypeError):
            weights["fitted"] = None
    except (OSError, ValueError, KeyError, TypeError):
        weights = None
    _cache["key"], _cache["weights"] = key, weights
    return weights


def _parse(d):
    return {
        "intercept": float(d["intercept"]),
        "w_market": float(d["w_market"]),
        # A negative weight would mean "bet against the model"; never act
        # on that, just ignore the model.
        "w_model": max(float(d["w_model"]), 0.0),
    }


def _logit(p):
    p = min(max(float(p), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def _valid_price(odds):
    return isinstance(odds, (int, float)) and (odds >= 100 or odds <= -100)


def anchor_to_market(p_over, p_under, odds_over, odds_under,
                     p_over_raw=None, weights=None, p_over_fitted=None,
                     fair_over=None):
    """Blend the model with the market line for one two-way prop.

    p_over / p_under   the model's probabilities (used as-is if we can't anchor,
                       and to size the push probability on whole-number lines)
    odds_over/under    the real American prices on each side
    p_over_raw         the model's UNCALIBRATED P(over), which is what the weights
                       were learned on; falls back to p_over if not given
    p_over_fitted      P(over) from the fitted projection, when today's slate has
                       one. If given (and fitted weights exist) it replaces the
                       rules-based number as the model input.
    fair_over          the market's no-vig P(over) if the caller already has a
                       better estimate than these two prices (e.g. the consensus
                       across books); default is this book's own two-way price
    weights            override (tests); default is the saved file

    Returns {"p_over", "p_under", "anchored"}. anchored is False when the inputs
    were returned unchanged.
    """
    unchanged = {"p_over": p_over, "p_under": p_under, "anchored": False}
    w = weights if weights is not None else load_weights()
    if not w or p_over is None or p_under is None:
        return unchanged
    if fair_over is None:
        if not (_valid_price(odds_over) and _valid_price(odds_under)):
            return unchanged
        fair_over = remove_vig(odds_over, odds_under).get("fair_over")
        if fair_over is None:
            return unchanged

    if p_over_fitted is not None and w.get("fitted"):
        w, model = w["fitted"], p_over_fitted
    else:
        model = p_over_raw if p_over_raw is not None else p_over
    z = (w["intercept"] + w["w_market"] * _logit(fair_over)
         + w["w_model"] * _logit(model))
    blended = _sigmoid(z)
    push = max(1.0 - p_over - p_under, 0.0)   # whole-number lines only
    return {"p_over": round(blended, 4),
            "p_under": round(max(1.0 - blended - push, 0.0), 4),
            "anchored": True}
