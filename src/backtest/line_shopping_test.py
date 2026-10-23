"""
line_shopping_test.py — how much is the best price across books worth?

You store opening prices from several sportsbooks for the same prop. Two
questions, both answerable without any predictive model:

  1. SHOPPING. If you bet a side anyway, how much better is the best available
     price than the average book's price?
  2. STALE NUMBERS. When one book's price is better than what the OTHER books
     imply, does betting it actually pay? For each book, the "market" is the
     average no-vig probability of the OTHER books quoting the same line
     (leave-one-out, so a book never grades itself). A price is flagged when
     probability(other books) x payout - 1 clears a threshold.

Only same-line comparisons are used (every book posting 24.5 points, say), so the
outcome is identical for all of them. DNPs are voided upstream.

Run:  python src/backtest/line_shopping_test.py [--min-books 4]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402


def _implied(odds):
    odds = np.asarray(odds, dtype=float)
    return np.where(odds > 0, 100.0 / (odds + 100.0), -odds / (-odds + 100.0))


def _decimal(odds):
    odds = np.asarray(odds, dtype=float)
    return np.where(odds > 0, 1.0 + odds / 100.0, 1.0 + 100.0 / -odds)


def load(min_books=4):
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT game_id, player_id, prop_type, book, line, odds_over, odds_under, "
        "hit_over FROM historical_props WHERE snapshot='opening' "
        "AND graded_at IS NOT NULL AND hit_over IS NOT NULL "
        "AND odds_over IS NOT NULL AND odds_under IS NOT NULL", conn)
    conn.close()
    ok = (df["odds_over"].abs() >= 100) & (df["odds_under"].abs() >= 100)
    df = df[ok].copy()
    # One row per book per (game, player, prop, line).
    df = df.drop_duplicates(["game_id", "player_id", "prop_type", "book", "line"])
    key = ["game_id", "player_id", "prop_type", "line"]
    df["n_books"] = df.groupby(key)["book"].transform("count")
    df = df[df["n_books"] >= min_books].copy()

    po, pu = _implied(df["odds_over"]), _implied(df["odds_under"])
    df["fair_over"] = po / (po + pu)
    g = df.groupby(key)["fair_over"]
    total, cnt = g.transform("sum"), g.transform("count")
    df["others_over"] = (total - df["fair_over"]) / (cnt - 1)   # leave-one-out
    df["dec_over"], df["dec_under"] = _decimal(df["odds_over"]), _decimal(df["odds_under"])
    df["ev_over"] = df["others_over"] * df["dec_over"] - 1.0
    df["ev_under"] = (1.0 - df["others_over"]) * df["dec_under"] - 1.0
    return df, key


def _profit(side_over, dec, hit_over):
    won = np.where(side_over, hit_over == 1, hit_over == 0)
    return np.where(won, dec - 1.0, -1.0)


def _roi_line(label, profit, extra=""):
    n = len(profit)
    if n < 2:
        return f"  {label:<34}{n:>7} bets"
    roi = profit.mean()
    ci = 1.96 * profit.std(ddof=1) / np.sqrt(n)
    return f"  {label:<34}{n:>7} bets  ROI {100 * roi:+6.2f}% ±{100 * ci:.2f}{extra}"


def main():
    ap = argparse.ArgumentParser(description="Value of line shopping / stale numbers")
    ap.add_argument("--min-books", type=int, default=4)
    args = ap.parse_args()

    df, key = load(args.min_books)
    groups = df.groupby(key).ngroups
    print(f"{len(df)} book prices across {groups} same-line prop groups "
          f"({args.min_books}+ books each), opening snapshot.\n")

    # 1) SHOPPING: always-under, at the average price vs the best price.
    avg_dec = df.groupby(key)["dec_under"].transform("mean")
    first = df.groupby(key).head(1)
    avg_profit = _profit(np.zeros(len(first), bool),
                         avg_dec.loc[first.index].values, first["hit_over"].values)
    best = df.loc[df.groupby(key)["dec_under"].idxmax()]
    best_profit = _profit(np.zeros(len(best), bool), best["dec_under"].values,
                          best["hit_over"].values)
    print("1) SHOPPING (bet every UNDER; same bets, different price)")
    print(_roi_line("at the average book's price", avg_profit))
    print(_roi_line("at the best available price", best_profit))
    print(f"  best price is worth about {100 * (best_profit.mean() - avg_profit.mean()):+.2f} "
          "points of ROI per bet\n")

    # 2) STALE NUMBERS: best (book, side) per group, flagged by EV vs OTHER books.
    cand = pd.concat([
        df.assign(side_over=True, ev=df["ev_over"], dec=df["dec_over"]),
        df.assign(side_over=False, ev=df["ev_under"], dec=df["dec_under"]),
    ])
    cand = cand.reset_index(drop=True)
    top = cand.loc[cand.groupby(key)["ev"].idxmax()]
    top_profit = _profit(top["side_over"].values, top["dec"].values,
                         top["hit_over"].values)
    print("2) STALE NUMBERS (best book+side per prop, EV judged by the OTHER books)")
    print(_roi_line("every prop, best price", top_profit,
                    f"   predicted EV {100 * top['ev'].mean():+.2f}%"))
    for theta in (0.0, 0.01, 0.02, 0.03, 0.05):
        m = (top["ev"] >= theta).values
        print(_roi_line(f"flagged: EV >= {100 * theta:.0f}%", top_profit[m],
                        f"   predicted EV {100 * top['ev'][m].mean():+.2f}%"
                        if m.any() else ""))
    print("\nRead it like this: if realized ROI tracks predicted EV, the other books'")
    print("number is a good yardstick and stale prices are real money. If it is")
    print("far below, the 'edge' was just the books being posted at different times.")


if __name__ == "__main__":
    main()
