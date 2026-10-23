"""Fitted projection: leak-free features, teammates-out, and the live slate."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import helpers  # noqa: F401  (puts src/ on sys.path)
from models import fitted_projection as fp

PLAYERS = {"P1": ("AAA", 1), "P2": ("AAA", 2), "P3": ("BBB", 3), "P4": ("BBB", 4)}


def make_logs(n_games=20, p2_out_game=15):
    """AAA vs BBB, home/away alternating; P2 sits out one game."""
    dates = list(pd.date_range("2026-01-01", periods=n_games, freq="2D")
                 .strftime("%Y-%m-%d"))
    rows = []
    for g in range(n_games):
        home = "AAA" if g % 2 == 0 else "BBB"
        for name, (team, pid) in PLAYERS.items():
            opp = "BBB" if team == "AAA" else "AAA"
            is_home = 1 if team == home else 0
            out = name == "P2" and g == p2_out_game
            rows.append({
                "player_id": pid, "player_name": name, "game_id": f"g{g:02d}",
                "game_date": dates[g], "season": "2025", "season_i": 2025.0,
                "matchup": f"{team} vs {opp}" if is_home else f"{team} @ {opp}",
                "is_home": is_home, "opponent": opp, "team": team,
                "minutes": 0.0 if out else 30.0,
                "points": 0.0 if out else float(10 + pid + g % 3),
                "rebounds": 0.0 if out else 5.0, "assists": 0.0 if out else 3.0,
                "threes_made": 0.0 if out else 1.0})
    return pd.DataFrame(rows)


class FeatureTests(unittest.TestCase):
    def test_future_games_cannot_change_a_games_features(self):
        logs = make_logs()
        base = fp.build_features(logs)
        tampered = logs.copy()
        later = tampered["game_id"] >= "g13"
        tampered.loc[later & (tampered["player_name"] == "P1"), "points"] = 999.0
        tampered.loc[later, "minutes"] = 48.0
        after = fp.build_features(tampered)
        cols = fp.feature_columns(base)
        for gid in ("g06", "g10", "g12"):       # games BEFORE the tampering
            a = base[(base["game_id"] == gid) & (base["player_name"] == "P1")][cols]
            b = after[(after["game_id"] == gid) & (after["player_name"] == "P1")][cols]
            pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))

    def test_a_games_own_result_is_not_in_its_features(self):
        logs = make_logs()
        a = fp.build_features(logs)
        changed = logs.copy()
        mask = (changed["game_id"] == "g10") & (changed["player_name"] == "P1")
        changed.loc[mask, "points"] = 999.0
        b = fp.build_features(changed)
        cols = fp.feature_columns(a)
        row = lambda t: t[(t["game_id"] == "g10") & (t["player_name"] == "P1")][cols]
        pd.testing.assert_frame_equal(row(a).reset_index(drop=True),
                                      row(b).reset_index(drop=True))

    def test_players_need_some_history(self):
        table = fp.build_features(make_logs())
        self.assertTrue((table["n_total"] >= fp.MIN_PRIOR_GAMES).all())
        self.assertNotIn("g02", set(table["game_id"]))

    def test_teammates_out_counts_rotation_players_who_sat(self):
        table = fp.build_features(make_logs())
        game = table[table["game_id"] == "g15"].set_index("player_name")
        self.assertEqual(game.loc["P1", "out_cnt"], 1)         # P2 (30 min guy) is out
        self.assertAlmostEqual(game.loc["P1", "out_min"], 30.0, places=1)
        self.assertEqual(game.loc["P3", "out_cnt"], 0)         # other team unaffected
        self.assertNotIn("P2", game.index)                     # a DNP is not predicted
        other = table[table["game_id"] == "g14"].set_index("player_name")
        self.assertEqual(other.loc["P1", "out_cnt"], 0)


class ConstModel:
    def predict(self, X):
        return np.full(len(X), 12.0)


class SlateTests(unittest.TestCase):
    def models(self):
        m = {s: {"model": ConstModel(), "alpha": 0.1} for s in fp.STATS}
        m["_columns"] = ["points_r5", "out_cnt"]
        return m

    def test_virtual_rows_mark_out_players_as_dnp(self):
        logs = make_logs()
        v = fp.virtual_rows("2026-03-01", [("AAA", "BBB")],
                            {n: t for n, (t, _) in PLAYERS.items()}, {"P2"}, logs)
        self.assertEqual(len(v), 4)
        self.assertEqual(float(v[v["player_name"] == "P2"]["minutes"].iloc[0]), 0.0)
        self.assertEqual(float(v[v["player_name"] == "P1"]["minutes"].iloc[0]), 1.0)
        self.assertEqual(v[v["player_name"] == "P3"]["matchup"].iloc[0], "BBB @ AAA")

    def test_players_without_history_are_skipped(self):
        v = fp.virtual_rows("2026-03-01", [("AAA", "BBB")], {"Nobody": "AAA"},
                            set(), make_logs())
        self.assertTrue(v.empty)

    def test_slate_projects_available_players_only(self):
        slate = fp.build_slate(
            "2026-03-01", games=[("AAA", "BBB")],
            roster={n: t for n, (t, _) in PLAYERS.items()}, out_names={"P2"},
            models=self.models(), logs=make_logs(), write=False)
        self.assertEqual(slate["mu"]["P1|points"], 12.0)
        self.assertNotIn("P2|points", slate["mu"])            # ruled out: no projection
        self.assertEqual(slate["meta"]["P1"]["out_cnt"], 1.0)  # but his absence is a feature
        self.assertEqual(slate["meta"]["P3"]["opp"], "AAA")

    def test_slate_probability_reads_the_saved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "slate.json"
            path.write_text(json.dumps({
                "date": "2026-03-01", "alpha": {"points": 0.1},
                "mu": {"P1|points": 20.0}, "meta": {}}))
            with mock.patch.object(fp, "_SLATE_PATH", path):
                fp._slate_cache.update(mtime=None, data=None)
                over_low = fp.slate_p_over("P1", "points", 15.5, "2026-03-01")
                over_high = fp.slate_p_over("P1", "points", 25.5, "2026-03-01")
                self.assertGreater(over_low, 0.5)
                self.assertLess(over_high, 0.5)
                self.assertGreater(over_low, over_high)
                # wrong day, unknown player, combo stat: nothing to say
                self.assertIsNone(fp.slate_p_over("P1", "points", 15.5, "2026-03-02"))
                self.assertIsNone(fp.slate_p_over("Nobody", "points", 15.5, "2026-03-01"))
                self.assertIsNone(fp.slate_p_over("P1", "pra", 35.5, "2026-03-01"))
            fp._slate_cache.update(mtime=None, data=None)


if __name__ == "__main__":
    unittest.main()
