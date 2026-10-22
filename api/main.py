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

# The front-end is served from the same origin in production, but allow all in
# dev so a locally-served page can call the API without CORS pain.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"],
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
    ev = evaluate_prop(res["p_over"], res["p_under"],
                       int(req.odds_over), int(req.odds_under))
    lean = "OVER" if res["p_over"] >= 0.5 else "UNDER"
    return {
        "player_id": req.player_id, "prop_type": req.prop_type,
        "prop_label": PROP_WORDS[req.prop_type], "line": req.line,
        "projection": round(res["projection"], 1),
        "p_over": res["p_over"], "p_under": res["p_under"],
        "lean": lean,
        "win_pct": round((res["p_over"] if lean == "OVER" else res["p_under"]) * 100),
        "confidence": res.get("confidence", ""),
        "ev_over_pct": ev.get("ev_over_pct"), "ev_under_pct": ev.get("ev_under_pct"),
        "is_value": ev.get("is_value", False),
        "recommended_side": ev.get("recommended_side"),
    }


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
    import datetime as _dt
    date = date or _dt.date.today().isoformat()
    lines = build_game_lines_map(date)
    ratings = team_ratings_map(2025)
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
