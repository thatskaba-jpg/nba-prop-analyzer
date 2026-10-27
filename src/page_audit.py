"""
page_audit.py — load the dashboard in many situations with FAKE data and report
any page error. Meant to be run from a COPY of the project, because loading the
dashboard writes to a few files in data/:

    rsync -a --exclude .git --exclude .venv --exclude __pycache__ ./ /tmp/bot_copy/
    cd /tmp/bot_copy && /path/to/.venv/bin/python src/page_audit.py

Scenarios: normal slate, no lines, network down, live/final games, every
checkbox ticked, the lineup button (working / empty feed / down), the update
button (stubbed), the parlay controls, every model profile.
"""
import contextlib
import sys
import time
import traceback
import warnings
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore")

import collect.balldontlie_client as bc  # noqa: E402
import collect.espn_api as ea  # noqa: E402
import collect.goat_data as gd  # noqa: E402
import odds.balldontlie_props as bp  # noqa: E402
import odds.game_lines as gl  # noqa: E402
import streamlit as st  # noqa: E402
import update_runner  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")
PAIRS = [("SAS", "HOU"), ("BOS", "NYK"), ("LAL", "LAC"), ("GSW", "MEM"),
         ("MIA", "DET"), ("PHI", "CLE")]


def _mk(h, a, sp, mlh, mla):
    return {h: dict(total=225.5, spread=sp, moneyline=mlh, opp=a, is_home=True,
                    matchup=f"{a} @ {h}", source="t"),
            a: dict(total=225.5, spread=(None if sp is None else -sp), moneyline=mla,
                    opp=h, is_home=False, matchup=f"{a} @ {h}", source="t")}


LINES = {}
for h, a, sp, ml, ma in [("SAS", "HOU", -3.75, -160, 135), ("BOS", "NYK", -6.5, -270, 220),
                         ("LAL", "LAC", -9.5, -390, 300), ("GSW", "MEM", -5.0, -210, 175),
                         ("MIA", "DET", 0, -110, -110), ("PHI", "CLE", None, None, None)]:
    LINES.update(_mk(h, a, sp, ml, ma))


def _games(status):
    return {"data": [{"id": i, "status": status, "datetime": "2099-01-01T23:%02d:00Z" % (10 + i),
                      "home_team": {"abbreviation": h}, "visitor_team": {"abbreviation": a},
                      "home_team_score": 100, "visitor_team_score": 90}
                     for i, (h, a) in enumerate(PAIRS)]}


def _row(team, fn, ln, pos, starter):
    return {"team": {"abbreviation": team}, "starter": starter, "position": pos,
            "player": {"first_name": fn, "last_name": ln}}


@contextlib.contextmanager
def env(lines=LINES, status="2099-01-01T23:30:00Z", net_fail=False, lineups_ok=True):
    def fake_get(path, params=None):
        if net_fail:
            raise bc.BallDontLieError("429 rate limited")
        return _games(status) if path.startswith("games") else {"data": []}

    def fake_all(path, params):
        if net_fail:
            raise RuntimeError("down")
        return _games(status)["data"]

    def lineups(gid):
        if net_fail:
            raise RuntimeError("down")
        return ([_row("SAS", "Victor", "Wembanyama", "C", True),
                 _row("HOU", "Alperen", "Sengun", "C", True)] if lineups_ok else [])
    with mock.patch.object(gl, "build_game_lines_map", lambda d=None: lines), \
            mock.patch.object(bc, "_get", fake_get), mock.patch.object(gd, "lineups", lineups), \
            mock.patch.object(bp, "_get_all", fake_all), \
            mock.patch.object(update_runner.UpdateRunner, "start", lambda self: True), \
            mock.patch.object(ea, "scoreboard",
                              lambda d, timeout=8: (_ for _ in ()).throw(OSError("blocked"))):
        yield


RESULTS = []


def scenario(name, fn=None, **kw):
    st.cache_data.clear()
    st.cache_resource.clear()
    t = time.time()
    try:
        with env(**kw):
            at = AppTest.from_file(APP, default_timeout=300)
            at.run()
            if fn:
                fn(at)
            errs = [str(e.value)[:200] for e in at.exception[:2]]
        RESULTS.append((name, errs, time.time() - t))
    except Exception:
        RESULTS.append((name, [traceback.format_exc()[-300:]], time.time() - t))


def tick_all(at):
    for c in at.checkbox:
        if any(k in str(c.key) for k in ("roster_full", "gl_more", "load_ml_board", "parlay_nochalk", "build_scorecard")):
            c.check()
    at.run()


def click(key):
    def f(at):
        for b in at.button:
            if b.key == key:
                b.click()
        at.run()
    return f


def open_each_game(at):
    """Open the breakdown for every game in turn, ticking every box."""
    sel = [x for x in at.selectbox if str(x.key).startswith("gl_sel_")]
    if not sel:
        raise AssertionError("no game selector on the page")
    for opt in sel[0].options[1:]:
        sel[0].set_value(opt)
        at.run()
        for c in at.checkbox:
            if any(k in str(c.key) for k in ("roster_full", "gl_more")):
                c.check()
        at.run()
        if not [m for m in at.markdown if "🏀" in m.value]:
            raise AssertionError(f"no card rendered for {opt}")


def analyze_stats(at):
    """Switch the Analyze tab through several stats (points, steals, threes...)."""
    sb = [x for x in at.selectbox if x.label == "Stat"]
    if not sb:
        raise AssertionError("no Stat selector on the Analyze tab")
    for key in ("rebounds", "assists", "threes_made", "steals", "points"):
        sb[0].set_value(key)             # raw stat key (the labels are formatted)
        at.run()
        if at.exception:
            raise AssertionError(f"Analyze failed for {key}: {at.exception[0].value}")
    if not [t for t in at.tabs if t.label in ("Factors", "Matchup")]:
        raise AssertionError("Analyze result tabs missing")


def parlay(at):
    got = [m.value for m in at.markdown if "-leg parlay" in m.value]
    if not got:
        raise AssertionError("no parlay summary rendered")
    for r in at.radio:
        if r.label == "Legs":
            for n in (2, 4, 5):
                r.set_value(n)
                at.run()


def profiles(at):
    for r in at.radio:
        if r.label == "Model profile":
            for opt in r.options:
                r.set_value(opt)
                at.run()


def main():
    scenario("default page")
    scenario("analyze: several stats", analyze_stats)
    scenario("parlay controls", parlay)
    scenario("open every game breakdown", open_each_game)
    scenario("open every game (all final)", open_each_game, status="Final")
    scenario("no lines at all", lines={})
    scenario("network fully down", net_fail=True)
    scenario("games live/final", status="Final")
    scenario("games live (Q2)", status="2nd Qtr 5:00")
    scenario("tick roster+details+parlay filter", tick_all)
    scenario("scorecard with finished games", tick_all, status="Final")
    scenario("check lineups button", click("check_all_lineups"))
    scenario("check lineups (feed empty)", click("check_all_lineups"), lineups_ok=False)
    scenario("check lineups, net down", click("check_all_lineups"), net_fail=True)
    scenario("update data button", click("update_data_btn"))
    scenario("each model profile", profiles)
    bad = 0
    for name, errs, dt in RESULTS:
        print(("OK   " if not errs else "FAIL ") + f"{name:36s} {dt:5.1f}s")
        for e in errs:
            bad += 1
            print("      ", e)
    print(f"\n{len(RESULTS) - sum(1 for _, e, _ in RESULTS if e)}/{len(RESULTS)} scenarios clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
