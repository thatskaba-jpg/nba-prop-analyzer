"""
api/main.py — the BACKEND for the new front-end app.

This is the "brain" exposed as a small JSON API. It reuses the EXACT same
model, database, and odds code the Streamlit dashboard uses — nothing about the
analysis changes. The new front-end (a real web app in web/) calls these
endpoints for data, so the UI is no longer tied to Streamlit's layout.

Run locally:
    cd ~/sports-betting-bot
    source .venv/bin/activate
    uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

Endpoints:
    GET  /api/health                      -> {status, rows}
    GET  /api/players                     -> [{name, id}]   (>=20 games)
    GET  /api/stats                       -> [{key, label}] prop types
    POST /api/analyze                     -> projection + EV for one prop
    GET  /api/line-shopping?limit=&player=-> cross-book gaps / one player
    GET  /api/game-lines?date=            -> game lines + model projection
"""

import os
import sys
from pathlib import Path
from typing import Optional

# Make the existing src/ importable (same engine as the dashboard).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import get_connection
from features.features import load_player_history
from models.prop_model_config import build_config, analyze, FACTORS
from odds.ev_engine import evaluate_prop
from odds.market_anchor import anchor_to_market
from models.fitted_projection import slate_p_over

PROP_WORDS = {
    "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
    "threes_made": "3-Pointers Made", "steals": "Steals", "blocks": "Blocks",
    "turnovers": "Turnovers", "pra": "Pts + Reb + Ast", "pr": "Pts + Reb",
    "pa": "Pts + Ast", "ra": "Reb + Ast", "stocks": "Stocks (Stl + Blk)",
}

# Recommended factor weights (mirrors the dashboard's default set).
DEFAULT_FACTORS = {
    "player_last_n_avg": 30, "opponent_defense_by_position": 22,
    "opponent_team_rating": 13, "teammate_injuries": 11, "game_environment": 10,
    "rest_days": 7, "home_away_split": 4, "minutes_trend": 3,
}
# Keep only factors that actually exist in this build (defensive).
DEFAULT_FACTORS = {k: v for k, v in DEFAULT_FACTORS.items() if k in FACTORS}

app = FastAPI(title="NBA Prop Analyzer API")

# The front-end is served from this same origin, so cross-origin access is only
# needed for a locally-run dev page. Allow just localhost by default (a wildcard
# lets ANY website you visit call this API in your browser and spend your
# paid-API quota). Override with API_CORS_ORIGINS="https://a.example,https://b".
# There is NO authentication on this API: keep it bound to 127.0.0.1 and do not
# deploy it publicly without adding some.
_ORIGINS = [o.strip() for o in os.getenv(
    "API_CORS_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_ORIGINS, allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health():
    try:
        conn = get_connection()
        n = conn.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0]
        conn.close()
        return {"status": "ok", "rows": n}
    except Exception as e:
        raise HTTPException(500, f"db error: {e}")


@app.get("/api/players")
def players():
    conn = get_connection()
    rows = conn.execute(
        "SELECT player_name, MIN(player_id) pid, COUNT(*) c "
        "FROM player_game_logs GROUP BY player_name HAVING c >= 20 "
        "ORDER BY player_name").fetchall()
    conn.close()
    return [{"name": r["player_name"], "id": r["pid"]} for r in rows]


@app.get("/api/stats")
def stats():
    return [{"key": k, "label": v} for k, v in PROP_WORDS.items()]


class AnalyzeReq(BaseModel):
    player_id: int
    prop_type: str
    line: float
    opponent: Optional[str] = None
    is_home: bool = True
    odds_over: int = -110
    odds_under: int = -110


@app.post("/api/analyze")
def analyze_prop(req: AnalyzeReq):
    if req.prop_type not in PROP_WORDS:
        raise HTTPException(400, f"unknown prop_type '{req.prop_type}'")
    try:
        cfg = build_config("API", "weighted", DEFAULT_FACTORS)
    except Exception as e:
        raise HTTPException(500, f"config error: {e}")
    hist = load_player_history(req.player_id)
    if hist is None or len(hist) < 20:
        raise HTTPException(404, "not enough history for this player")
    import datetime as _dt
    last = hist.iloc[-1]["game_date"]
    try:
        cutoff = (_dt.date.fromisoformat(last) + _dt.timedelta(days=1)).isoformat()
    except Exception:
        cutoff = last
    ctx = {"opponent_abbr": req.opponent, "upcoming_is_home": req.is_home}
    res = analyze(cfg, req.player_id, req.prop_type, req.line, cutoff, hist, ctx)
    if res.get("projection") is None:
        raise HTTPException(422, "could not project this prop")
    # Value is judged on the market-anchored probability: the model alone hasn't
    # beaten the posted line, so it only nudges the line's own number.
    _conn = get_connection()
    _row = _conn.execute("SELECT player_name FROM player_game_logs "
                         "WHERE player_id = ? LIMIT 1", (req.player_id,)).fetchone()
    _conn.close()
    anch = anchor_to_market(res["p_over"], res["p_under"],
                            int(req.odds_over), int(req.odds_under),
                            p_over_raw=res.get("p_over_raw"),
                            p_over_fitted=slate_p_over(
                                _row["player_name"] if _row else "",
                                req.prop_type, req.line))
    ev = evaluate_prop(anch["p_over"], anch["p_under"],
                       int(req.odds_over), int(req.odds_under))
    lean = "OVER" if res["p_over"] >= 0.5 else "UNDER"

    # ---- WHY: turn each raw adjustment into a plain sentence ----
    import re as _re
    reasons = []
    for adj in (res.get("adjustments") or []):
        name = adj.split("]")[0].lstrip("[").split(" w=")[0] if "]" in adj else adj
        nums = _re.findall(r"\(([\d.]+) -> ([\d.]+)\)", adj)
        before, after = (nums[-1] if nums else (None, None))
        direction = ""
        if before and after:
            direction = ("nudges up" if float(after) > float(before)
                         else "nudges down" if float(after) < float(before)
                         else "no change")
        reasons.append({"factor": name, "effect": direction,
                        "from": float(before) if before else None,
                        "to": float(after) if after else None})

    # ---- HISTORY: last 10 games for this stat, vs the line ----
    stat_col = {"points": "points", "rebounds": "rebounds", "assists": "assists",
                "threes_made": "threes_made", "steals": "steals",
                "blocks": "blocks", "turnovers": "turnovers"}.get(req.prop_type)
    recent = []
    hit_count = 0
    if stat_col and stat_col in hist.columns:
        tail = hist.tail(10)
        for _, r in tail.iterrows():
            val = r.get(stat_col)
            if val is None:
                continue
            over = val > req.line
            if over:
                hit_count += 1
            recent.append({"date": r.get("game_date"), "opp": r.get("opponent"),
                           "value": float(val), "over": bool(over)})

    # ---- dollar EV for the leaned side ----
    ev_pick = ev.get("ev_over_pct") if lean == "OVER" else ev.get("ev_under_pct")
    odds_pick = req.odds_over if lean == "OVER" else req.odds_under
    return {
        "player_id": req.player_id, "prop_type": req.prop_type,
        "prop_label": PROP_WORDS[req.prop_type], "line": req.line,
        "projection": round(res["projection"], 1),
        "base_projection": res.get("base_projection"),
        "p_over": res["p_over"], "p_under": res["p_under"],
        "lean": lean,
        "win_pct": round((res["p_over"] if lean == "OVER" else res["p_under"]) * 100),
        "confidence": res.get("confidence", ""),
        "ev_over_pct": ev.get("ev_over_pct"), "ev_under_pct": ev.get("ev_under_pct"),
        "ev_pick_pct": ev_pick, "odds_pick": odds_pick,
        "is_value": ev.get("is_value", False),
        "recommended_side": ev.get("recommended_side"),
        "anchored": anch["anchored"],
        "p_over_anchored": anch["p_over"] if anch["anchored"] else None,
        "reasons": reasons,
        "recent": recent,
        "recent_hits": hit_count, "recent_n": len(recent),
    }


def _hit_rate(hist, stat_col, line, n):
    """Over hit-rate for the last n games of a stat vs a line."""
    if stat_col not in hist.columns:
        return None
    tail = hist.tail(n)
    vals = [v for v in tail[stat_col].tolist() if v is not None]
    if not vals:
        return None
    overs = sum(1 for v in vals if v > line)
    return {"hits": overs, "n": len(vals), "pct": round(overs / len(vals) * 100)}


def _stars(ev_pct):
    """0-5 bet rating from the leaned side's EV% (bigger edge = more stars)."""
    if ev_pct is None:
        return 0
    e = abs(ev_pct)
    return 5 if e >= 12 else 4 if e >= 8 else 3 if e >= 5 else 2 if e >= 2 else 1 if e > 0 else 0


@app.get("/api/player-card")
def player_card(player_id: int, prop_type: str, line: float):
    """BettingPros-style single-prop analysis: projection, EV, star rating, and
    multi-window hit-rates (L5/L10/L15/season) for one player + prop."""
    if prop_type not in PROP_WORDS:
        raise HTTPException(400, f"unknown prop_type '{prop_type}'")
    hist = load_player_history(player_id)
    if hist is None or len(hist) < 10:
        raise HTTPException(404, "not enough history")
    # Reuse the analyze endpoint's core via a direct call.
    req = AnalyzeReq(player_id=player_id, prop_type=prop_type, line=line)
    res = analyze_prop(req)
    stat_col = {"points": "points", "rebounds": "rebounds", "assists": "assists",
                "threes_made": "threes_made", "steals": "steals",
                "blocks": "blocks", "turnovers": "turnovers"}.get(prop_type)
    windows = {}
    if stat_col:
        for lbl, n in (("L5", 5), ("L10", 10), ("L15", 15), ("L25", 25)):
            hr = _hit_rate(hist, stat_col, line, n)
            if hr:
                windows[lbl] = hr
    res["windows"] = windows
    res["stars"] = _stars(res.get("ev_pick_pct"))
    return res


@app.get("/api/line-shopping")
def line_shopping(limit: int = 10, player: Optional[str] = None):
    from odds.line_shopping import biggest_gaps
    gaps = biggest_gaps(500)
    if player:
        gaps = [g for g in gaps if g["player"].lower() == player.lower()]
    else:
        gaps = gaps[:limit]
    # Tag prop labels for display.
    for g in gaps:
        g["prop_label"] = PROP_WORDS.get(g["prop_type"], g["prop_type"])
    return gaps


@app.get("/api/line-shopping/players")
def line_shopping_players():
    """Just the player names that have cross-book props today (for a picker)."""
    from odds.line_shopping import biggest_gaps
    return sorted({g["player"] for g in biggest_gaps(500)})


@app.get("/api/game-lines")
def game_lines(date: Optional[str] = None):
    from odds.game_lines import build_game_lines_map
    from models.game_model import project_game, cover_probability
    from collect.goat_data import team_ratings_map
    from database import nba_season_start_year, nba_today
    date = date or nba_today()          # US-Eastern slate, not the server's UTC day
    lines = build_game_lines_map(date)
    ratings = team_ratings_map(nba_season_start_year())
    out = []
    seen = set()
    for team, g in lines.items():
        mu = g.get("matchup")
        if not mu or mu in seen:
            continue
        seen.add(mu)
        try:
            away, home = [t.strip() for t in mu.split("@")]
        except ValueError:
            continue
        gh = lines.get(home) or {}
        proj = project_game(home, away, ratings) if ratings else None
        row = {"matchup": mu, "home": home, "away": away,
               "total": gh.get("total"), "spread": gh.get("spread"),
               "moneyline": gh.get("moneyline")}
        if proj:
            row["proj_margin"] = round(proj["exp_margin"], 1)
            row["proj_total"] = round(proj["exp_total"])
            row["p_home_win"] = round(proj["p_home_win"] * 100)
        out.append(row)
    return out


# Serve the built front-end (web/) at the root, if present. In production the
# API and the page are one origin; in dev you can open web/index.html directly.
_WEB = ROOT / "web"
if _WEB.exists():
    app.mount("/", StaticFiles(directory=str(_WEB), html=True), name="web")
