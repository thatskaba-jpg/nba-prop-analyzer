"""
profile_scoreboard.py — day-by-day success tracking for the Game Lines
(ML/spread) model profiles.

Each night's finished games are graded against every profile's straight-up
pick, and the running record (correct / total) accumulates in a small JSON file
committed to the repo — so it SURVIVES redeploys (unlike the ephemeral DB on
Streamlit Cloud) and builds up over the season.

Honest design:
  - Picks are the profile's projected winner from the SAME blended ratings the
    live tab uses. Results are reconstructed from player_game_logs (sum of each
    side's points per game_id), so we grade against what actually happened.
  - We grade a (profile, game) exactly once — keyed by game_id — so re-running
    on a day already scored can't double-count.
  - Straight-up (winner) only. ATS needs the historical closing spread, which we
    don't store per game; adding that later would extend this cleanly.
"""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from models.game_model import project_game, GAME_PROFILES  # noqa: E402

_SCORE_PATH = Path(__file__).resolve().parents[2] / "data" / \
    "profile_scoreboard.json"


def _roster_strength(season):
    """{team: {off, def}} from each team's CURRENT roster (mirrors the live
    tab's early-season logic), so the trained-model grade uses the same inputs
    the cards show. {} on error."""
    try:
        import numpy as np
        from collect.goat_data import active_players, season_average
        from concurrent.futures import ThreadPoolExecutor

        def _minf(m):
            s = str(m or "")
            if ":" in s:
                a, b = s.split(":", 1)
                return float(int(a)) + float(int(b)) / 60.0
            return float(s or 0)
        ap = active_players()

        def _fetch(p):
            sa = season_average(p["id"], season)
            if not sa or not sa.get("games_played"):
                sa = season_average(p["id"], season - 1)
            return (p.get("team"), sa) if sa and sa.get("games_played") else None
        with ThreadPoolExecutor(max_workers=16) as ex:
            rows = [r for r in ex.map(_fetch, ap) if r]
        byteam = {}
        for team, sa in rows:
            byteam.setdefault(team, []).append(sa)
        out = {}
        for team, lst in byteam.items():
            sc = sorted([(float(a.get("pts") or 0), _minf(a.get("min")))
                         for a in lst], key=lambda x: x[1], reverse=True)
            rot = [s for s in sc if s[1] >= 10][:10]
            off = max(95.0, min(128.0, sum(p for p, _ in rot)))
            stk = np.mean([float(a.get("stl") or 0) + float(a.get("blk") or 0)
                           for a in lst])
            reb = np.mean([float(a.get("reb") or 0) for a in lst])
            deff = max(104.0, min(120.0,
                                  113.0 - (stk - 1.3) * 4.0 - (reb - 4.0) * 1.2))
            out[team] = {"off": off, "def": deff, "pace": 100.0}
        return out
    except Exception:
        return {}


def _blend_ratings(season):
    """Blend this + last season team ratings (mirrors the dashboard)."""
    from collect.goat_data import team_ratings_map
    cur = team_ratings_map(season) or {}
    prev = team_ratings_map(season - 1) or {}
    if not prev:
        return cur
    if not cur:
        return prev
    out = {}
    for abbr, c in cur.items():
        p = prev.get(abbr)
        if not p:
            out[abbr] = c
            continue
        n = c.get("games") or 5
        w = max(0.0, min(1.0, n / 30.0))
        b = dict(c)
        for f in ("off", "def", "pace"):
            if c.get(f) is not None and p.get(f) is not None:
                b[f] = round(w * c[f] + (1 - w) * p[f], 2)
        out[abbr] = b
    return out


def _finished_games(season):
    """Reconstruct finished games from logs:
    [(game_id, date, home, away, home_pts, away_pts), ...]."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT game_id, game_date,
               MAX(CASE WHEN is_home=1 THEN matchup END) home_mu,
               SUM(CASE WHEN is_home=1 THEN points ELSE 0 END) home_pts,
               SUM(CASE WHEN is_home=0 THEN points ELSE 0 END) away_pts
        FROM player_game_logs WHERE season = ?
        GROUP BY game_id
        """, (season,)).fetchall()
    conn.close()
    out = []
    for r in rows:
        mu = r["home_mu"] or ""
        parts = mu.replace(" vs ", " @ ").split(" @ ")
        if len(parts) != 2:
            continue
        home, away = parts[0].strip(), parts[1].strip()
        hp, ap = r["home_pts"], r["away_pts"]
        if not home or not away or hp is None or ap is None or (hp + ap) == 0:
            continue
        out.append((r["game_id"], r["game_date"], home, away, hp, ap))
    return out


def _finished_games_live(days_back=14):
    """Finished games from the LIVE balldontlie feed over the last `days_back`
    days: [(game_id, date, home, away, home_pts, away_pts), ...]. This updates
    the SAME NIGHT a game goes final — no dependency on the DB log ingestion /
    re-upload. Falls back to [] on any API hiccup."""
    try:
        from collect.balldontlie_client import _get
        from datetime import date, timedelta
    except Exception:
        return []
    try:
        from database import nba_today
        end = date.fromisoformat(nba_today())
    except Exception:
        end = date.today()
    start = end - timedelta(days=days_back)
    out = []
    cursor = None
    try:
        while True:
            params = {"start_date": start.isoformat(),
                      "end_date": end.isoformat(), "per_page": 100}
            if cursor:
                params["cursor"] = cursor
            payload = _get("games", params)
            for g in payload.get("data", []):
                if str(g.get("status") or "").strip().lower()[:5] != "final":
                    continue
                h = (g.get("home_team") or {}).get("abbreviation")
                a = (g.get("visitor_team") or {}).get("abbreviation")
                hp = g.get("home_team_score")
                ap = g.get("visitor_team_score")
                if not (h and a) or hp is None or ap is None or (hp + ap) == 0:
                    continue
                out.append((g.get("id"), (g.get("date") or "")[:10], h, a,
                            int(hp), int(ap)))
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception:
        return out
    return out


def compute_live_scoreboard(season=2026, days_back=30):
    """Grade every profile straight from the LIVE feed, IN MEMORY — no file I/O.
    Returns [(profile, correct, total, pct), ...] sorted best-first. This always
    works even on a read-only filesystem (Streamlit Cloud), so the display never
    depends on a successful JSON write."""
    ratings = _blend_ratings(season)
    games = _finished_games_live(days_back)
    tally = {}
    for gid, gdate, home, away, hp, ap in games:
        home_won = hp > ap
        for pname, prof in GAME_PROFILES.items():
            proj = project_game(home, away, ratings, prof)
            if not proj:
                continue
            rec = tally.setdefault(pname, [0, 0])  # [correct, total]
            rec[1] += 1
            if (proj["exp_margin"] > 0) == home_won:
                rec[0] += 1
    rows = [(n, c, t, (c / t if t else 0.0)) for n, (c, t) in tally.items()]
    rows.sort(key=lambda x: (-x[3], -x[2]))
    # Best-effort persist for longer history; never let a write break the view.
    try:
        update_scoreboard(season)
    except Exception:
        pass
    return rows


def _load():
    if _SCORE_PATH.exists():
        try:
            return json.loads(_SCORE_PATH.read_text())
        except Exception:
            pass
    return {"profiles": {}, "graded_game_ids": []}


def _save(data):
    _SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SCORE_PATH.write_text(json.dumps(data, indent=2))


def update_scoreboard(season=2026, use_live=True):
    """Grade any not-yet-scored finished games against every profile and persist.
    Returns the updated scoreboard dict. Safe to call repeatedly.

    Grades from the LIVE scores feed by default (updates the same night a game
    goes final); falls back to the DB game logs if the feed returns nothing."""
    data = _load()
    done = set(data.get("graded_game_ids", []))
    ratings = _blend_ratings(season)
    rost = _roster_strength(season)   # current-roster strength for the trained model
    try:
        from models.game_ml_model import predict_from_features
    except Exception:
        predict_from_features = None
    games = (_finished_games_live() if use_live else []) \
        or _finished_games(season)
    newly = 0
    for gid, gdate, home, away, hp, ap in games:
        if gid in done:
            continue
        home_won = hp > ap
        for pname, prof in GAME_PROFILES.items():
            proj = project_game(home, away, ratings, prof)
            if not proj:
                continue
            picked_home = proj["exp_margin"] > 0
            correct = (picked_home == home_won)
            rec = data["profiles"].setdefault(pname, {"correct": 0, "total": 0})
            rec["total"] += 1
            if correct:
                rec["correct"] += 1
        # ---- The ACTUAL current model (trained + roster strength) ----
        if predict_from_features and rost.get(home) and rost.get(away):
            _h = (rost[home]["off"], rost[home]["def"], 0.5, 2)
            _a = (rost[away]["off"], rost[away]["def"], 0.5, 2)
            _tm = predict_from_features(_h, _a)
            if _tm:
                picked_home = _tm["margin"] > 0
                correct = (picked_home == home_won)
                rec = data["profiles"].setdefault(
                    "🤖 Trained model (live)", {"correct": 0, "total": 0})
                rec["total"] += 1
                if correct:
                    rec["correct"] += 1
        done.add(gid)
        newly += 1
    data["graded_game_ids"] = sorted(done, key=str)
    if newly:
        _save(data)
    return data


def get_scoreboard():
    """Read the current scoreboard without grading. Returns
    [(profile, correct, total, pct), ...] sorted by win% (most-tested breaks
    ties)."""
    data = _load()
    rows = []
    for pname, rec in data.get("profiles", {}).items():
        tot = rec.get("total", 0)
        pct = (rec["correct"] / tot) if tot else 0.0
        rows.append((pname, rec.get("correct", 0), tot, pct))
    rows.sort(key=lambda x: (-x[3], -x[2]))
    return rows


if __name__ == "__main__":
    d = update_scoreboard()
    print("Graded games:", len(d.get("graded_game_ids", [])))
    for pname, c, t, pct in get_scoreboard():
        print(f"  {pname:<28} {c}/{t}  {pct*100:.1f}%")
