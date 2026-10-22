"""
roster_strength_db.py — team offense/defense from the LOCAL DB, no API.

The game model needs each team's strength (off/def) to project a game. Early in
a season, team box-score ratings don't exist yet AND don't reflect offseason
trades. So we build strength from the CURRENT ROSTER instead:

  - WHO is on the team  -> player_teams (official current team; trade-aware)
  - HOW GOOD they are   -> player_game_logs, this season blended with last

CRITICAL (trade handling): we join player_teams -> player_game_logs on
player_name, so a traded-in player is counted on his NEW team. When his
THIS-season sample is thin we fall back to his LAST-season STAT RATES — only
the numbers fall back, never the team. (LeBron traded to PHI shows up on PHI
with his old scoring, not on last season's team.)

Blend weight ramps with games played this season (w = n/(n+K), K=9 -> 50/50 at
~9 games), so current data takes over as the season fills in.

Zero network: one ~90ms SQL query for all 30 teams.
"""

import sys
import datetime as _d
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import get_connection  # noqa: E402

_K = 9.0
_OFF_LO, _OFF_HI = 108.0, 122.0   # realistic NBA team-points spread (rescaled)
_DEF_LO, _DEF_HI = 104.0, 120.0


def _cur_season_year(today=None):
    t = today or _d.date.today()
    return t.year if t.month >= 10 else t.year - 1


def team_strength_map(season=None):
    """{team: {'off': pts, 'def': pts, 'players': n}} for all teams, from the
    local DB. Current roster (player_teams) x blended this/last-season scoring.
    {} only if the DB is unreadable."""
    season = season or _cur_season_year()
    prev = season - 1
    try:
        conn = get_connection()
    except Exception:
        return {}
    try:
        rows = conn.execute(
            """
            SELECT pt.team AS team, pt.player_name AS name,
                   AVG(CASE WHEN l.season = ? THEN l.points END)   AS pts_cur,
                   AVG(CASE WHEN l.season = ? THEN l.points END)   AS pts_prev,
                   AVG(CASE WHEN l.season = ? THEN l.minutes END)  AS min_cur,
                   AVG(CASE WHEN l.season = ? THEN l.minutes END)  AS min_prev,
                   AVG(l.steals)   AS stl,
                   AVG(l.blocks)   AS blk,
                   AVG(l.rebounds) AS reb,
                   SUM(CASE WHEN l.season = ? THEN 1 ELSE 0 END)   AS g_cur
            FROM player_teams pt
            JOIN player_game_logs l ON l.player_name = pt.player_name
            WHERE l.season IN (?, ?) AND l.minutes > 0
            GROUP BY pt.team, pt.player_name
            """,
            (season, prev, season, prev, season, season, prev),
        ).fetchall()
    except Exception:
        conn.close()
        return {}
    conn.close()

    by_team = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)

    out = {}
    for team, players in by_team.items():
        scored, stks, rebs = [], [], []
        for p in players:
            n = p["g_cur"] or 0
            w = n / (n + _K)                       # this-season weight
            pc, pp = p["pts_cur"], p["pts_prev"]
            mc, mp = p["min_cur"], p["min_prev"]
            if pc is None and pp is None:
                continue
            # Stats fall back to last season; the TEAM never does.
            pts = (w * (pc if pc is not None else pp)
                   + (1 - w) * (pp if pp is not None else pc))
            mins = (w * (mc if mc is not None else (mp or 0))
                    + (1 - w) * (mp if mp is not None else (mc or 0)))
            scored.append((pts, mins))
            if p["stl"] is not None:
                stks.append((p["stl"] or 0) + (p["blk"] or 0))
            if p["reb"] is not None:
                rebs.append(p["reb"] or 0)
        if not scored:
            continue
        rotation = sorted([s for s in scored if s[1] >= 10],
                          key=lambda x: x[1], reverse=True)[:10]
        if not rotation:
            rotation = sorted(scored, key=lambda x: x[1], reverse=True)[:8]
        raw_off = sum(p for p, _ in rotation)   # rescaled across the league below
        stk = (sum(stks) / len(stks)) if stks else 1.3
        reb = (sum(rebs) / len(rebs)) if rebs else 4.0
        deff = max(_DEF_LO, min(_DEF_HI,
                               113.0 - (stk - 1.3) * 4.0 - (reb - 4.0) * 1.2))
        out[team] = {"off_raw": raw_off, "def": round(deff, 1),
                     "pace": 100.0, "players": len(scored)}

    # Rescale raw rotation sums (which overshoot real team totals because the
    # top-10 minutes exceed one game's 240) LINEARLY into a realistic team-
    # points band, preserving the league's spread so strong/weak teams stay
    # distinct instead of clipping together at a ceiling.
    if out:
        raws = [v["off_raw"] for v in out.values()]
        lo_r, hi_r = min(raws), max(raws)
        span = (hi_r - lo_r) or 1.0
        for v in out.values():
            frac = (v["off_raw"] - lo_r) / span          # 0..1 across the league
            v["off"] = round(_OFF_LO + frac * (_OFF_HI - _OFF_LO), 1)
            del v["off_raw"]
    return out


if __name__ == "__main__":
    import time
    t = time.time()
    m = team_strength_map()
    print(f"{len(m)} teams in {(time.time()-t)*1000:.0f}ms (zero API)")
    for ab in ("PHI", "CLE", "DEN", "OKC"):
        print(f"  {ab}: {m.get(ab)}")
