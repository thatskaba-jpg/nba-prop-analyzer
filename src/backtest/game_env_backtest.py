"""
game_env_backtest.py — does the game_environment factor help on real lines?

Compares the Recommended config WITH vs WITHOUT game_environment, graded against
the same stored real opening player-prop lines. Historical game totals/spreads
come from balldontlie's opening game-odds endpoint, fetched ONCE per distinct
game date (not per prop) and resolved to teams via that date's games — so the
run stays fast.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database import get_connection  # noqa: E402
from features.features import load_player_history  # noqa: E402
from models.prop_model_config import analyze, build_config  # noqa: E402
from collect.goat_data import team_ratings_map  # noqa: E402
from collect.balldontlie_client import _get, get_all  # noqa: E402

BREAK_EVEN = 0.524

CFG_BASE = {"player_last_n_avg": 30, "opponent_defense_by_position": 22,
            "opponent_team_rating": 13, "teammate_injuries": 11,
            "rest_days": 7, "home_away_split": 4, "minutes_trend": 13}
CFG_ENV = {"player_last_n_avg": 30, "opponent_defense_by_position": 22,
           "opponent_team_rating": 12, "teammate_injuries": 11,
           "game_environment": 10, "rest_days": 7, "home_away_split": 4,
           "minutes_trend": 4}


def _num(v):
    try:
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


_date_cache = {}


def game_lines_for_date(date):
    """{team_abbr: {total, spread}} from opening game odds for a date, cached."""
    if date in _date_cache:
        return _date_cache[date]
    out = {}
    try:
        odds = get_all("nba/v2/odds/opening", {"dates[]": date})
        games = get_all("games", {"dates[]": date, "per_page": 100})
    except Exception:
        _date_cache[date] = out
        return out
    teams = {g["id"]: ((g.get("home_team") or {}).get("abbreviation"),
                       (g.get("visitor_team") or {}).get("abbreviation"))
             for g in games}
    agg = {}
    for r in odds:
        gid = r["game_id"]
        a = agg.setdefault(gid, {"t": [], "sh": [], "sa": []})
        a["t"].append(_num(r.get("total_value")))
        a["sh"].append(_num(r.get("spread_home_value")))
        a["sa"].append(_num(r.get("spread_away_value")))
    for gid, a in agg.items():
        home, away = teams.get(gid, (None, None))
        if not home:
            continue
        import statistics as st
        def med(x):
            x = [v for v in x if v is not None]
            return st.median(x) if x else None
        out[home] = {"total": med(a["t"]), "spread": med(a["sh"])}
        out[away] = {"total": med(a["t"]), "spread": med(a["sa"])}
    _date_cache[date] = out
    return out


def run(prop, limit=1500):
    conn = get_connection()
    rows = conn.execute(
        "SELECT player_id, game_id, game_date, prop_type, AVG(line) line, "
        "MAX(hit_over) hit_over FROM historical_props "
        "WHERE snapshot='opening' AND graded_at IS NOT NULL "
        "AND hit_over IS NOT NULL AND prop_type=? "
        "GROUP BY player_id, game_id, prop_type LIMIT ?", (prop, limit)).fetchall()
    conn.close()

    cfg_base = build_config("Base", "weighted", CFG_BASE)
    cfg_env = build_config("Env", "weighted", CFG_ENV)
    tr = team_ratings_map(2025)
    hist_cache = {}
    team_of = {}
    # team per player from latest game
    cj = get_connection()
    for r2 in cj.execute("SELECT player_id, matchup FROM player_game_logs l "
                         "WHERE game_date=(SELECT MAX(game_date) FROM "
                         "player_game_logs WHERE player_id=l.player_id)"):
        mu = r2["matchup"] or ""
        for sep in (" vs ", " @ "):
            if sep in mu:
                team_of[r2["player_id"]] = mu.split(sep)[0].strip()
                break
    cj.close()

    tally = {"base": [0, 0], "env": [0, 0], "moved": 0}
    for r in rows:
        pid = r["player_id"]
        hist = hist_cache.get(pid)
        if hist is None:
            hist = hist_cache[pid] = load_player_history(pid)
        if hist.empty:
            continue
        srow = hist[hist["game_date"] == r["game_date"]]
        opp = srow.iloc[0]["opponent"] if len(srow) else None
        gl = game_lines_for_date(r["game_date"]).get(team_of.get(pid))
        went = 1 if r["hit_over"] == 1 else 0
        base_ctx = {"opponent_abbr": opp, "team_ratings_map": tr}
        rb = analyze(cfg_base, pid, prop, r["line"], r["game_date"], hist,
                     base_ctx, calibrate=False)
        re = analyze(cfg_env, pid, prop, r["line"], r["game_date"], hist,
                     dict(base_ctx, game_line=gl), calibrate=False)
        if rb.get("p_over") is None or re.get("p_over") is None:
            continue
        for tag, res in (("base", rb), ("env", re)):
            pick = res["p_over"] > 0.5
            ok = (pick and went) or (not pick and not went)
            tally[tag][0] += 1
            tally[tag][1] += 1 if ok else 0
        if gl:
            tally["moved"] += 1
    return tally


if __name__ == "__main__":
    props = sys.argv[1:] or ["points", "threes_made"]
    print(f"Game-environment factor backtest (break-even {BREAK_EVEN:.1%})\n")
    print(f"{'PROP':<14}{'WITHOUT':<18}{'WITH game_env':<18}{'games w/ line'}")
    print("-" * 64)
    for prop in props:
        t = run(prop)
        b_n, b_c = t["base"]
        e_n, e_c = t["env"]
        bp = f"{100*b_c/b_n:.1f}% (n={b_n})" if b_n else "—"
        ep = f"{100*e_c/e_n:.1f}% (n={e_n})" if e_n else "—"
        print(f"{prop:<14}{bp:<18}{ep:<18}{t['moved']}")
