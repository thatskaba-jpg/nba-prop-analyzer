"""
elo.py — a simple, strong, BACKTESTABLE baseline for who wins a game.

Elo needs only past results, so it can be replayed over every game since 2020 to
get an honest out-of-sample record, which the hand-built game model cannot (it
uses roster strength and API ratings that only exist as of today). Anything
fancier has to beat this on the same games before it deserves trust.

  - each team has a rating; the winner takes points from the loser
  - bigger wins move it more (margin-of-victory multiplier, FiveThirtyEight style)
  - the home team gets a fixed bonus in the win-probability
  - ratings regress part of the way to average between seasons
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402

INIT = 1500.0
ELO_PER_POINT = 28.0       # ~28 Elo points per point of margin


def expected(diff):
    """Win probability for a side that is `diff` Elo points better."""
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


class Elo:
    def __init__(self, k=20.0, hca=70.0, carry=0.75):
        self.k, self.hca, self.carry = k, hca, carry
        self.r = {}
        self.season = None

    def rating(self, team):
        return self.r.get(team, INIT)

    def p_home(self, home, away):
        return expected(self.rating(home) - self.rating(away) + self.hca)

    def margin(self, home, away):
        return (self.rating(home) - self.rating(away) + self.hca) / ELO_PER_POINT

    def update(self, home, away, home_pts, away_pts, season=None):
        """Learn from one finished game (call AFTER predicting it)."""
        if season is not None and season != self.season:
            if self.season is not None:               # new season: regress to the mean
                self.r = {t: INIT + self.carry * (v - INIT) for t, v in self.r.items()}
            self.season = season
        r_h, r_a = self.rating(home), self.rating(away)
        diff = r_h - r_a + self.hca
        home_won = home_pts > away_pts
        winner_diff = diff if home_won else -diff
        mult = ((abs(home_pts - away_pts) + 3.0) ** 0.8) / (7.5 + 0.006 * winner_diff)
        delta = self.k * mult * ((1.0 if home_won else 0.0) - expected(diff))
        self.r[home], self.r[away] = r_h + delta, r_a - delta


def games_from_logs():
    """Every finished game with both final scores, oldest first, rebuilt from
    the box-score table (player points summed per side)."""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT game_id, game_date, season, is_home, matchup, points "
        "FROM player_game_logs WHERE minutes > 0", conn)
    conn.close()
    df["team"] = df["matchup"].fillna("").str.split(" ").str[0].str.upper()
    g = (df.groupby(["game_id", "game_date", "season", "is_home", "team"],
                    as_index=False)["points"].sum())
    home = (g[g["is_home"] == 1].drop(columns="is_home")
            .rename(columns={"team": "home_team", "points": "home_pts"}))
    away = (g[g["is_home"] == 0][["game_id", "team", "points"]]
            .rename(columns={"team": "away_team", "points": "away_pts"}))
    games = home.merge(away, on="game_id")
    games = games[(games["home_pts"] > 0) & (games["away_pts"] > 0)
                  & (games["home_team"] != games["away_team"])]
    games["home_win"] = (games["home_pts"] > games["away_pts"]).astype(int)
    games["margin"] = games["home_pts"] - games["away_pts"]
    return games.sort_values(["game_date", "game_id"]).reset_index(drop=True)


def replay(games, elo):
    """Run Elo through `games` in order. Returns (p_home, predicted_margin) per
    game, each made BEFORE the game was learned from."""
    p, m = [], []
    for r in games.itertuples():
        p.append(elo.p_home(r.home_team, r.away_team))
        m.append(elo.margin(r.home_team, r.away_team))
        elo.update(r.home_team, r.away_team, r.home_pts, r.away_pts, r.season)
    return p, m


def current_ratings(games=None, **params):
    """An Elo object that has learned from every finished game so far."""
    elo = Elo(**params)
    replay(games_from_logs() if games is None else games, elo)
    return elo
