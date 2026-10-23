"""
game_model_eval.py — how good is each way of picking game winners, honestly?

Replays every finished game since 2020 in date order. Models are tuned on the
first 80% and scored on the newest 20%, so nobody grades their own homework:

  always home   the dumbest pick (home teams win ~55%)
  Elo           ratings from results only, tuned on the first 80%
  trained ML    src/models/game_ml_model.py (rolling team form -> logistic/ridge)

The hand-built game model (roster strength + API ratings + a stack of nudges) is
NOT here, because its inputs only exist as of today and cannot be rebuilt for a
past game. It can only be judged going forward (the frozen-picks scoreboard);
whatever it does there should be read against the rows below.

Run:  python src/backtest/game_model_eval.py
"""

import itertools
import math
import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1]))

from models import game_ml_model as gm  # noqa: E402
from models.elo import Elo, games_from_logs, replay  # noqa: E402

BURN_IN = 500       # games before scores count (every team starts at 1500)


def _ll(y, p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main():
    games = games_from_logs()
    cut = int(len(games) * 0.8)
    train, test = games.iloc[:cut], games.iloc[cut:]
    print(f"{len(games)} finished games {games['game_date'].iloc[0]} .. "
          f"{games['game_date'].iloc[-1]}; tuned on the first {cut}, scored on the "
          f"newest {len(test)} ({test['game_date'].iloc[0]} on).\n")

    # Tune Elo on the training games only.
    best = None
    for k, hca, carry in itertools.product((10, 15, 20, 30), (50, 70, 90), (0.6, 0.75, 0.9)):
        p, _ = replay(train, Elo(k, hca, carry))
        loss = _ll(train["home_win"].iloc[BURN_IN:], p[BURN_IN:])
        if best is None or loss < best[0]:
            best = (loss, k, hca, carry)
    _, k, hca, carry = best
    print(f"Elo tuned on the training games: K={k}, home bonus={hca}, "
          f"season carry-over={carry}\n")

    # Replay everything with those settings; score only the newest 20%.
    p_all, m_all = replay(games, Elo(k, hca, carry))
    p_te, m_te = np.array(p_all[cut:]), np.array(m_all[cut:])
    y_te = test["home_win"].to_numpy()
    margin_te = test["margin"].to_numpy()

    home_rate = float(train["home_win"].mean())
    rows = [("always pick the home team", float(np.mean(y_te == 1)),
             _ll(y_te, np.full(len(y_te), home_rate)),
             float(np.mean(np.abs(margin_te - train["margin"].mean()))))]
    rows.append(("Elo (results only)", float(np.mean((p_te >= 0.5) == (y_te == 1))),
                 _ll(y_te, p_te), float(np.mean(np.abs(m_te - margin_te)))))

    ml = gm.train(save=False, games=games, refit_all=False)
    if ml:
        met = ml["metrics"]
        rows.append(("trained ML (team form)", met["ml_accuracy"], met["log_loss"],
                     met["margin_mae"]))

    print(f"{'on the newest ' + str(len(test)) + ' games':<32}{'winner right':>13}"
          f"{'log loss':>10}{'margin MAE':>12}")
    for name, acc, ll, mae in rows:
        print(f"{name:<32}{100 * acc:>12.1f}%{ll:>10.4f}{mae:>12.2f}")
    print("\nA coin flip scores log loss 0.6931. The betting market is not in this "
          "table: no historical game lines are stored, so it can't be compared yet.")


if __name__ == "__main__":
    main()
