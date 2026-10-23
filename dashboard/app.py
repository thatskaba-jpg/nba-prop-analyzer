"""
app.py — Phase 7: the dashboard, written to be understood by ANYONE.

Run it from the project root (with the venv active):

    streamlit run dashboard/app.py

Plain-language goal: pick what matters to you, pick a player, and the page tells
you — in everyday words — how many points/rebounds/assists we expect, how likely
the Over or Under is, and (if you type in the real odds) whether it looks like a
good deal. A "Test It" button checks the model against real past games.

Wired to the real, verified engine (prop_model_config, baseline_model, ev_engine,
backtest). Nothing is made up: numbers come from stored games; odds only appear
when YOU type them in.
"""

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from database import get_connection  # noqa: E402
# Apply non-destructive schema migrations (ADD COLUMN) on startup so new stat
# columns (e.g. threes_att/fga/fta) exist before any read. Safe + idempotent.
try:
    from database import init_schema as _init_schema  # noqa: E402
    _init_schema()
except Exception:
    pass
from features.features import load_player_history  # noqa: E402
from features.opponent_defense import defense_factors  # noqa: E402
from models.prop_model_config import FACTORS, build_config, analyze  # noqa: E402
from models.milestone_props import milestone_probability  # noqa: E402
from models.prediction_tracker import (  # noqa: E402
    log_prediction, grade_pending, tracking_summary,
)
from odds.ev_engine import evaluate_prop  # noqa: E402
from odds.market_anchor import anchor_to_market  # noqa: E402
from models.fitted_projection import slate_p_over  # noqa: E402
try:
    from odds.sportsgameodds_client import player_prop_odds as _sgo_prop, has_key as _sgo_has, todays_player_keys as _sgo_today, all_player_props as _sgo_all  # noqa: E402
except Exception:
    def _sgo_prop(*a, **k):
        return None
    def _sgo_has():
        return False
    def _sgo_today():
        return set()
    def _sgo_all():
        return {}
try:
    from odds.balldontlie_props import all_player_props as _bdl_all, player_prop_odds as _bdl_prop, has_key as _bdl_has, todays_player_keys as _bdl_today, BDLPropsError as _BDLErr  # noqa: E402
except Exception:
    class _BDLErr(Exception):
        pass
    def _bdl_all():
        return {}
    def _bdl_prop(*a, **k):
        return None
    def _bdl_has():
        return False
    def _bdl_today():
        return set()


# --- Prop-odds router: balldontlie PRIMARY, SportsGameOdds BACKUP. ---
# balldontlie props are keyed by player_id (exact match to our DB) and come from
# the API we already rely on; SGO is the fallback if balldontlie has no key or
# returns nothing. Rate-limit errors propagate so the UI can say "wait a minute".
def sgo_has_key():
    return _bdl_has() or _sgo_has()


def sgo_all_props():
    if _bdl_has():
        try:
            bdl = _bdl_all()
            if bdl:
                return bdl
        except _BDLErr as e:
            if "rate limit" in str(e).lower():
                raise  # let the dashboard show the wait message
    # Fallback to SportsGameOdds.
    return _sgo_all() if _sgo_has() else {}


def sgo_prop_odds(name, prop_type):
    if _bdl_has():
        try:
            hit = _bdl_prop(name, prop_type)
            if hit:
                return hit
        except (_BDLErr, OSError):      # OSError covers a dropped connection/timeout
            pass
    try:
        return _sgo_prop(name, prop_type) if _sgo_has() else None
    except OSError:                     # no network: show "no odds", not a stack trace
        return None


def sgo_today():
    if _bdl_has():
        try:
            k = _bdl_today()
            if k:
                return k
        except (_BDLErr, OSError):
            pass
    try:
        return _sgo_today() if _sgo_has() else set()
    except OSError:
        return set()
try:
    from collect.goat_data import (  # noqa: E402
        season_average, advanced_stats, standings_map, team_ratings_map,
        player_advanced_avg,
    )
    from collect.goat_data import expected_threes, standings  # noqa: E402
except Exception:
    season_average = advanced_stats = standings_map = team_ratings_map = None
    player_advanced_avg = None
    expected_threes = None
    standings = None
try:
    from collect.fetch_injuries import current_injury  # noqa: E402
except Exception:
    def current_injury(_pid):
        return None
try:
    from collect.espn_player_bio import player_bio as espn_bio  # noqa: E402
except Exception:
    def espn_bio(_name):
        return None
from backtest.backtest import run_backtest  # noqa: E402

st.set_page_config(page_title="Bomb Vegas · Prop Analyzer", page_icon="💣",
                   layout="wide", initial_sidebar_state="collapsed")

# --- Styling: MODERN MONOCHROME — near-black, layered greys, crisp white
#     accent. Minimal, high-contrast, premium. No color, all contrast. ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

:root {
    --bg:#0c0c0d; --panel:#161617; --panel-2:#1d1d1f; --raise:#242427;
    --line:#2a2a2d; --line-2:#363639;
    --text:#f4f4f5; --muted:#9a9aa0; --faint:#6e6e73;
    --accent:#ffffff;            /* pure white accent — the only "color" */
    --accent-soft:rgba(255,255,255,.08);
    --up:#e6e6e6; --down:#8a8a8f;
}

/* ---- Base ---- */
html, body, [class*="css"] { font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; }
.stApp { background:
    radial-gradient(1100px 560px at 50% -15%, #1a1a1c 0%, transparent 60%),
    var(--bg); color:var(--text); }
.block-container { padding-top:1.2rem; padding-bottom:3rem; max-width:1180px; }

/* ---- Headings: clean, tight, thin rule ---- */
h1,h2,h3 { color:var(--text); font-weight:800; letter-spacing:-.02em; }
h2,h3 { font-size:1.05rem !important; border-bottom:1px solid var(--line);
    padding-bottom:9px; margin-top:.4rem; }

/* ---- Metric tiles: soft raised card, white number, gentle lift ---- */
div[data-testid="stMetric"] {
    background:linear-gradient(180deg,var(--panel-2),var(--panel));
    border:1px solid var(--line); border-radius:16px; padding:16px 18px;
    box-shadow:0 1px 0 rgba(255,255,255,.04) inset, 0 10px 30px -18px #000;
    transition:transform .14s ease, border-color .14s ease; }
div[data-testid="stMetric"]:hover { transform:translateY(-2px); border-color:var(--line-2); }
div[data-testid="stMetric"] label { color:var(--muted) !important; font-size:.72rem;
    text-transform:uppercase; letter-spacing:.09em; font-weight:700; }
div[data-testid="stMetricValue"] { font-size:1.9rem; font-weight:900;
    color:#fff; letter-spacing:-.03em; }

/* ---- Buttons (ALL: main, sidebar, form, download): white fill, DARK text,
   invert to outline on hover. The !important dark color stops any button from
   rendering white-on-white (invisible). ---- */
div.stButton > button, div.stDownloadButton > button,
section[data-testid="stSidebar"] button[kind],
div[data-testid="stForm"] button {
    background:var(--accent) !important; color:#0c0c0d !important;
    border:1px solid var(--accent) !important;
    border-radius:12px; font-weight:800; padding:.55rem 1.3rem; letter-spacing:-.01em;
    box-shadow:0 8px 24px -14px #fff; transition:all .14s ease; }
div.stButton > button *, div.stDownloadButton > button *,
section[data-testid="stSidebar"] button[kind] * {
    color:#0c0c0d !important; }
div.stButton > button:hover, div.stDownloadButton > button:hover,
section[data-testid="stSidebar"] button[kind]:hover {
    background:transparent !important; color:var(--accent) !important;
    box-shadow:none; }
div.stButton > button:hover *, section[data-testid="stSidebar"] button[kind]:hover * {
    color:var(--accent) !important; }
div.stButton > button:active { transform:translateY(1px); }

/* ---- Tabs: soft segmented control, white active ---- */
div[data-baseweb="tab-list"] { gap:4px; background:var(--panel); padding:5px;
    border:1px solid var(--line); border-radius:14px; }
button[data-baseweb="tab"] { font-size:.9rem; font-weight:700; border-radius:10px;
    padding:9px 16px; color:var(--muted); letter-spacing:-.01em; transition:all .12s ease; }
button[data-baseweb="tab"]:hover { color:var(--text); background:var(--panel-2); }
button[data-baseweb="tab"][aria-selected="true"] {
    background:var(--accent) !important; color:#0c0c0d !important; }
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] { display:none; }

/* ---- All radios as clean tappable pills (chip look, no radio circle) ---- */
div[role="radiogroup"] { gap:8px !important; flex-wrap:wrap !important; }
div[role="radiogroup"] label {
    background:#161617 !important; border:1px solid #2a2a2c !important;
    border-radius:10px !important; padding:9px 14px !important;
    margin:0 !important; cursor:pointer !important; transition:all .12s ease;
    display:flex !important; align-items:center !important;
    justify-content:center !important; text-align:center !important; }
div[role="radiogroup"] label:hover { border-color:#5a5a5e !important; }
div[role="radiogroup"] label > div:first-child { display:none !important; }
div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] p {
    color:#d4d4d8 !important; font-size:.86rem !important; margin:0 !important;
    white-space:nowrap !important; }
div[role="radiogroup"] label:has(input:checked) {
    background:#ffffff !important; border-color:#ffffff !important; }
div[role="radiogroup"] label:has(input:checked) p {
    color:#0c0c0d !important; font-weight:700 !important; }

/* Stat picker (a radio with MANY options, 5+): a uniform 4-column grid so
   every pill is equal width and the rows line up symmetrically. The 3-option
   importance pills and 2-option Venue stay inline. Scoped by option count
   because this Streamlit build (1.36) doesn't emit per-widget key classes. */
div[role="radiogroup"]:has(> label:nth-child(5)) {
    display:grid !important;
    grid-template-columns:repeat(4, 1fr) !important;
    gap:8px !important; }
div[role="radiogroup"]:has(> label:nth-child(5)) label { min-height:42px !important; }

/* ---- Inputs: recessed, white focus ---- */
div[data-baseweb="input"] input, div[data-baseweb="select"] > div,
.stNumberInput input, .stTextInput input {
    background:#0f0f10 !important; border-radius:11px !important;
    border:1px solid var(--line) !important; color:var(--text) !important; }
div[data-baseweb="input"]:focus-within, .stNumberInput div:focus-within,
div[data-baseweb="select"] > div:focus-within {
    border-color:var(--accent) !important;
    box-shadow:0 0 0 3px var(--accent-soft) !important; }

/* ---- Tables ---- */
div[data-testid="stDataFrame"] { border-radius:12px; overflow:hidden;
    border:1px solid var(--line); }

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
    background:linear-gradient(180deg,#121213,#0c0c0d); border-right:1px solid var(--line); }
section[data-testid="stSidebar"] h1 { font-size:1.15rem; letter-spacing:-.02em; }

/* ---- Expanders: soft card, brighter edge on hover ---- */
div[data-testid="stExpander"] { border:1px solid var(--line); border-radius:14px;
    background:var(--panel); overflow:hidden; transition:border-color .14s ease;
    margin-bottom:6px; }
div[data-testid="stExpander"]:hover { border-color:var(--line-2); }
/* Tighten the gap between stacked expanders (e.g. the Game Lines list) so the
   rows sit close together instead of floating far apart. */
div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stExpander"]) {
    margin-bottom:0 !important; }

/* ---- Multiselect / selected tags: dark pill, READABLE white text ----
   (the generic input rule was turning these white-on-white — invisible) ---- */
span[data-baseweb="tag"] {
    background:#2a2a2c !important; color:#f4f4f5 !important;
    border:1px solid var(--line-2) !important; border-radius:8px !important; }
span[data-baseweb="tag"] *,
span[data-baseweb="tag"] span, span[data-baseweb="tag"] div {
    background:transparent !important; color:#f4f4f5 !important; }
span[data-baseweb="tag"] svg { fill:#f4f4f5 !important; color:#f4f4f5 !important; }
/* the multiselect box itself */
div[data-baseweb="select"] div[data-baseweb="tag"] { font-weight:600; }

/* ---- Alerts ---- */
div[data-testid="stAlert"] { border-radius:12px; border:1px solid var(--line);
    background:var(--panel) !important; }

/* ---- Radio / slider / checkbox: monochrome ---- */
div[role="radiogroup"] label { font-weight:600; }
.stSlider [data-baseweb="slider"] div[role="slider"] { background:var(--accent); }
.stCheckbox [data-baseweb="checkbox"] span[aria-checked="true"] {
    background:var(--accent) !important; border-color:var(--accent) !important; }
/* dark checkmark so it shows on the white checked box (was white-on-white) */
.stCheckbox [data-baseweb="checkbox"] span[aria-checked="true"] svg,
.stCheckbox [data-baseweb="checkbox"] span[aria-checked="true"] svg * {
    fill:#0c0c0d !important; stroke:#0c0c0d !important; color:#0c0c0d !important; }
/* unchecked box: visible outline, not a white block */
.stCheckbox [data-baseweb="checkbox"] span[aria-checked="false"] {
    background:#0f0f10 !important; border:1px solid var(--line-2) !important; }

/* ---- Monochrome hero: glass panel, thin top highlight ---- */
.sb-hero {
    background:linear-gradient(180deg,var(--panel-2),var(--panel));
    border:1px solid var(--line); border-radius:20px; padding:22px 26px;
    margin-bottom:18px; position:relative; overflow:hidden;
    box-shadow:0 1px 0 rgba(255,255,255,.05) inset, 0 20px 50px -30px #000; }
.sb-hero::after { content:""; position:absolute; inset:0 0 auto 0; height:1px;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent); }
.sb-hero h1 { margin:0; font-size:1.95rem; font-weight:900; letter-spacing:-.03em; color:#fff; }
.sb-hero p { margin:.4rem 0 0; color:var(--muted); font-size:.95rem; max-width:640px;
    line-height:1.5; }
.sb-pill { display:inline-block; background:var(--accent-soft); color:var(--text);
    border:1px solid var(--line-2); font-weight:700; font-size:.68rem; letter-spacing:.1em;
    text-transform:uppercase; padding:3px 11px; border-radius:999px; margin-bottom:10px; }

/* ---- Hide Streamlit chrome so it reads as a standalone app ---- */
#MainMenu { visibility:hidden; }
/* Keep the header element present (its sidebar-toggle arrow lives inside it)
   but make it transparent so it doesn't show a bar — hiding the whole header
   previously also hid the ONLY control that reopens a collapsed sidebar. */
header[data-testid="stHeader"] { background:transparent !important; }
footer { display:none; }
div[data-testid="stToolbar"] { display:none; }
div[data-testid="stDecoration"] { display:none; }
.stDeployButton { display:none; }
/* The arrow that opens the sidebar (model editor) — always visible & on top. */
div[data-testid="stSidebarCollapsedControl"],
button[data-testid="stSidebarCollapseButton"],
button[data-testid="baseButton-headerNoPadding"] {
    display:flex !important; visibility:visible !important; opacity:1 !important;
    z-index:999999 !important; color:#f4f4f5 !important; }
div[data-testid="stSidebarCollapsedControl"] svg,
button[data-testid="stSidebarCollapseButton"] svg {
    fill:#f4f4f5 !important; color:#f4f4f5 !important; }
.block-container { padding-top:0.6rem !important; }
</style>
""", unsafe_allow_html=True)


# Everyday-word labels for things that otherwise sound technical.
PROP_WORDS = {
    "points": "Points", "rebounds": "Rebounds", "assists": "Assists",
    "threes_made": "3-Pointers Made",
    "threes_att": "3-Point Attempts",
    "fgm": "Field Goals Made",
    "fga": "Field-Goal Attempts",
    "ftm": "Free Throws Made",
    "fta": "Free-Throw Attempts",
    "steals": "Steals", "blocks": "Blocks", "turnovers": "Turnovers",
    "pra": "Pts + Reb + Ast (PRA)",
    "pr": "Pts + Reb", "pa": "Pts + Ast", "ra": "Reb + Ast",
    "stocks": "Stocks (Stl + Blk)",
}
# Friendly one-line descriptions for each factor (shown under its checkbox).
FACTOR_HELP = {
    "player_last_n_avg": "Hot or cold lately? Compares the last 5 games to the "
                         "player's season average and leans the projection "
                         "toward whichever way they're trending.",
    "opponent_defense": "How good is tonight's opponent at defense overall? A "
                        "weak defense nudges the projection up; a tough one "
                        "nudges it down.",
    "opponent_defense_by_position": "How well does the opponent defend THIS "
                                    "player's position? Sharper than overall "
                                    "defense — a team can lock up guards but get "
                                    "torched by bigs.",
    "opponent_team_rating": "Official team numbers: a weaker defense (gives up "
                            "more points) and a faster pace (more possessions) "
                            "both make a bigger night more likely.",
    "rest_days": "Tired or fresh? Played yesterday (back-to-back) nudges down; "
                 "a few days off nudges up.",
    "home_away_split": "Does the player put up better numbers at home or on the "
                       "road? Leans toward their split for tonight's venue.",
    "minutes_trend": "Is the player's role growing or shrinking? More minutes "
                     "lately means more chances to pile up stats (nudge up); "
                     "fewer means the opposite.",
    "player_vs_team": "How has the player done against this specific opponent "
                      "before? A small, weak signal — kept gentle on purpose.",
    "teammate_injuries": "Are the player's teammates hurt? If a teammate sits, "
                         "this player usually gets more shots and minutes, so "
                         "their line nudges up. This is game-day news that "
                         "sportsbooks are often slow to fully price in.",
    "game_environment": "Tonight's game line. A high total means a fast, high-"
                        "scoring game (everyone's numbers lean up); a low total "
                        "means a grind (lean down). A huge spread means likely "
                        "blowout — stars rest in the 4th, capping their stats.",
}


# Combo stats are summed from base columns; singles map straight through.
_HITRATE_COMBO = {
    "pra": ("points", "rebounds", "assists"),
    "pr": ("points", "rebounds"),
    "pa": ("points", "assists"),
    "ra": ("rebounds", "assists"),
    "stocks": ("steals", "blocks"),
}


def hit_rate_windows(hist, prop_type, line, windows=(5, 10, 15)):
    """How often the player went OVER `line` for this stat in their last N
    games. Returns [(N, hits, n, over_pct, avg), ...] — real games only, so a
    short career just reports fewer windows. None if the stat isn't countable
    from the game log."""
    if hist is None or len(hist) == 0:
        return []
    cols = _HITRATE_COMBO.get(prop_type, (prop_type,))
    if any(c not in hist.columns for c in cols):
        return []
    series = hist[list(cols)].sum(axis=1).dropna()
    if series.empty:
        return []
    out = []
    for n in windows:
        last = series.tail(n)
        if len(last) == 0:
            continue
        hits = int((last > line).sum())
        out.append((len(last), hits, hits / len(last), float(last.mean())))
    return out


@st.cache_data(show_spinner=False)
def player_list():
    conn = get_connection()
    # One row per NAME (players can have two ids across data sources). We pick
    # a representative id and sum games across ids so the floor counts the
    # player's whole career, not just one source. history loads by name anyway,
    # so any id for that name resolves the full timeline.
    #
    # Floor is LOW (3 games) so active players with short histories — rookies,
    # recent call-ups — still appear in the dropdown. The projection itself
    # guards thin samples (it labels them "low confidence — small sample" and
    # refuses to project with too few games), so a low floor can't produce a
    # fake-confident number; it just stops hiding real players who are playing.
    rows = conn.execute(
        "SELECT player_name, MIN(player_id) AS pid, COUNT(*) c "
        "FROM player_game_logs GROUP BY player_name HAVING c >= 3 "
        "ORDER BY player_name").fetchall()
    conn.close()
    return [(r["player_name"], r["pid"]) for r in rows]


@st.cache_data(show_spinner=False)
def defense_map(cutoff):
    return defense_factors(cutoff)


def defense_summary(cutoff, team):
    """Plain-English defense read for `team` from our OWN season data
    (opponent_defense: points allowed vs league average). Returns a dict with
    a label, a rank (1 = toughest D), and the points-vs-league wording, or None.

    Reliable — computed from the local 247k-game DB, no external API."""
    fmap = defense_map(cutoff)
    if not fmap or team not in fmap:
        return None
    # factor > 1 = allows MORE than average (soft D); < 1 = tough D.
    # Rank 1 = toughest (lowest factor).
    order = sorted(fmap, key=lambda t: fmap[t])
    rank = order.index(team) + 1
    n = len(order)
    factor = fmap[team]
    pct = (factor - 1.0) * 100
    if factor >= 1.03:
        tag = "soft defense — gives up more than average"
    elif factor <= 0.97:
        tag = "tough defense — stingy"
    else:
        tag = "average defense"
    return {"rank": rank, "n": n, "factor": factor, "pct": pct, "tag": tag}


@st.cache_data(show_spinner=False, ttl=900)
def cached_history(pid):
    """Player game history, cached for the session so the value scanner doesn't
    re-read the DB for the same player on every scan. Returns the DataFrame."""
    return load_player_history(pid)


@st.cache_data(show_spinner=False, ttl=3600)
def cached_four_factors(season):
    """Team Four Factors map (eFG/TOV/OREB), cached an hour. {} on error."""
    try:
        from collect.goat_data import team_four_factors_map
        return team_four_factors_map(season)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=120)
def cached_polymarket(home_abbr, away_abbr):
    """Polymarket crowd probability for a game, cached 2 min (prices move fast).
    None when no market / unavailable."""
    try:
        from odds.polymarket import game_probability
        return game_probability(home_abbr, away_abbr)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=600)
def cached_opening_odds(date_iso):
    """Opening game odds keyed by MATCHUP string ('AWAY @ HOME') for a date,
    cached 10 min. The game-lines dict has no game_id, so we resolve game_id ->
    matchup via the games feed and re-key. {} on error."""
    try:
        from odds.line_movement import opening_game_odds
        from collect.goat_data import _get
        by_gid = opening_game_odds(date_iso)
        if not by_gid:
            return {}
        # Map game_id -> "AWAY @ HOME" from the games feed.
        gid_to_matchup = {}
        try:
            r = _get("nba/v1/games", {"dates[]": date_iso, "per_page": 100})
            for g in r.get("data", []):
                h = (g.get("home_team") or {}).get("abbreviation")
                a = (g.get("visitor_team") or {}).get("abbreviation")
                if h and a:
                    gid_to_matchup[g.get("id")] = f"{a} @ {h}"
        except Exception:
            return {}
        return {gid_to_matchup[g]: v for g, v in by_gid.items()
                if g in gid_to_matchup}
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=600)
def cached_opening_props(game_id):
    """Opening prop lines {(pid,prop_type): line} for a game, cached 10 min."""
    try:
        from odds.line_movement import opening_props_for_game
        return opening_props_for_game(game_id)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=3600)
def cached_team_style(season, category, type_):
    """Team playtype/tracking stats {abbr: stats}, cached an hour. {} on error."""
    try:
        from collect.goat_data import team_style_map
        return team_style_map(season, category, type_)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=30)
def cached_live_tracker():
    """Live prop-tracking rows: join live box scores to current prop lines.
    Cached 30s (it's LIVE). [] when nothing is in progress or on error."""
    try:
        from odds.live_tracker import track
        from odds.balldontlie_props import all_player_props
        props = all_player_props()
        lines = {nk: {s: v.get("line") for s, v in sd.items()
                      if v.get("line") is not None}
                 for nk, sd in props.items()}
        return track(lines)
    except Exception:
        return []


@st.cache_data(show_spinner=False)
def team_abbrs():
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT opponent FROM player_game_logs "
        "WHERE opponent IS NOT NULL AND opponent != '' ORDER BY opponent"
    ).fetchall()
    conn.close()
    return [r["opponent"] for r in rows]


def _h2h_vs_opponent(hist, opp, stat, cutoff=None):
    """(avg, games) for how this player has done in `stat` vs opponent `opp`,
    using only games strictly BEFORE cutoff (leak-free) when cutoff is given.
    Returns (None, 0) when the column/opponent is missing or too few meetings.
    Feeds the player_vs_team factor, which was dormant because nothing computed
    this and (until the collector fix) opponent was never stored."""
    try:
        if hist is None or opp is None or stat not in hist.columns:
            return None, 0
        df = hist
        if "opponent" not in df.columns:
            return None, 0
        if cutoff is not None and "game_date" in df.columns:
            df = df[df["game_date"].astype(str) < str(cutoff)]
        vs = df[df["opponent"] == opp]
        n = int(len(vs))
        if n < 3:
            return None, n
        return float(vs[stat].mean()), n
    except Exception:
        return None, 0


@st.cache_data(show_spinner=False)
def cached_pos_defense_raw(stat, cutoff):
    """Raw stat-allowed-to-position + league rank per team, cached."""
    from features.positional_defense import positional_defense_raw
    if stat not in ("points", "rebounds", "assists"):
        return {}
    return positional_defense_raw(stat, cutoff)


@st.cache_data(show_spinner=False)
def cached_team_steals(cutoff):
    """Team steals generated + allowed per game with ranks, cached."""
    from features.positional_defense import team_steals
    return team_steals(cutoff)


@st.cache_data(show_spinner=False)
def positional_map(stat, cutoff):
    from features.positional_defense import positional_defense_factors
    if stat not in ("points", "rebounds", "assists"):
        return {}
    return positional_defense_factors(stat, cutoff)


@st.cache_data(show_spinner=False)
def player_group(pid, cutoff):
    from features.positional_defense import player_groups
    return player_groups(cutoff).get(pid)


@st.cache_data(show_spinner=False, ttl=3600)
def player_team(pid):
    """The player's current team abbreviation. Prefers the OFFICIAL roster
    (/players/active), which reflects real offseason TRADES the game log can't
    know yet (a traded player's last game is still under his OLD team). Falls
    back to the most-recent-game team only when the roster has no entry."""
    conn = get_connection()
    row = conn.execute(
        "SELECT player_name, matchup FROM player_game_logs WHERE player_id = ? "
        "ORDER BY game_date DESC LIMIT 1", (pid,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        from collect.fetch_rosters import official_team
        off = official_team(row["player_name"])
        if off:
            return off
    except Exception:
        pass
    m = (row["matchup"] or "").split(" ")
    return m[0] if m and m[0] else None


@st.cache_data(show_spinner=False, ttl=1800)
def next_opponent(team_abbr):
    """(opponent, is_home) for the team's next scheduled game, or None.

    Tries balldontlie's schedule first (GOAT tier), then falls back to ESPN's
    FREE public scoreboard so a dropped/rate-limited balldontlie call still
    resolves tonight's matchup."""
    if not team_abbr:
        return None
    # Primary: balldontlie.
    try:
        from collect.schedule import next_game_for_team
        hit = next_game_for_team(team_abbr)
        if hit:
            return hit
    except Exception:
        pass
    # Backup: ESPN public scoreboard (no key).
    try:
        from collect.schedule_espn import next_game_for_team as espn_next
        return espn_next(team_abbr)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def goat_standings(season):
    if standings_map is None:
        return {}
    try:
        return standings_map(season)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=3600)
def goat_season_avg(pid, season):
    if season_average is None:
        return {}
    try:
        return season_average(pid, season)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=3600)
def goat_team_ratings(season):
    """Team off/def/net ratings + pace, roster-strength FIRST.

    The balldontlie team-ratings API feed is unreliable early in a season
    (compressed/flat, missing teams). So every surface that asks for team
    ratings now gets the DB-first, trade-aware ROSTER strength (current roster x
    player advanced stats) overlaid on top of the API feed — the roster numbers
    win for any team they cover, the API feed fills the rest. Served from the
    12h disk store; empty dict only if nothing is available at all."""
    api = {}
    if team_ratings_map is not None:
        try:
            from collect.local_store import get_or_refresh
            api = get_or_refresh(f"team_ratings_{season}",
                                 lambda: team_ratings_map(season),
                                 max_age_hours=12) or {}
        except Exception:
            try:
                api = team_ratings_map(season) or {}
            except Exception:
                api = {}
    # Overlay roster-based strength (primary). One place, so prop factors, the
    # analyze tab and the game-lines tab all agree.
    try:
        from models.roster_strength_db import team_strength_map
        roster = team_strength_map(season) or {}
        return _blend_roster_api(roster, api)
    except Exception:
        return api


# Games-played handoff: how many API games before the API feed is trusted ~50%.
# Below this the roster-based strength dominates (early-season the API ratings
# are compressed/flat); past it the API's real game data takes over. K=8 means
# the API is already the majority source by ~8-10 games (a couple weeks), not
# dragging out across the whole season.
_RATINGS_HANDOFF_K = 8.0


def _blend_roster_api(roster, api):
    """Per-team blend of roster strength and the API ratings feed, weighted by
    how many games the API has logged for that team. Early season (few games)
    leans on roster strength; as games accumulate the API feed takes over.
    A team only in one source uses that source."""
    teams = set(roster) | set(api)
    out = {}
    for ab in teams:
        r = roster.get(ab)
        a = api.get(ab)
        if not r:
            out[ab] = dict(a); continue
        if not a or a.get("off") is None or a.get("def") is None:
            out[ab] = {"off": r["off"], "def": r["def"],
                       "pace": r.get("pace", 100.0)}
            continue
        n = a.get("games") or 0
        w_api = max(0.0, min(1.0, n / (n + _RATINGS_HANDOFF_K)))  # API weight
        b = dict(a)
        for f in ("off", "def"):
            b[f] = round(w_api * a[f] + (1 - w_api) * r[f], 1)
        b["pace"] = a.get("pace") or r.get("pace", 100.0)
        out[ab] = b
    return out


@st.cache_data(show_spinner=False, ttl=1800)
def cached_team_form(teams_tuple, cutoff, n=10):
    """{team: {recent_margin, rest_days}} for the game model — recent form +
    rest, reconstructed leak-free from local game logs (no API). Cached 30 min;
    tuple key so Streamlit can hash it. Empty on error (model falls back)."""
    try:
        from models.team_form import team_form_map
        return team_form_map([t for t in teams_tuple if t], cutoff, n)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=1800)
def recent_pace_map(teams_tuple, cutoff, n=10):
    """{team_abbr: recent_pace} for the given teams, from the per-game pace
    collector (team_game_pace). Leak-free: averages each team's last n games
    before `cutoff`. Empty for teams with no stored pace, so the rate model
    cleanly falls back to season pace. Cached 30 min; a tuple key so Streamlit
    can hash it."""
    try:
        from collect.fetch_team_pace import recent_team_pace, _table_ready
        if not _table_ready():
            return {}
        out = {}
        for t in teams_tuple:
            if not t:
                continue
            rp = recent_team_pace(t, cutoff, n)
            if rp:
                out[t.upper()] = rp
        return out
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=3600)
def blended_team_ratings(season):
    """Team ratings BLENDED across this season and last — the right call early
    in a season when this year's sample is tiny and unreliable on its own.

    Weight on THIS season ramps up with how many games it has: ~0 games -> lean
    almost entirely on last year; by ~20 games this season it's ~2/3 current.
    Blends off/def/pace; keeps this season's row shape (ranks). Falls back to
    whichever season actually has data."""
    cur = goat_team_ratings(season) or {}
    prev = goat_team_ratings(season - 1) or {}
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
        # Games played this season drives the weight (fall back to a small
        # early-season assumption when the feed doesn't report it).
        n = c.get("games") or 5
        w = max(0.0, min(1.0, n / 30.0))   # full current weight by ~30 games
        blended = dict(c)
        for f in ("off", "def", "net", "pace"):
            cv, pv = c.get(f), p.get(f)
            if cv is not None and pv is not None:
                blended[f] = round(w * cv + (1 - w) * pv, 2)
        blended["_blend_w"] = round(w, 2)
        out[abbr] = blended
    return out


@st.cache_data(show_spinner=False, ttl=3600)
def goat_adv_avg(pid, season):
    """Player's season-average advanced stats (usage%, assist%, PIE, TS%),
    cached an hour. Real efficiency signals the box score alone doesn't show."""
    if player_advanced_avg is None:
        return {}
    try:
        return player_advanced_avg(pid, season)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=3600)
def goat_expected_threes(pid, season):
    """Rough per-game 3PT attempts/makes from shooting splits, cached an hour.
    Context for a 3-Pointers-Made prop. {} when unavailable."""
    if expected_threes is None:
        return {}
    try:
        return expected_threes(pid, season)
    except Exception:
        return {}


@st.cache_data(show_spinner=False, ttl=86400)
def cached_bio(player_name):
    """Player photo + position/height/weight from ESPN (free). Cached a day —
    bios barely change. None on any failure (cosmetic only)."""
    try:
        return espn_bio(player_name)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=900)
def todays_keys():
    """Normalized name-keys of players with props posted today (SportsGameOdds)."""
    try:
        return sgo_today()
    except Exception:
        return set()


@st.cache_data(show_spinner=False, ttl=900)
def _cached_games_on_date_ok(date_iso):
    """Cache ONLY successful non-empty fetches (raises on empty so a transient
    429/empty is not cached for 15 min)."""
    from odds.balldontlie_props import games_on_date
    g = games_on_date(date_iso)
    if not g:
        raise ValueError("empty — do not cache")
    return g


def cached_games_on_date(date_iso):
    """Scheduled NBA games for a date. Retries live if the cache has no good
    copy yet (so a one-off empty fetch doesn't persist as 'No games')."""
    try:
        return _cached_games_on_date_ok(date_iso)
    except Exception:
        # No cached success — try once live (a genuinely empty day returns []).
        try:
            from odds.balldontlie_props import games_on_date
            return games_on_date(date_iso)
        except Exception:
            return []


@st.cache_data(show_spinner=False, ttl=900)
def cached_prop_keys_for_date(date_iso):
    """Normalized name-keys of players who have OVER/UNDER props posted for a
    date (balldontlie). Empty when books haven't posted props yet (future days)
    — the caller treats empty as 'props not up', not 'no players'. Cached 15
    min; deadline-bounded so a slow feed can't hang the tab."""
    def _build():
        from odds.balldontlie_props import all_player_props
        props = all_player_props(date_iso) or {}
        return set(props.keys())
    try:
        return _with_deadline(_build, seconds=6, default=set()) or set()
    except Exception:
        return set()


@st.cache_data(show_spinner=False, ttl=900)
def cached_all_props():
    """Bulk prop lines for today, cached 15 min so a full evening of research
    barely touches the API (and rarely trips the free-tier rate limit). Lines
    move slowly pre-game, so 15 min is plenty fresh. Re-raises so the scanner
    can report a rate-limit distinctly."""
    return sgo_all_props()


def _with_deadline(fn, seconds=4, default=None):
    """Run fn() on a side thread and give up after `seconds`.

    The game-line and teammate-injury lookups hit the network. If a feed is
    slow or unreachable, a direct call would block Streamlit's single render
    thread and freeze the whole page on "RUNNING…" — which is exactly the
    "nothing loads below the inputs" bug. This returns `default` (an empty map)
    the instant the deadline passes, so a slow feed degrades to "no extra"
    instead of hanging. The thread is a daemon, so a truly stuck call can't keep
    the process alive."""
    import threading
    box = {"val": default}

    def _run():
        try:
            box["val"] = fn()
        except Exception:
            box["val"] = default

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    return box["val"]


@st.cache_data(show_spinner=False, ttl=900)
def cached_out_teammates():
    """OUT-teammate map, cached 15 min AND bounded to a short deadline so a slow
    network pass can never stall the Analyze tab. Degrades to {} on timeout."""
    def _build():
        from collect.teammate_injuries import build_out_teammates_map
        return build_out_teammates_map()
    return _with_deadline(_build, seconds=4, default={}) or {}


@st.cache_data(show_spinner=False, ttl=900)
def cached_team_injuries():
    """{team: [(name, status, ppg), ...]} of OUT/DOUBTFUL players per team, for
    the game-model Star Tax. Cached 15 min, deadline-bounded like the rest."""
    def _build():
        from collect.teammate_injuries import out_players_by_team
        return out_players_by_team()
    return _with_deadline(_build, seconds=4, default={}) or {}


@st.cache_data(show_spinner=False, ttl=600)
def cached_team_lineup(team_abbr):
    """Confirmed starters/bench for a team's game TODAY, from the lineups feed
    (posts near tip-off; empty before). Finds today's game id for the team,
    then pulls that team's rows only. Cached 10 min, deadline-bounded."""
    if not team_abbr:
        return {"starters": [], "bench": []}
    def _build():
        from collect.goat_data import lineup_for_team
        from odds.balldontlie_props import _todays_game_ids
        from collect.schedule import upcoming_games
        from database import nba_today
        # Map team -> today's game id via the schedule's game list.
        gids = _todays_game_ids()
        # Match the team to one of today's games by checking each game's rows.
        for gid in gids:
            lu = lineup_for_team(gid, team_abbr)
            if lu["starters"] or lu["bench"]:
                return lu
        return {"starters": [], "bench": []}
    return _with_deadline(_build, seconds=4,
                          default={"starters": [], "bench": []}) \
        or {"starters": [], "bench": []}


@st.cache_data(show_spinner=False, ttl=600)
def cached_opp_starters(opp_abbr):
    """Opponent's confirmed starters WITH position + player_id for tonight's
    game: [{'name','position','player_id'}, ...]. Used to pick the likely
    primary defender (same position group) for a matchup-defense note."""
    if not opp_abbr:
        return []
    def _build():
        from collect.goat_data import starters_detailed
        from odds.balldontlie_props import _todays_game_ids
        for gid in _todays_game_ids():
            s = starters_detailed(gid, opp_abbr)
            if s:
                return s
        return []
    return _with_deadline(_build, seconds=4, default=[]) or []


def primary_defender_note(opp_abbr, player_group, season):
    """Pick the opponent starter whose position matches the player's group and
    return a plain note on what he allows to his man. None if unavailable.
    Honest: this is the LIKELY matchup by position, not a confirmed assignment —
    defenders switch, so we label it as such."""
    starters = cached_opp_starters(opp_abbr)
    if not starters:
        return None
    # Map our position groups to lineup slots (G/F/C).
    grp = (player_group or "").lower()
    want = ("G" if "guard" in grp else "C" if "big" in grp or "center" in grp
            else "F")
    cand = [s for s in starters if s.get("position", "").startswith(want)]
    cand = cand or starters  # fall back to any starter if no slot match
    for s in cand:
        pid = s.get("player_id")
        if pid is None:
            continue
        adv = goat_adv_avg(pid, season)
        if adv and adv.get("matchup_pts") is not None:
            return {"name": s["name"], "adv": adv}
    return None


@st.cache_data(show_spinner=False, ttl=900)
def cached_game_lines(date=None):
    """Game-lines map, cached 15 min AND bounded to a short deadline. A live odds
    fetch that's slow degrades to {} instead of freezing the page."""
    def _build():
        from odds.game_lines import build_game_lines_map
        return build_game_lines_map(date)
    return _with_deadline(_build, seconds=4, default={}) or {}


@st.cache_data(show_spinner=False, ttl=300)
def cached_schedule(date=None):
    """Today's full schedule with each game's status, cached 5 min and bounded
    to a short deadline. Returns {matchup "AWAY @ HOME": status_str}. Used to
    tell OPEN (not yet tipped) games apart from LIVE/FINAL ones, so the Game
    Lines tab can say '3 of 11 open' instead of a bare '3 games'. A status that
    looks like an ISO timestamp (e.g. '2026-10-22T02:00:00Z') means the game
    has NOT started yet; anything else ('1st Qtr', 'Final', 'Halftime') means
    it is live or done."""
    import datetime as _d
    date = date or _d.date.today().isoformat()
    def _build():
        from collect.balldontlie_client import _get
        out = {}
        for g in _get("games", {"dates[]": date, "per_page": 100}).get("data", []):
            h = (g.get("home_team") or {}).get("abbreviation")
            a = (g.get("visitor_team") or {}).get("abbreviation")
            if h and a:
                out[f"{a} @ {h}"] = str(g.get("status") or "")
        return out
    return _with_deadline(_build, seconds=4, default={}) or {}


@st.cache_data(show_spinner=False, ttl=300)
def cached_scores(date=None):
    """Final/live scores per game, cached 5 min. Returns
    {matchup "AWAY @ HOME": {"home": int, "away": int, "final": bool}} so the
    Game Lines tab can grade the model's pick once a game is over. Empty on any
    failure — grading is optional, never blocks the page."""
    import datetime as _d
    date = date or _d.date.today().isoformat()
    def _build():
        from collect.balldontlie_client import _get
        out = {}
        for g in _get("games", {"dates[]": date, "per_page": 100}).get("data", []):
            h = (g.get("home_team") or {}).get("abbreviation")
            a = (g.get("visitor_team") or {}).get("abbreviation")
            if not (h and a):
                continue
            hs, as_ = g.get("home_team_score"), g.get("visitor_team_score")
            if hs is None or as_ is None:
                continue
            final = str(g.get("status") or "").strip().lower().startswith("final")
            out[f"{a} @ {h}"] = {"home": int(hs), "away": int(as_),
                                 "final": final}
        return out
    return _with_deadline(_build, seconds=4, default={}) or {}


def _game_not_started(status: str) -> bool:
    """True if the game hasn't tipped off yet (status is a future ISO time)."""
    if not status:
        return False
    s = status.strip()
    # Scheduled games carry an ISO start time; live/final carry text like
    # '1st Qtr', 'Halftime', 'Final'.
    return s[:4].isdigit() and "T" in s


# Minimum games THIS season before we trust a VALUE flag. Early in a season a
# player's "recent form" leans on last season, so a value call on a 1-2 game
# sample is noise — we show the projection but hold the green badge until here.
MIN_SEASON_GAMES_FOR_VALUE = 5


def _season_start_iso():
    """ISO date of the current NBA season's start (Oct 1 of the season year)."""
    t = date.today()
    yr = t.year if t.month >= 10 else t.year - 1
    return f"{yr}-10-01"


def season_games_count(hist):
    """How many games in `hist` fall in the current season (>= Oct 1)."""
    try:
        start = _season_start_iso()
        return int((hist["game_date"] >= start).sum())
    except Exception:
        return 0


def scan_today_value(_config, stats, edge_threshold, max_players):
    """Run every player in today's games through the model, compare each real
    posted prop line/odds to our projection, and return value bets ranked by EV.

    _config is underscored so Streamlit's cache doesn't try to hash the model
    object. Returns (rows, note) where rows is a list of dicts and note is a
    plain-English status line. Pure read: no logging, no writes.

    FAST PATH: all sportsbook props are fetched in ONE call (all_player_props)
    and looked up in memory — no per-player, per-stat network requests. A full
    slate scan is one API call instead of hundreds.
    """
    import datetime as _dt
    if not sgo_has_key():
        return [], "No SportsGameOdds key — add SPORTSGAMEODDS_KEY to .env to scan real odds."

    # ONE network call (cached 5 min): every player's prop lines for today.
    try:
        props = cached_all_props()
    except Exception as e:
        if "rate limit" in str(e).lower() or "429" in str(e):
            return [], ("⏳ SportsGameOdds rate limit hit (free tier) — wait "
                        "about a minute, then scan again.")
        return [], f"Couldn't fetch odds: {e}"
    if not props:
        return [], "No props posted yet for today (they appear ~12-24h before tip-off)."

    def _nk(s):
        return "".join(c for c in s.lower() if c.isalnum())

    # Only scan players we have history for AND who have props posted today.
    roster = [(name, pid) for (name, pid) in player_list() if _nk(name) in props]

    # PERF: the opponent-defense maps cost ~0.4s each to build. They're keyed by
    # cutoff date, and every player is predicting the SAME upcoming slate, so we
    # compute them ONCE for a single shared cutoff (today) instead of per player
    # (which was ~177 rebuilds = the whole slowdown). Projection impact is nil:
    # the defensive map barely moves day to day within a slate.
    shared_cutoff = _dt.date.today().isoformat()
    shared_def_map = defense_map(shared_cutoff)
    shared_pos_maps = {s: positional_map(s, shared_cutoff) for s in stats}
    # Official balldontlie team ratings (def rating + pace), computed ONCE per
    # scan — season year from the cutoff, >=2024 to stay on GOAT-available data.
    try:
        _rs = int(shared_cutoff[:4])
    except Exception:
        _rs = 2026
    shared_team_ratings = goat_team_ratings(_rs if _rs >= 2024 else 2024)
    # Game-day usage signal: which players have OUT/DOUBTFUL teammates tonight.
    # Built ONCE per scan from the merged injuries table.
    try:
        shared_out_teammates = cached_out_teammates()
    except Exception:
        shared_out_teammates = {}
    # Game lines (total + spread) for tonight's slate, built ONCE per scan.
    try:
        shared_game_lines = cached_game_lines(shared_cutoff)
    except Exception:
        shared_game_lines = {}

    rows = []
    for name, pid in roster:
        try:
            player_props = props.get(_nk(name), {})
            if not player_props:
                continue
            hist = cached_history(pid)
            if hist is None or len(hist) < 20:
                continue
            last_date = hist.iloc[-1]["game_date"]
            try:
                cutoff = (_dt.date.fromisoformat(last_date)
                          + _dt.timedelta(days=1)).isoformat()
            except Exception:
                cutoff = last_date
            my_team = player_team(pid)
            auto = next_opponent(my_team)
            opp, is_home = (auto if auto else (hist.iloc[-1]["opponent"], True))
            grp = player_group(pid, cutoff)
            inj = current_injury(pid)
            _early = season_games_count(hist) < MIN_SEASON_GAMES_FOR_VALUE

            # Confirmed starting lineup (tonight) — cached per team, so this is
            # one feed hit per team no matter how many of its players we scan.
            # 'usually_starts' is proxied by recent minutes (a rotation starter
            # plays ~24+ mpg); only an explicit starter/bench slot moves the
            # projection (see the confirmed_lineup factor).
            scan_lineup_status = None
            scan_usually_starts = False
            scan_min_override = None
            try:
                _lu = cached_team_lineup(my_team) if my_team else None
                if _lu and _lu.get("starters"):
                    _last = name.split()[-1]
                    if any(_last in s for s in _lu["starters"]):
                        scan_lineup_status = "starter"
                    elif any(_last in b for b in _lu.get("bench", [])):
                        scan_lineup_status = "bench"
                    _rm = float(hist["minutes"].tail(10).mean())
                    scan_usually_starts = _rm >= 24.0
                    # Role-change minutes override (mirrors the Analyze tab).
                    if scan_lineup_status == "starter" and not scan_usually_starts:
                        scan_min_override = max(_rm, 28.0)
                    elif scan_lineup_status == "bench" and scan_usually_starts:
                        scan_min_override = min(_rm, 16.0)
            except Exception:
                scan_lineup_status = None

            # Advanced role (usage% + pace) — cached per player-season.
            scan_adv = None
            try:
                _sc = int(cutoff[:4])
                scan_adv = goat_adv_avg(pid, _sc if _sc >= 2024 else 2024)
            except Exception:
                scan_adv = None

            for stat in stats:
                hit = player_props.get(stat)
                if not hit or not hit.get("line"):
                    continue
                line = hit["line"]
                o_over = hit["odds_over"] or -110
                o_under = hit["odds_under"] or -110
                ctx = {
                    "opponent_abbr": opp,
                    "defense_factor_map": shared_def_map,
                    "positional_defense_map": shared_pos_maps.get(stat, {}),
                    "position_group": grp,
                    "upcoming_is_home": is_home,
                    "team_ratings_map": shared_team_ratings,
                    "current_team": my_team,
                }
                ctx.update(_rate_ctx())  # rate-model toggle + blend
                ctx["recent_pace_map"] = recent_pace_map((my_team, opp), cutoff)
                _s_h2h_avg, _s_h2h_n = _h2h_vs_opponent(hist, opp, stat, cutoff)
                if _s_h2h_avg is not None:
                    ctx["h2h_avg"] = _s_h2h_avg
                    ctx["h2h_games"] = _s_h2h_n
                if scan_lineup_status:
                    ctx["lineup_status"] = scan_lineup_status
                    ctx["usually_starts"] = scan_usually_starts
                    if scan_min_override is not None:
                        ctx["minutes_override"] = scan_min_override
                if scan_adv:
                    ctx["player_adv"] = scan_adv
                if inj:
                    ctx["injury_status"] = inj[0]
                mates_out = shared_out_teammates.get(pid)
                if mates_out:
                    ctx["out_teammates"] = mates_out
                gl = shared_game_lines.get(my_team)
                if gl:
                    ctx["game_line"] = gl
                res = analyze(_config, pid, stat, line, cutoff, hist, ctx)
                if res["projection"] is None:
                    continue
                # Judge value on the market-anchored probability: the model
                # alone hasn't beaten the posted line (see market_edge_test).
                _anch = anchor_to_market(res["p_over"], res["p_under"],
                                         int(o_over), int(o_under),
                                         p_over_raw=res.get("p_over_raw"),
                                         p_over_fitted=slate_p_over(name, stat, line))
                ev = evaluate_prop(_anch["p_over"], _anch["p_under"],
                                   int(o_over), int(o_under),
                                   edge_threshold=edge_threshold)
                # Pick = which side the PROJECTION lands on vs the line. This is
                # unambiguous (proj above line -> OVER) and never contradicts the
                # shown projection, unlike leaning on p_over which can be
                # miscalibrated for very low lines (e.g. 0.5 blocks/steals).
                lean = "OVER" if res["projection"] > line else "UNDER"
                ev_pick = ev["ev_over_pct"] if lean == "OVER" else ev["ev_under_pct"]
                # Compact 'why' for the scanner table — same honest logic as the
                # Analyze why-box, condensed to one line.
                _edge = res["projection"] - line
                _sc_season = int(cutoff[:4]) if cutoff else 2026
                if _sc_season < 2024:
                    _sc_season = 2024
                _sc_weak = _opp_weakness_note(opp, _sc_season, stat)
                _sc_hr = hit_rate_windows(hist, stat, line)
                _sc_bits, _ = _why_take_it(res, lean, _edge, line, stat, opp,
                                           _sc_hr, opp_weak=_sc_weak)
                # Strip markdown bold for the table cell.
                _why_txt = " ".join(b.replace("**", "") for b in _sc_bits)
                rows.append({
                    "Player": name,
                    "Matchup": f"{my_team} {'vs' if is_home else '@'} {opp}",
                    "Prop": PROP_WORDS.get(stat, stat),
                    "Line": line,
                    "Proj": res["projection"],
                    "Pick": lean,
                    "Win %": int(round((res["p_over"] if lean == "OVER"
                                        else res["p_under"]) * 100)),
                    "Odds": o_over if lean == "OVER" else o_under,
                    "EV %": ev_pick if ev_pick is not None else 0.0,
                    "Value?": "✅" if (ev["is_value"] and not _early and
                                       ev["recommended_side"] == lean.lower())
                              else ("⏳" if (ev["is_value"] and _early) else ""),
                    "Books": hit.get("books", 0),
                    "Why": _why_txt,
                    "_pid": pid,
                })
        except Exception:
            continue  # one bad player never sinks the whole scan

    # Rank best EV first.
    rows.sort(key=lambda r: r["EV %"], reverse=True)
    note = (f"Scanned {len(roster)} players in today's games · "
            f"{len(rows)} priced props found.")
    return rows, note


def _plain_verdict(ev: dict) -> str:
    """Turn the EV engine's verdict into one everyday sentence."""
    if ev["is_value"]:
        side = ev["recommended_side"].upper()
        return (f"The math likes the {side} here — the odds give you a bit more "
                f"than the risk, based on our guess. Still not a sure thing.")
    return ("The math doesn't see a clear edge here — the odds are about fair or "
            "worse, so there's no special reason to bet it.")


def _plain_reason(adj: str) -> str:
    """Rewrite one raw adjustment line into an everyday sentence.

    Raw looks like:
      "[Player Recent Performance w=40%] recent form cold (L5 10.8 vs season
       17.0) x0.800 -> net x0.920 (13.1 -> 12.0)"
    We keep the factor name, say up or down and roughly why, and show the
    before -> after numbers — dropping the x-multipliers and 'net' math.
    """
    import re
    name = adj.split("]")[0].lstrip("[").split(" w=")[0] if "]" in adj else adj
    # Pull the "(A -> B)" at the very end for the numbers.
    nums = re.findall(r"\(([\d.]+) -> ([\d.]+)\)", adj)
    before, after = (nums[-1] if nums else (None, None))
    direction = ""
    if before and after:
        direction = ("raised the projection" if float(after) > float(before)
                     else "lowered the projection")
    low = adj.lower()
    if "vs " in low and ("soft" in low or "tough" in low):
        why = ("opponent is soft against this player's position"
               if "soft" in low else
               "opponent is tough against this player's position")
    elif "cold" in low:
        why = "recent form below season average"
    elif "hot" in low:
        why = "recent form above season average"
    elif "tough" in low:
        why = "opponent is a strong defense"
    elif "soft" in low:
        why = "opponent is a weak defense"
    elif "short rest" in low or "back-to-back" in low:
        why = "on short rest"
    elif "well rested" in low:
        why = "well rested"
    elif "home" in low:
        why = "home-court effect"
    elif "away" in low:
        why = "road effect"
    elif "rising" in low:
        why = "trending up in minutes"
    elif "falling" in low:
        why = "trending down in minutes"
    else:
        why = "matchup adjustment"
    if before and after:
        return f"**{name}** — {why}; {direction} ({before} → {after})."
    return f"**{name}** — {why}."


def _opp_weakness_note(opp, season, prop_type):
    """A short 'what this opponent gives up' line, tailored to the prop type,
    from the team-style defense tables. Returns '' when no data (so the why-box
    just omits it rather than guessing)."""
    try:
        ddef = cached_team_style(season, "general", "defense").get(opp) or {}
        drim = cached_team_style(season, "tracking", "defense").get(opp) or {}
    except Exception:
        return ""
    bits = []
    paint = ddef.get("opp_pts_paint")
    rim = drim.get("def_rim_fg_pct")
    fb = ddef.get("opp_pts_fb")
    # Scoring/makes props care about paint + rim; everything else still benefits
    # from the paint read, which is the most interpretable single number.
    if prop_type in ("points", "fgm", "fga", "rebounds", "threes_made"):
        if paint is not None:
            bits.append(f"{paint:.0f} paint pts/g allowed")
        if rim is not None:
            bits.append(f"{rim*100:.0f}% allowed at the rim")
        if fb is not None and prop_type == "points":
            bits.append(f"{fb:.0f} fast-break pts/g")
    else:
        if paint is not None:
            bits.append(f"{paint:.0f} paint pts/g allowed")
    return " · ".join(bits[:3])


def _why_take_it(res, lean, edge, line, prop_type, opp, hr, opp_weak=""):
    """Plain-English 'why this is the pick' — built ONLY from what the model
    actually produced (projection vs line, the factor that moved it most, the
    matchup, recent hit-rate). Never fabricates a reason: if the edge is thin or
    nothing drove it, it says so. Returns (headline, bullets, honest_caveat)."""
    word = PROP_WORDS.get(prop_type, prop_type).lower()
    proj = res.get("projection")
    bullets = []

    # 1) The core: projection vs the line.
    if proj is not None:
        gap = abs(edge)
        if lean == "OVER":
            bullets.append(
                f"The model projects **{proj} {word}** — {gap:.1f} above the "
                f"{line:g} line, so it leans **OVER**.")
        else:
            bullets.append(
                f"The model projects **{proj} {word}** — {gap:.1f} below the "
                f"{line:g} line, so it leans **UNDER**.")

    # 2) The single biggest matchup/form driver (from the real adjustments).
    adjs = res.get("adjustments") or []
    if adjs:
        import re as _re
        def _mag(a):
            n = _re.findall(r"\(([\d.]+) -> ([\d.]+)\)", a)
            if not n:
                return 0.0
            b, af = n[-1]
            return abs(float(af) - float(b))
        top = max(adjs, key=_mag)
        if _mag(top) > 0.05:
            bullets.append("Biggest factor: " + _plain_reason(top).lstrip("• "))

    # 2b) Opponent's specific defensive weakness (paint / rim / fast break).
    if opp_weak:
        bullets.append(f"Matchup: **{opp}** gives up {opp_weak}.")

    # 3) Recent hit-rate vs THIS line (if computed).
    if hr:
        n, hits, pct, _avg = hr[0]
        p = int(round(pct * 100))
        agree = ((lean == "OVER" and p >= 60) or (lean == "UNDER" and p <= 40))
        if agree:
            bullets.append(
                f"History agrees: he went {'over' if lean=='OVER' else 'under'} "
                f"this line in {hits}/{n} of his last {n} games.")
        else:
            bullets.append(
                f"Note: recent hit-rate ({hits}/{n} over last {n}) doesn't "
                f"strongly back this lean — the edge is coming from the "
                f"projection, not the streak.")

    # Honest confidence read.
    conf = (res.get("confidence") or "").lower()
    thin = abs(edge) < 1.0 or "low" in conf or "early" in conf
    if thin:
        caveat = ("⚠️ Thin edge — treat this as lean, not a lock. Small samples "
                  "and early-season noise can flip it.")
    else:
        caveat = ("This is the model's read from form + matchup, not a "
                  "guarantee. Always sanity-check injuries and the posted line.")
    return bullets, caveat


# --------------------------------------------------------------------------- #
# MODEL BUILDER STATE  (rendered as a tab below — NOT the sidebar, which is
# unusable on phones). Config is built from session_state so every tab sees it
# regardless of which tab the controls live in.
# --------------------------------------------------------------------------- #

# The recommended factor set (importance for simple mode, % for weighted).
RECOMMENDED_SIMPLE = {
    "player_last_n_avg": "high", "opponent_defense_by_position": "high",
    "opponent_team_rating": "medium", "teammate_injuries": "medium",
    "confirmed_lineup": "medium", "usage_and_pace": "medium",
    "game_environment": "medium",
    "rest_days": "medium", "home_away_split": "low", "minutes_trend": "low",
}
RECOMMENDED_WEIGHTED = {
    "player_last_n_avg": 26, "opponent_defense_by_position": 18,
    "opponent_team_rating": 11, "teammate_injuries": 9,
    "confirmed_lineup": 8, "usage_and_pace": 9, "game_environment": 8,
    "rest_days": 5, "home_away_split": 3, "minutes_trend": 3,
}

# Friendly labels for the factor controls.
FACTOR_LABELS = {
    "player_last_n_avg": "Recent form",
    "opponent_defense": "Opponent defense",
    "opponent_defense_by_position": "Opponent defense vs position",
    "opponent_team_rating": "Opponent team rating (def + pace)",
    "rest_days": "Rest",
    "home_away_split": "Home / away",
    "minutes_trend": "Playing-time trend",
    "player_vs_team": "History vs this opponent",
    "teammate_injuries": "Teammate injuries (usage boost)",
    "confirmed_lineup": "Confirmed starting lineup",
    "usage_and_pace": "Usage & pace (advanced role)",
    "game_environment": "Game line (total + spread)",
    "injury": "Injury status",
}


def _stage_factor_set(mode, factors, name=None):
    """Stage a (mode, factors) set to be applied at the TOP of the next run,
    before the builder widgets instantiate. Writing a widget's session_state
    key AFTER its widget rendered is forbidden by Streamlit, so preset / load /
    recommended buttons stage here and rerun instead of writing directly."""
    st.session_state["pending_model"] = {"mode": mode, "factors": dict(factors),
                                          "name": name}


def _apply_pending_model():
    """Apply a staged model set. MUST run before the builder widgets render."""
    pend = st.session_state.pop("pending_model", None)
    if not pend:
        return
    mode, factors = pend["mode"], pend["factors"]
    st.session_state["simple_mode"] = (mode == "simple")
    st.session_state["seeded_mode"] = (mode == "simple")  # don't re-seed over it
    for key in FACTORS:
        st.session_state[f"on_{key}"] = key in factors
        if key in factors:
            st.session_state[("i_" if mode == "simple" else "w_") + key] = \
                factors[key]
    if pend.get("name"):
        st.session_state["model_name"] = pend["name"]


# Apply any staged model BEFORE the mode toggle / factor widgets are created.
_apply_pending_model()


def _apply_recommended():
    """Stage the recommended set (applied on the next run, pre-widget)."""
    mode_simple = st.session_state.get("simple_mode", True)
    rec = RECOMMENDED_SIMPLE if mode_simple else RECOMMENDED_WEIGHTED
    _stage_factor_set("simple" if mode_simple else "weighted", rec)


# Seed defaults on FIRST load only (runs before widgets, so a direct write is
# safe here). If the URL carries a saved preset (survives Cloud reloads), load
# THAT instead of the recommended default — so the user's choice sticks.
if "seeded_mode" not in st.session_state:
    _restored = None
    _full = None
    try:
        import json as _json0
        _qpm = st.query_params.get("model")
        if _qpm:
            _full = _json0.loads(_qpm)
    except Exception:
        _full = None
    try:
        _qp = st.query_params.get("preset")
        if _qp and _qp in PRESETS:
            _restored = _qp
    except Exception:
        _restored = None
    if _full and isinstance(_full, dict) and _full.get("factors"):
        # Full saved state (any custom config) — highest priority.
        _mode = _full.get("mode", "simple")
        _simple = (_mode == "simple")
        _facs = _full["factors"]
        for key in FACTORS:
            st.session_state[f"on_{key}"] = key in _facs
            if key in _facs:
                st.session_state[("i_" if _simple else "w_") + key] = _facs[key]
        st.session_state["simple_mode"] = _simple
        st.session_state["seeded_mode"] = _simple
        if _full.get("name"):
            st.session_state["model_name"] = _full["name"]
    elif _restored:
        _mode, _factors, _ = PRESETS[_restored]
        _simple = (_mode == "simple")
        for key in FACTORS:
            st.session_state[f"on_{key}"] = key in _factors
            if key in _factors:
                st.session_state[("i_" if _simple else "w_") + key] = \
                    _factors[key]
        st.session_state["simple_mode"] = _simple
        st.session_state["seeded_mode"] = _simple
        st.session_state["model_name"] = (_restored.split(" ", 1)[-1]
                                           if " " in _restored else _restored)
    else:
        _simple_now = st.session_state.get("simple_mode", True)
        rec = RECOMMENDED_SIMPLE if _simple_now else RECOMMENDED_WEIGHTED
        for key in FACTORS:
            st.session_state[f"on_{key}"] = key in rec
            if key in rec:
                st.session_state[("i_" if _simple_now else "w_") + key] = rec[key]
        st.session_state["seeded_mode"] = _simple_now

# Restore the bet slip from the URL once per session (survives Cloud reboots).
if "_slip_restored" not in st.session_state:
    st.session_state["_slip_restored"] = True
    try:
        import json as _js
        _qs = st.query_params.get("slip")
        if _qs:
            _loaded = _js.loads(_qs)
            if isinstance(_loaded, list):
                st.session_state["my_slip"] = _loaded
    except Exception:
        pass


def _build_config_from_state():
    """Read the builder widgets' session_state and return (config, error).

    Lives separately from the controls so every tab can get the current model
    even though the controls render inside the Build Model tab."""
    simple_mode = st.session_state.get("simple_mode", True)
    model_name = st.session_state.get("model_name", "My First Model")
    chosen = {}
    for key in FACTORS:
        if not st.session_state.get(f"on_{key}"):
            continue
        if simple_mode:
            chosen[key] = st.session_state.get(f"i_{key}", "medium")
        else:
            chosen[key] = st.session_state.get(f"w_{key}", 20)
    if not chosen:
        return None, None
    try:
        cfg = build_config(
            model_name, "simple" if simple_mode else "weighted",
            {k: (v if simple_mode else int(v)) for k, v in chosen.items()})
        return cfg, None
    except ValueError as e:
        return None, str(e)


def _rate_ctx():
    """Rate-model settings from the builder widgets, as ctx keys. Shared by the
    Analyze tab and the Value Scanner so both honor the same toggle/slider."""
    use = st.session_state.get("use_rate_model", True)
    return {
        "use_rate_model": use,
        "rate_blend": st.session_state.get("rate_blend_pct", 35) / 100.0,
    }


config, config_err = _build_config_from_state()

# Persist the CURRENT model to the URL (survives Cloud reboots). Write only
# when it changed, so we don't thrash the query string every rerun.
if config is not None:
    try:
        import json as _jm
        _cur = _jm.dumps(_current_model_dict(), separators=(",", ":"))
        if st.session_state.get("_last_model_url") != _cur:
            st.query_params["model"] = _cur
            st.session_state["_last_model_url"] = _cur
    except Exception:
        pass

# Persist the bet slip to the URL too, only when it changed.
try:
    import json as _js2
    _slipcur = _js2.dumps(st.session_state.get("my_slip", []),
                          separators=(",", ":"))
    if st.session_state.get("_last_slip_url") != _slipcur:
        st.query_params["slip"] = _slipcur
        st.session_state["_last_slip_url"] = _slipcur
except Exception:
    pass


# --------------------------------------------------------------------------- #
# PRESETS + SAVED MODELS
#
# Streamlit Cloud has an EPHEMERAL filesystem (and our DB is re-downloaded on
# boot), so a disk/DB "save" would silently vanish on restart. Instead we keep
# saved models in session_state (durable for the session) and offer JSON
# download/upload so a model survives across sessions — honest about the
# platform, and portable. Built-in presets are always available.
# --------------------------------------------------------------------------- #

# Each preset: (mode, factors, one-line description). Simple mode unless noted.
PRESETS = {
    "⭐ Balanced (recommended)": ("simple", dict(RECOMMENDED_SIMPLE),
        "A bit of everything — every factor on, sensibly weighted. The solid "
        "all-purpose default."),
    "🔥 Trusts recent form": ("simple", {
        "player_last_n_avg": "high", "minutes_trend": "high",
        "usage_and_pace": "medium", "rest_days": "medium",
        "home_away_split": "low"},
        "Leans on how the player's been playing lately — form, minutes and "
        "role, with a nod to home/away and rest."),
    "🛡️ Focuses on the matchup": ("simple", {
        "opponent_defense_by_position": "high", "opponent_team_rating": "high",
        "player_vs_team": "medium", "player_last_n_avg": "medium",
        "game_environment": "medium", "opponent_defense": "low"},
        "Weights who they're facing — opponent defense by position, team "
        "rating/pace, and the player's own history vs this opponent."),
    "🩹 Chases injury news": ("simple", {
        "teammate_injuries": "high", "usage_and_pace": "high",
        "confirmed_lineup": "high", "player_last_n_avg": "medium",
        "injury": "medium", "minutes_trend": "medium"},
        "Backs opportunity — teammates out, confirmed lineups and a usage "
        "bump, on top of the player's recent production and injury status."),
    "⚖️ Exact-percent (advanced)": ("weighted", dict(RECOMMENDED_WEIGHTED),
        "Precise percentage weights across every factor that add up to 100 "
        "(power users)."),
}


def _apply_factor_set(mode, factors, name=None):
    """Stage a (mode, factors) set; applied at the top of the next run before
    the builder widgets render (callers must st.rerun() after calling)."""
    _stage_factor_set(mode, factors, name=name)


def _current_model_dict():
    """Serialize the current builder state to a portable dict."""
    simple_mode = st.session_state.get("simple_mode", True)
    mode = "simple" if simple_mode else "weighted"
    factors = {}
    for key in FACTORS:
        if st.session_state.get(f"on_{key}"):
            factors[key] = (st.session_state.get(f"i_{key}", "medium")
                            if simple_mode
                            else int(st.session_state.get(f"w_{key}", 20)))
    return {"name": st.session_state.get("model_name", "My Model"),
            "mode": mode, "factors": factors}


def _save_current_model():
    """Store the current model in session_state under its name."""
    m = _current_model_dict()
    if not m["factors"]:
        return False
    st.session_state.setdefault("saved_models", {})[m["name"]] = m
    return True


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
st.markdown("""
<div class="sb-hero">
  <span class="sb-pill">💣 NBA · Player Props</span>
  <h1>Bomb Vegas <span style="color:var(--muted);font-weight:700;">· Prop Analyzer</span></h1>
  <p>Your edge, every night. Stat projections, Over/Under reads, and the best
     price across 8 books. 💣</p>
</div>
""", unsafe_allow_html=True)

# Data health: say so out loud instead of quietly running on stale data.
try:
    import pipeline_status as _ps
    from database import nba_today as _nba_today
    _hc = get_connection()
    _last_game = _hc.execute("SELECT MAX(game_date) FROM player_game_logs").fetchone()[0]
    _hc.close()
    _today = _nba_today()
    _stale, _stale_msg = _ps.freshness(_last_game, _today, max_gap_days=4)
    if _stale and _today[5:7] not in ("07", "08", "09"):    # not the offseason
        st.warning(f"⚠ {_stale_msg}")
    _broken = [k for k in _ps.failed_steps()
               if k in ("daily:games", "daily:lines", "intraday:lines")]
    if _broken:
        st.warning("⚠ The last update had problems with: " + ", ".join(_broken)
                   + ". Prices or box scores may be out of date. "
                     "Details: data/pipeline_status.json")
except Exception:
    pass

tabM, tab1, tab5, tab4, tab3, tab2 = st.tabs(["⚙️ Build Model",
                                  "🔍 Analyze a Prop",
                                  "🏀 Game Lines (ML + Spread)",
                                  "🎯 Value Scanner",
                                  "📒 Track Record",
                                  "📊 Backtest"])

# ----------------------- Build Model (was the sidebar) ----------------------- #
with tabM:
    st.subheader("🏀 Build Your Model")
    st.write("Pick the factors that matter and how much. New here? Tap "
             "**⭐ Use recommended factors** — it's a solid starting point. "
             "Your model applies to every tab.")

    bc1, bc2 = st.columns([2, 1])
    bc1.text_input("Model name", "My First Model", key="model_name")
    bc2.toggle("Simple weighting (recommended)", value=True, key="simple_mode",
               help="On: rate each factor Low / Medium / High. Off: assign "
                    "exact percentages that sum to 100.")
    simple_mode = st.session_state.get("simple_mode", True)

    # ---- Presets: one-tap starting points ----
    st.markdown("#### Presets")
    st.caption("Each loads a ready-made model. Tap one, then tweak the factors "
               "below if you want.")
    preset_names = list(PRESETS.keys())
    pcols = st.columns(2)
    for i, pname in enumerate(preset_names):
        col = pcols[i % 2]
        mode, factors, desc = PRESETS[pname]
        if col.button(pname, key=f"preset_{i}", use_container_width=True):
            nm = pname.split(" ", 1)[-1] if " " in pname else pname
            _apply_factor_set(mode, factors, name=nm)
            # Persist the choice in the URL so a Cloud reload restores it
            # (session_state is wiped on reboot; query params survive).
            try:
                st.query_params["preset"] = pname
            except Exception:
                pass
            st.rerun()
        col.caption(desc)

    # ---- Save / load your own models (session + JSON file) ----
    with st.expander("💾 Save or load a model"):
        sv1, sv2 = st.columns([1, 1])
        if sv1.button("💾 Save current model", use_container_width=True):
            if _save_current_model():
                st.success(f"Saved **{st.session_state.get('model_name')}** "
                           "for this session. Use **Download** below to keep it "
                           "after the app restarts.")
            else:
                st.warning("Pick at least one factor before saving.")

        saved = st.session_state.get("saved_models", {})
        if saved:
            pick = sv2.selectbox("Load a saved model",
                                 ["—"] + list(saved.keys()))
            if pick and pick != "—":
                if st.button(f"Load “{pick}”"):
                    m = saved[pick]
                    _apply_factor_set(m["mode"], m["factors"], name=m["name"])
                    st.rerun()

        # Portable JSON so a model survives across sessions on the cloud.
        import json as _json
        st.download_button(
            "⬇️ Download this model (.json)",
            data=_json.dumps(_current_model_dict(), indent=2),
            file_name=f"{st.session_state.get('model_name', 'model')}.json",
            mime="application/json", use_container_width=True)
        up = st.file_uploader("⬆️ Load a model file (.json)", type="json",
                              key="model_upload")
        if up is not None:
            # Apply each uploaded file ONCE (by name+size) so re-runs don't keep
            # overwriting the user's subsequent manual edits.
            sig = f"{up.name}:{up.size}"
            if st.session_state.get("model_upload_sig") != sig:
                st.session_state["model_upload_sig"] = sig
                try:
                    m = _json.loads(up.read().decode("utf-8"))
                    nm = m.get("name", "Loaded model")
                    _apply_factor_set(m.get("mode", "simple"),
                                      m.get("factors", {}), name=nm)
                    st.session_state.setdefault("saved_models", {})[nm] = m
                    st.success(f"Loaded **{nm}**.")
                    st.rerun()
                except Exception:
                    st.error("That file isn't a valid model .json.")

    st.markdown("### Factors")
    for key, f in FACTORS.items():
        label = FACTOR_LABELS.get(key, f.label)
        if simple_mode:
            # One compact row: checkbox on the left, Low/Med/High pills on the
            # right (only when the factor is on). No more tall sliders.
            fc1, fc2 = st.columns([3, 4])
            on = fc1.checkbox(label, key=f"on_{key}", help=FACTOR_HELP.get(key))
            if on:
                if f"i_{key}" not in st.session_state:
                    st.session_state[f"i_{key}"] = "medium"
                fc2.radio(
                    f"{label} importance", options=["low", "medium", "high"],
                    format_func=lambda x: {"low": "Low", "medium": "Med",
                                           "high": "High"}[x],
                    key=f"i_{key}", horizontal=True,
                    label_visibility="collapsed")
        else:
            on = st.checkbox(label, key=f"on_{key}", help=FACTOR_HELP.get(key))
            if on:
                if f"w_{key}" not in st.session_state:
                    st.session_state[f"w_{key}"] = 20
                st.number_input(f"   {label} — weight %", 0, 100, step=5,
                                key=f"w_{key}")

    # Rebuild from the just-edited state so the summary below is current.
    config, config_err = _build_config_from_state()

    # ---- Deeper projection: rate model (per-minute x minutes x pace) --------
    st.markdown("### Deeper projection")
    st.checkbox(
        "Use the rate model (per-minute × projected minutes × pace)",
        key="use_rate_model",
        value=st.session_state.get("use_rate_model", True),
        help="On top of the recent/season-form blend, projects "
             "per-minute production × expected minutes × game pace, then "
             "blends the two. Values minutes and tempo directly. Applies to "
             "volume stats (points, threes, attempts, rebounds, assists, …).")
    if st.session_state.get("use_rate_model", True):
        st.slider(
            "Rate-model blend (how much weight the rate projection gets)",
            min_value=0, max_value=100, step=5,
            key="rate_blend_pct",
            value=st.session_state.get("rate_blend_pct", 35),
            help="0% = pure recent/season totals. 100% = pure rate model. "
                 "35% is a balanced default — the rate path is more reactive "
                 "to minutes/pace but noisier, so it gets a minority weight.")
        st.caption(f"Base projection = "
                   f"{100 - st.session_state.get('rate_blend_pct', 35)}% totals "
                   f"+ {st.session_state.get('rate_blend_pct', 35)}% rate model.")

    if not simple_mode and config:
        total = sum(int(st.session_state.get(f"w_{k}", 0))
                    for k in config.factors)
        ok = (total == 100)
        st.markdown(f"Weights total **:{'green' if ok else 'red'}[{total}%]** "
                    + ("✅" if ok else "— must sum to 100%"))
    if config_err:
        st.error(f"Config issue: {config_err}")
    elif config:
        st.success(f"Model ready: **{len(config.factors)} factor"
                   f"{'s' if len(config.factors) != 1 else ''}** active. "
                   "Head to any tab to use it.")
    else:
        st.info("Select at least one factor to build your model.")

if config_err and not config:
    # A broken config (e.g. weights ≠ 100) — the analysis tabs can't run.
    for _t in (tab1, tab5, tab4, tab3, tab2):
        with _t:
            st.warning("Fix your model in the **⚙️ Build Model** tab first "
                       f"({config_err}).")
elif not config:
    for _t in (tab1, tab5, tab4, tab3, tab2):
        with _t:
            st.info("👉 Open the **⚙️ Build Model** tab and pick at least one "
                    "factor (or tap ⭐ Use recommended factors) to start.")
else:

    # ----------------------- Check a Player ----------------------- #
    with tab1:
        players = player_list()

        # ---- DATE SELECTOR: pick a day, see its NBA games, auto-filter the
        # player list to players with props posted that day (like the NBA app's
        # scoreboard). Props post near tip-off, so future days show games but
        # may have no props yet — we say so rather than hiding players. --------
        import datetime as _da
        from database import nba_today as _nt
        _today_iso = _nt()
        _tdate = _da.date.fromisoformat(_today_iso)
        _dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        # Only show days that ACTUALLY have props posted — and only ever TODAY
        # and TOMORROW (books don't post further out). Usually just today until
        # tomorrow's lines go up near tip-off. Uses the 15-min cached prop-keys
        # helper, so this is cheap after the first check.
        _days_with_props = []
        for _i in range(2):  # today + tomorrow only
            _d_iso = (_tdate + _da.timedelta(days=_i)).isoformat()
            if cached_prop_keys_for_date(_d_iso):
                _days_with_props.append(_d_iso)
        if not _days_with_props:
            _days_with_props = [_today_iso]  # fall back to today
        if st.session_state.get("analyze_date") not in _days_with_props:
            st.session_state["analyze_date"] = _days_with_props[0]

        def _pick_date(_iso):
            # Callback: runs BEFORE the rerun, so Streamlit keeps the Analyze
            # tab active (st.rerun() would reset to the first tab).
            st.session_state["analyze_date"] = _iso

        st.caption("📅 **Days with props posted** (today / tomorrow) — pick "
                   "one; the player list auto-filters to who's playing that "
                   "day. Tomorrow appears here once the books post its lines.")
        _cols = st.columns(max(len(_days_with_props), 1))
        for _i, _iso in enumerate(_days_with_props):
            _dt = _da.date.fromisoformat(_iso)
            _day = "Today" if _iso == _today_iso else _dow[_dt.weekday()]
            _lbl = f"{_day}\n{_dt.month}/{_dt.day}"
            _is_sel = st.session_state["analyze_date"] == _iso
            _cols[_i].button(("🔵 " if _is_sel else "") + _lbl,
                             key=f"dsel_{_iso}", use_container_width=True,
                             on_click=_pick_date, args=(_iso,))
        sel_date = st.session_state["analyze_date"]

        # Show that day's games as a compact strip.
        _games = cached_games_on_date(sel_date)
        if _games:
            _mu = " · ".join(gg["matchup"] for gg in _games)
            st.caption(f"🏀 **{len(_games)} game"
                       f"{'s' if len(_games) != 1 else ''}** on {sel_date}:  "
                       f"{_mu}")
        else:
            st.caption(f"No NBA games scheduled on {sel_date}.")

        # Player filter: players with props posted for the SELECTED date.
        pkeys = cached_prop_keys_for_date(sel_date)
        if pkeys:
            only_games = st.checkbox(
                f"Only players with props posted on {sel_date} "
                f"({len(pkeys)} players)", value=True)
            if only_games:
                def _nk(s):
                    return "".join(c for c in s.lower() if c.isalnum())
                filtered = [p for p in players if _nk(p[0]) in pkeys]
                if filtered:
                    players = filtered
                else:
                    st.caption("No roster matches to the posted players — "
                               "showing everyone.")
        elif _games:
            st.caption("📋 Books haven't posted props for this day yet (they go "
                       "up closer to tip-off). Showing the full player list — "
                       "you can still project any player manually.")

        names = [p[0] for p in players]
        id_of = dict(players)

        st.subheader("Select player, opponent, and line")
        st.write("The **line** is the sportsbook's number for a stat (e.g. 25.5 "
                 "points). **Over** = more than the line, **Under** = fewer. "
                 "Pick who they're facing so the matchup factors apply.")
        c1, c2 = st.columns([2, 1])
        sel = c1.selectbox("Player", names,
                           index=names.index("Mikal Bridges")
                           if "Mikal Bridges" in names else 0)
        if "sgo_line" not in st.session_state:
            st.session_state["sgo_line"] = 25.5
        if "pending_line" in st.session_state:
            st.session_state["sgo_line"] = st.session_state.pop("pending_line")
        line = c2.number_input("Line", step=0.5, key="sgo_line",
                               help="The sportsbook's number. Over = above it, "
                                    "Under = below it. Auto-fill sets this to "
                                    "the real posted line.")
        # Stat picker: a click-to-select pill row (not a type-to-search box).
        prop_type = st.radio("Stat", list(PROP_WORDS.keys()),
                             format_func=lambda k: PROP_WORDS[k],
                             horizontal=True, key="prop_type_pick")

        pid = id_of[sel]
        hist = load_player_history(pid)
        # Predict an UPCOMING game: cutoff = the day AFTER the last game on
        # record, so every game to date is used (and nothing in the future —
        # there is nothing after it yet). This makes the card a true forward
        # projection rather than "re-predicting" the last game already played.
        import datetime as _dt
        last_date = hist.iloc[-1]["game_date"]
        try:
            cutoff = (_dt.date.fromisoformat(last_date)
                      + _dt.timedelta(days=1)).isoformat()
        except Exception:
            cutoff = last_date

        # Auto-detect the player's team (official roster) and next opponent.
        my_team = player_team(pid)
        auto = next_opponent(my_team)  # (opp, is_home) or None
        teams = team_abbrs()

        if auto:
            auto_opp, auto_home = auto
            default_opp = auto_opp
            default_site_idx = 0 if auto_home else 1
            st.caption(f"{sel} — {my_team} · next: "
                       f"{'vs' if auto_home else '@'} {auto_opp}")
        else:
            default_opp = hist.iloc[-1]["opponent"]
            default_site_idx = 0
            st.caption(f"{sel} — {my_team or '?'} · no scheduled game, pick below")

        # ---- Confirmed lineup (CURRENT roster — post-trade accurate) ----
        # Our team mapping comes from the player's last game log, which can be
        # LAST season's roster after an offseason trade. The lineups feed has
        # the real, current starters/bench, so we use it to (a) show tonight's
        # confirmed five and (b) flag the player's starter/bench role.
        lu = cached_team_lineup(my_team) if my_team else {"starters": [],
                                                          "bench": []}
        lineup_status = None  # 'starter' | 'bench' | None (fed to the model)
        if lu.get("starters"):
            in_starters = any(sel.split()[-1] in s for s in lu["starters"])
            in_bench = any(sel.split()[-1] in b for b in lu["bench"])
            lineup_status = ("starter" if in_starters
                             else "bench" if in_bench else None)
            role = ("🟢 confirmed STARTER" if in_starters
                    else "🪑 off the bench" if in_bench
                    else "⚠️ not in the posted lineup — may be out/traded")
            st.caption(f"**Tonight's confirmed {my_team} lineup:** "
                       + ", ".join(lu["starters"])
                       + f"  ·  {sel}: {role}")
            if not in_starters and not in_bench:
                st.warning(f"{sel} isn't in {my_team}'s confirmed lineup. If he "
                           "was traded or is out, our team/matchup data (built "
                           "from last game logs) may be stale for him.")

        d1, d2 = st.columns([1, 1])
        opp = d1.selectbox(
            "Opponent", teams,
            index=teams.index(default_opp) if default_opp in teams else 0,
            help="Auto-filled from the schedule. Change it to test a different "
                 "matchup.")
        site = d2.radio("Venue", ["Home", "Away"], index=default_site_idx,
                        horizontal=True,
                        help="Auto-filled from the schedule.")
        is_home = (site == "Home")

        grp = player_group(pid, cutoff)
        try:
            _rs = int(cutoff[:4])
        except Exception:
            _rs = 2026
        ctx = {
            "opponent_abbr": opp,
            "defense_factor_map": defense_map(cutoff),
            "positional_defense_map": positional_map(prop_type, cutoff),
            "position_group": grp,
            "upcoming_is_home": is_home,
            "team_ratings_map": goat_team_ratings(_rs if _rs >= 2024 else 2024),
            "current_team": my_team,
        }
        ctx.update(_rate_ctx())  # rate-model toggle + blend from the builder
        # True recent pace (last-10 games) for both teams, if the collector has
        # run; falls back to season pace inside the model when absent.
        ctx["recent_pace_map"] = recent_pace_map((my_team, opp), cutoff)
        # Head-to-head vs this opponent (leak-free) for the player_vs_team factor.
        _h2h_avg, _h2h_n = _h2h_vs_opponent(hist, opp, prop_type, cutoff)
        if _h2h_avg is not None:
            ctx["h2h_avg"] = _h2h_avg
            ctx["h2h_games"] = _h2h_n
        # Confirmed starting lineup (tonight) -> minutes/usage signal. We feed
        # the role only when the lineup is actually posted. "Usually starts" is
        # proxied by recent minutes (a rotation starter plays ~24+ mpg), so a
        # benching of a usual starter downgrades and a surprise starter bumps.
        if lineup_status:
            ctx["lineup_status"] = lineup_status
            try:
                recent_min = float(hist["minutes"].tail(10).mean())
            except Exception:
                recent_min = 0.0
            usually = recent_min >= 24.0
            ctx["usually_starts"] = usually
            # Drive projected MINUTES from the confirmed role when it DIFFERS
            # from his recent norm (the rate model then scales output by these
            # minutes). A surprise starter gets starter minutes; a benched usual
            # starter gets bench minutes. No override when the role matches his
            # norm — his recent minutes are already right. Starter baseline ~28,
            # bench baseline ~16 (conservative; we never claim exact minutes).
            STARTER_MIN, BENCH_MIN = 28.0, 16.0
            if lineup_status == "starter" and not usually:
                ctx["minutes_override"] = max(recent_min, STARTER_MIN)
            elif lineup_status == "bench" and usually:
                ctx["minutes_override"] = min(recent_min, BENCH_MIN)
        # Advanced role signals (usage% + pace) for the usage_and_pace factor.
        try:
            _adv = goat_adv_avg(pid, _rs if _rs >= 2024 else 2024)
            if _adv:
                ctx["player_adv"] = _adv
        except Exception:
            pass
        # Injury status (from the GOAT injuries snapshot, if fetched).
        inj = current_injury(pid)
        if inj:
            ctx["injury_status"] = inj[0]
            st.warning(f"⚠️ Injury: **{sel}** is listed **{inj[0]}**"
                       + (f" — {inj[1]}" if inj[1] else "")
                       + ". The projection accounts for this if 'Injury Status' "
                         "is one of your factors.")
        # Teammate injuries + game line are EXTRAS that need the network. They
        # are loaded through deadline-bounded cached wrappers (max 4s each) so a
        # slow feed can never freeze the page — on timeout they return {} and
        # the projection still renders. These were the two calls that caused the
        # "nothing loads below the inputs" freeze; the deadline is the guard.
        try:
            mates_out = cached_out_teammates().get(pid)
        except Exception:
            mates_out = None
        if mates_out:
            ctx["out_teammates"] = mates_out
            _who = ", ".join(f"{m[0]} ({m[2]:.0f} ppg)" if len(m) > 2 else m[0]
                             for m in mates_out[:3])
            st.info(f"🟢 Teammate(s) out: **{_who}**"
                    + ("…" if len(mates_out) > 3 else "")
                    + f" — more usage available for {sel}. The projection "
                      "bumps up (weighted by how much each absent teammate "
                      "scores) if 'Teammate Injuries' is one of your factors.")
        # Game line (total + spread) — scoring environment + blowout risk.
        try:
            gl = cached_game_lines().get(my_team)
        except Exception:
            gl = None
        if gl:
            ctx["game_line"] = gl
            _t = gl.get("total")
            _s = gl.get("spread")
            _bits = []
            if _t is not None:
                feel = ("high-scoring — leans overs up" if _t >= 235
                        else "low-scoring — leans unders" if _t <= 222
                        else "average pace")
                _bits.append(f"total **{_t:g}** ({feel})")
            if _s is not None:
                fav = "favored" if _s < 0 else "underdog"
                big = " — big spread, blowout risk caps starters' minutes" \
                      if abs(_s) >= 12 else ""
                _bits.append(f"{my_team} **{_s:+g}** ({fav}{big})")
            if _bits:
                st.info("📈 Game line: " + "; ".join(_bits)
                        + ". Wired into the projection if 'Game Line' is one of "
                          "your factors.")
        if grp:
            st.caption(f"{sel} is classified as a **{grp}** — the matchup factor "
                       f"uses how {opp} defends {grp}s for this stat.")
            # Raw number: how much of THIS stat the opponent allows to this
            # position group, + league rank (1 = allows the most = softest).
            if prop_type in ("points", "rebounds", "assists"):
                try:
                    _raw = cached_pos_defense_raw(prop_type, cutoff)
                    _cell = (_raw.get(opp) or {}).get(grp)
                except Exception:
                    _cell = None
                if _cell:
                    _r, _of = _cell["rank"], _cell["of"]
                    _soft = ("softest" if _r <= _of // 3
                             else "toughest" if _r > 2 * _of // 3
                             else "middle")
                    st.caption(
                        f"📊 {opp} allows **{_cell['allowed']:g} "
                        f"{PROP_WORDS[prop_type].lower()}** per game to "
                        f"{grp}s — **#{_r} of {_of}** ({_soft} matchup; "
                        "#1 = gives up the most).")

        # ---- Steals props: team ball-pressure matchup ----
        if prop_type == "steals":
            try:
                _ts = cached_team_steals(cutoff)
            except Exception:
                _ts = {}
            _o = _ts.get(opp)
            if _o:
                _ar, _of = _o["allowed_rank"], _o["of"]
                _soft = ("lots of giveaways — good for steals" if _ar <= _of // 3
                         else "protects the ball well — fewer steals"
                         if _ar > 2 * _of // 3 else "average")
                st.caption(
                    f"🖐️ {opp} coughs up **{_o['allowed']:g} steals/game** to "
                    f"opponents — **#{_ar} of {_of}** ({_soft}).")

        # ---- Likely primary defender (context only, not a projection factor) --
        # Who on the opponent probably guards this player (same position group),
        # and what that defender allows to his man. Defenders switch, so this is
        # the LIKELY matchup, not a confirmed assignment.
        try:
            _pd_note = primary_defender_note(opp, grp, _rs if _rs >= 2024 else 2024)
        except Exception:
            _pd_note = None
        if _pd_note:
            a = _pd_note["adv"]
            bits = [f"~{a['matchup_pts']:.1f} pts"]
            if a.get("matchup_fg_pct") is not None:
                bits.append(f"{a['matchup_fg_pct']*100:.0f}% FG")
            if a.get("matchup_3pt_pct") is not None:
                bits.append(f"{a['matchup_3pt_pct']*100:.0f}% from 3")
            if a.get("matchup_ast") is not None:
                bits.append(f"{a['matchup_ast']:.1f} ast")
            st.caption(f"🥊 Likely on {sel}: **{_pd_note['name']}** ({opp}) — "
                       f"allows his matchup " + ", ".join(bits)
                       + ". (Likely by position — defenders switch, so this is "
                         "context, not baked into the projection.)")

        # ---- Shooting splits (context) — only for the 3-Pointers Made prop ----
        # How many threes this player actually launches per game, from distance
        # splits. Direct read on a 3PM over/under; approximate, context only.
        if prop_type == "threes_made":
            try:
                _et = goat_expected_threes(pid, _rs if _rs >= 2024 else 2024)
            except Exception:
                _et = {}
            if _et:
                st.caption(
                    f"🎯 Shooting splits: **{sel}** takes ~{_et['fga_3']:.1f} "
                    f"threes a game, makes ~{_et['fgm_3']:.1f} "
                    f"({_et['fg3_pct']*100:.0f}%). Compare the **makes** to the "
                    "line above. (From last season's distance splits — context, "
                    "not baked into the projection.)")

        # Odds inputs live here so the EV verdict can render inside the slip.
        st.markdown("**Sportsbook odds** (optional — enter the real price for "
                    "an EV check; -110 = risk \\$110 to win \\$100)")
        with st.expander("❓ What do odds like -194 or +138 mean?"):
            st.markdown(
                "American odds tell you **who's favored** and **what a bet pays**:\n\n"
                "- **Minus (−)** = favorite. The number is what you **risk to win "
                "\\$100**. −194 → bet \\$194 to win \\$100.\n"
                "- **Plus (+)** = underdog. The number is what you **win on a "
                "\\$100 bet**. +138 → bet \\$100 to win \\$138.\n"
                "- **−110** is the normal \"coin-flip\" price (the book's cut). "
                "The further from −110, the more lopsided the matchup.\n\n"
                "Scaled to \\$10: +138 wins \\$13.80; −194 wins about \\$5.15. "
                "Enter the **over** price and the **under** price and we'll tell "
                "you if there's real value (positive EV) on either side.")

        # Auto-fill real odds from the odds router (balldontlie primary).
        if sgo_has_key():
            def _pull_odds():
                """Pull consensus odds+line for the current player/stat. Reuses
                the cached bulk slate first (free if already fetched); only
                falls back to a direct single call if this player isn't in it."""
                hit = None
                try:
                    nk = "".join(c for c in sel.lower() if c.isalnum())
                    allp = cached_all_props()
                    hit = (allp.get(nk) or {}).get(prop_type)
                except Exception:
                    hit = None
                if not hit:
                    hit = sgo_prop_odds(sel, prop_type)
                return hit

            def _stage_odds(hit):
                """Stage pulled values under 'pending_' keys — applied to the
                real widget keys at the TOP of the next run, before those widgets
                are instantiated (Streamlit forbids writing a widget key after
                its widget renders)."""
                st.session_state["pending_over"] = hit["odds_over"] or -110
                st.session_state["pending_under"] = hit["odds_under"] or -110
                if hit.get("line"):
                    st.session_state["pending_line"] = hit["line"]
                st.session_state["sgo_msg"] = (
                    f"Auto-filled consensus from {hit['books']} books: "
                    f"line {hit.get('line','?')}, "
                    f"O {hit['odds_over']} / U {hit['odds_under']}.")

            # AUTO-FILL ON SELECTION: when the player or stat changes, pull the
            # real odds once automatically (no button). A marker in session_state
            # remembers what we last filled so this doesn't re-pull every rerun.
            _sel_key = f"{sel}|{prop_type}"
            if st.session_state.get("autofill_for") != _sel_key:
                st.session_state["autofill_for"] = _sel_key
                hit = _pull_odds()
                if hit and (hit["odds_over"] or hit["odds_under"]):
                    _stage_odds(hit)
                    st.rerun()
                else:
                    st.session_state["sgo_msg_warn"] = (
                        "No prop odds posted for this player yet (they appear "
                        "closer to tip-off, and a book can pull a line). Enter "
                        "odds by hand, or tap refresh.")

            # Manual refresh — for when a line was suspended and later re-posts.
            if st.button("🎯 Refresh odds"):
                hit = _pull_odds()
                if hit and (hit["odds_over"] or hit["odds_under"]):
                    _stage_odds(hit)
                    st.rerun()
                else:
                    st.session_state["sgo_msg_warn"] = (
                        "Still no prop posted for this player.")

            if st.session_state.get("sgo_msg"):
                st.success(st.session_state.pop("sgo_msg"))
            if st.session_state.get("sgo_msg_warn"):
                st.info(st.session_state.pop("sgo_msg_warn"))
        else:
            st.caption("Tip: add a free SPORTSGAMEODDS_KEY to .env to auto-fill "
                       "real prop odds. For now, type them in.")

        oc1, oc2 = st.columns(2)
        if "sgo_over" not in st.session_state:
            st.session_state["sgo_over"] = -110
        if "sgo_under" not in st.session_state:
            st.session_state["sgo_under"] = -110
        if "pending_over" in st.session_state:
            st.session_state["sgo_over"] = st.session_state.pop("pending_over")
        if "pending_under" in st.session_state:
            st.session_state["sgo_under"] = st.session_state.pop("pending_under")
        odds_over = oc1.number_input("Over odds", step=5, key="sgo_over")
        odds_under = oc2.number_input("Under odds", step=5, key="sgo_under")

        res = analyze(config, pid, prop_type, line, cutoff, hist, ctx)

        if res["projection"] is None:
            st.warning("Not enough prior games for this player to project.")
        else:
            # PICK comes from PROJECTION vs LINE — the single source of truth.
            # projected > line => OVER, always. p_over is a calibrated
            # probability that can shrink in magnitude but must NEVER flip the
            # side away from the projection (that caused UNDER badges on cards
            # projecting clearly OVER). Keep the shown % consistent with the
            # pick's side.
            edge = res["projection"] - line
            lean = "OVER" if edge >= 0 else "UNDER"
            # Probability for the pick's side, forced consistent: at least 50%
            # for the side the projection favors (a projection over the line
            # can't be a sub-coinflip to go over).
            _po = res["p_over"]
            if lean == "OVER":
                chance = _po if _po >= 0.5 else max(_po, 0.5)
            else:
                _pu = res["p_under"]
                chance = _pu if _pu >= 0.5 else max(_pu, 0.5)
            conf = res["confidence"].split("(")[0].strip()
            conf_word = {"solid": "Reasonable confidence",
                         "moderate": "Moderate confidence — volatile form",
                         "low": "Low confidence — small sample",
                         "none": "No prior games"}.get(conf, conf)
            venue_txt = "vs" if is_home else "@"
            pick_color = "#22c55e" if lean == "OVER" else "#ef4444"
            edge_txt = (f"+{edge:.1f} over the line" if edge >= 0
                        else f"{edge:.1f} under the line")
            pct = int(round(chance * 100))

            # Expected value for the side we lean, using the entered odds.
            # Judge value on the market-anchored probability: the model alone
            # hasn't beaten the posted line (see market_edge_test).
            _anch = anchor_to_market(res["p_over"], res["p_under"],
                                     int(odds_over), int(odds_under),
                                     p_over_raw=res.get("p_over_raw"),
                                     p_over_fitted=slate_p_over(sel, prop_type, line))
            ev = evaluate_prop(_anch["p_over"], _anch["p_under"],
                               int(odds_over), int(odds_under))
            ev_pick = ev["ev_over_pct"] if lean == "OVER" else ev["ev_under_pct"]
            ev_odds = odds_over if lean == "OVER" else odds_under
            if ev_pick is None:
                # Invalid/blank odds — prompt rather than show a fake EV.
                ev_bg = "#1b2130"
                ev_strip = (
                    f'<div style="background:{ev_bg}; padding:8px 20px; '
                    f'border-top:1px solid #2a3142; color:#93a1b5; '
                    f'font-size:.9rem;">Enter real odds above (e.g. -110) to '
                    f'check value.</div>')
            else:
                season_n = season_games_count(hist)
                early = season_n < MIN_SEASON_GAMES_FOR_VALUE
                # Plain-English money framing: EV% ~ profit per $100 over time.
                per100 = f"${abs(ev_pick):.0f} per $100 you'd bet"
                if ev["is_value"] and ev["recommended_side"] == lean.lower() \
                        and not early:
                    ev_bg = "#14321f"
                    ev_label = (f"✅ Good deal — over time you'd expect to make "
                                f"about {per100}")
                elif ev["is_value"] and early:
                    ev_bg = "#2a2410"
                    ev_label = (f"⏳ Looks good, but this player's only played "
                                f"{season_n} game"
                                f"{'s' if season_n != 1 else ''} this season — "
                                f"too early to trust")
                elif ev_pick > 0:
                    ev_bg = "#2a2410"
                    ev_label = (f"Small edge — maybe {per100} over time. Not much")
                else:
                    ev_bg = "#2a1414"
                    ev_label = (f"Bad deal — you'd expect to LOSE about {per100} "
                                f"over time. Skip it")
                ev_strip = (
                    f'<div style="background:{ev_bg}; padding:8px 20px; '
                    f'border-top:1px solid #2a3142; color:#e6e9ef; font-size:.9rem;">'
                    f'{ev_label}.</div>')
            if _anch["anchored"] and ev_pick is not None:
                ev_strip = ev_strip.replace(
                    ".</div>",
                    '. <span style="color:#93a1b5;font-size:.78rem;">'
                    "Blended with the market line: our model alone hasn't "
                    "beaten it yet.</span></div>", 1)

            # --- The bet slip ---
            bio = cached_bio(sel)
            if bio and bio.get("headshot"):
                sub = " · ".join(x for x in [bio.get("position"),
                                             bio.get("height"),
                                             bio.get("weight")] if x)
                head_html = (
                    f'<img src="{bio["headshot"]}" alt="" style="width:46px;'
                    f'height:46px;border-radius:50%;object-fit:cover;'
                    f'background:#0b0f17;margin-right:10px;" />')
                name_block = (
                    f'<div style="display:flex;align-items:center;">{head_html}'
                    f'<div><div style="font-size:1.15rem;font-weight:700;'
                    f'color:#e6e9ef;">{sel}</div>'
                    f'<div style="color:#6b7688;font-size:.72rem;">{sub}</div>'
                    f'</div></div>')
            else:
                name_block = (f'<div style="font-size:1.15rem;font-weight:700;'
                              f'color:#e6e9ef;">{sel}</div>')
            st.markdown(f"""
<div style="background:#141926; border:1px solid #2a3142; border-radius:14px;
            overflow:hidden; margin:6px 0 14px 0;
            box-shadow:0 4px 18px rgba(0,0,0,.35);">
  <div style="display:flex; justify-content:space-between; align-items:center;
              background:#1b2130; padding:12px 18px; border-bottom:1px solid #2a3142;">
    {name_block}
    <div style="color:#93a1b5; font-size:.9rem;">{my_team} {venue_txt} {opp}
         · {'Home' if is_home else 'Away'}</div>
  </div>
  <div style="display:flex; align-items:stretch;">
    <div style="flex:0 0 118px; background:{pick_color}; color:#0b0f17;
                display:flex; flex-direction:column; justify-content:center;
                align-items:center; padding:16px 8px;">
      <div style="font-size:1.5rem; font-weight:800; line-height:1;">{lean}</div>
      <div style="font-size:1.1rem; font-weight:700; margin-top:2px;">{line}</div>
    </div>
    <div style="flex:1; padding:14px 20px;">
      <div style="color:#93a1b5; font-size:.78rem; text-transform:uppercase;
                  letter-spacing:.05em;">{PROP_WORDS[prop_type]}</div>
      <div style="font-size:1.05rem; color:#e6e9ef; margin:2px 0 10px 0;">
        Projected <b>{res['projection']}</b>
        <span style="color:{pick_color};">({edge_txt})</span></div>
      <div style="display:flex; align-items:center; gap:10px;">
        <div style="flex:1; background:#0b0f17; border-radius:6px; height:10px;
                    overflow:hidden;">
          <div style="width:{pct}%; height:100%; background:{pick_color};"></div>
        </div>
        <div style="font-weight:700; color:#e6e9ef; font-size:.95rem;">{pct}%</div>
      </div>
      <div style="color:#93a1b5; font-size:.8rem; margin-top:8px;">
        {conf_word} · {res['games_played_before']} games of history</div>
    </div>
  </div>
  {ev_strip}
</div>
""", unsafe_allow_html=True)

            # --- Why you should (or shouldn't) take this prop ---------------
            _hr_for_why = hit_rate_windows(hist, prop_type, line)
            _why_season = int(cutoff[:4]) if cutoff else 2026
            if _why_season < 2024:
                _why_season = 2024
            _why_opp_weak = _opp_weakness_note(opp, _why_season, prop_type)
            _why_bullets, _why_caveat = _why_take_it(
                res, lean, edge, line, prop_type, opp, _hr_for_why,
                opp_weak=_why_opp_weak)
            with st.expander("💡 Why take this prop? — matchup check",
                             expanded=True):
                for _b in _why_bullets:
                    st.markdown("• " + _b)
                st.caption(_why_caveat)

            # --- Add this pick to MY SLIP (tracked live on the Live tab) ---
            _slip = st.session_state.setdefault("my_slip", [])
            _pick = {"player": sel, "stat": prop_type, "line": float(line),
                     "side": lean,
                     "odds": int(odds_over if lean == "OVER" else odds_under)}
            _already = any(p["player"] == _pick["player"]
                           and p["stat"] == _pick["stat"]
                           and p["side"] == _pick["side"] for p in _slip)
            bc1, bc2 = st.columns([1, 3])
            if _already:
                bc1.button("✓ On your slip", disabled=True, key="slip_add_btn")
            elif bc1.button("➕ Add to my slip", key="slip_add_btn"):
                _slip.append(_pick)
                st.session_state["my_slip"] = _slip
                st.rerun()
            bc2.caption(f"Your slip has {len(_slip)} pick"
                        f"{'s' if len(_slip) != 1 else ''} — track them live on "
                        "the 🔴 Live Tracker tab.")

            # --- Combo-stat note (now a real empirical win%) ---
            if res.get("combo_note"):
                st.caption("🧮 " + res["combo_note"].capitalize() + ".")

            # --- Hit-rate cards: how often this player beat THIS line lately ---
            hr = hit_rate_windows(hist, prop_type, line)
            if hr:
                st.markdown(
                    '<div style="color:#93a1b5;font-size:.8rem;'
                    'text-transform:uppercase;letter-spacing:.05em;'
                    'margin:2px 0 6px 2px;">Hit rate — times this player went '
                    f'OVER {line:g} {PROP_WORDS[prop_type].lower()}</div>',
                    unsafe_allow_html=True)
                cards = []
                for n, hits, pct, avg in hr:
                    p = int(round(pct * 100))
                    # Green when history agrees with the OVER lean (or UNDER
                    # with a low rate); grey when it's a coin flip.
                    if p >= 60:
                        col, foot = "#22c55e", "leans Over"
                    elif p <= 40:
                        col, foot = "#ef4444", "leans Under"
                    else:
                        col, foot = "#93a1b5", "coin flip"
                    cards.append(
                        f'<div style="flex:1;background:#141926;'
                        f'border:1px solid #2a3142;border-radius:12px;'
                        f'padding:12px 10px;text-align:center;">'
                        f'<div style="color:#6b7688;font-size:.72rem;'
                        f'text-transform:uppercase;letter-spacing:.05em;">'
                        f'Last {n}</div>'
                        f'<div style="font-size:1.6rem;font-weight:800;'
                        f'color:{col};line-height:1.1;margin:2px 0;">{p}%</div>'
                        f'<div style="color:#e6e9ef;font-size:.8rem;">'
                        f'{hits}/{n} over</div>'
                        f'<div style="color:#6b7688;font-size:.72rem;'
                        f'margin-top:2px;">avg {avg:.1f} · {foot}</div>'
                        f'</div>')
                st.markdown(
                    '<div style="display:flex;gap:10px;margin:0 0 14px 0;">'
                    + "".join(cards) + '</div>', unsafe_allow_html=True)
                st.caption("Past performance vs this exact line — not a "
                           "prediction. Small samples swing fast.")

            if st.button("📒 Log this prediction to my track record"):
                _logged = log_prediction(
                    pid, sel, cutoff, prop_type, line, res["projection"],
                    lean.lower(), round(chance, 4), opponent=opp,
                    model_name=config.name)
                if _logged:
                    st.success("Logged. It'll be graded automatically once "
                               "that game is in the data — see the Track "
                               "Record tab.")
                else:
                    st.info("Already logged. Your first entry for this exact "
                            "prop is kept, so the track record can't be "
                            "rewritten after the fact.")

            with st.expander("Why — factor breakdown"):
                st.write(f"Base projection (recent + season form): "
                         f"**{res['base_projection']}**. Your selected factors "
                         f"then adjusted it:")
                if res.get("base_desc"):
                    st.caption("📐 " + res["base_desc"])
                if res["adjustments"]:
                    for a_ in res["adjustments"]:
                        st.write("• " + _plain_reason(a_))
                else:
                    st.write("• No factor had enough signal to move it "
                             "(missing opponent/venue data, or neutral).")

            with st.expander("🆚 Matchup breakdown", expanded=True):
                _season_m = int(cutoff[:4]) if cutoff else 2026
                if _season_m < 2024:
                    _season_m = 2024
                _tr = goat_team_ratings(_season_m)
                _hd = _tr.get(my_team) or {}
                _ad = _tr.get(opp) or {}

                # 1) TEAM vs TEAM — ratings + pace head to head.
                if _hd.get("off") is not None and _ad.get("off") is not None:
                    st.markdown(
                        f"**Team vs team** — {my_team}: off "
                        f"{_hd.get('off','?')} / def {_hd.get('def','?')}  ·  "
                        f"{opp}: off {_ad.get('off','?')} / def "
                        f"{_ad.get('def','?')}. "
                        f"(Lower def rating = tougher D.)")

                # 1b) WHAT THE OPPONENT GIVES UP — paint, fast break, rim FG%.
                try:
                    _ddef = cached_team_style(_season_m, "general", "defense").get(opp) or {}
                    _drim = cached_team_style(_season_m, "tracking", "defense").get(opp) or {}
                except Exception:
                    _ddef, _drim = {}, {}
                _gu = []
                if _ddef.get("opp_pts_paint") is not None:
                    _gu.append(f"{_ddef['opp_pts_paint']:.0f} paint pts")
                if _ddef.get("opp_pts_fb") is not None:
                    _gu.append(f"{_ddef['opp_pts_fb']:.0f} fast-break pts")
                if _drim.get("def_rim_fg_pct") is not None:
                    _gu.append(f"{_drim['def_rim_fg_pct']*100:.0f}% at the rim")
                if _ddef.get("blk") is not None:
                    _gu.append(f"{_ddef['blk']:.1f} blocks")
                if _gu:
                    st.markdown(f"**{opp} allows:** " + " · ".join(_gu)
                                + ". (High paint/rim = soft inside — good for "
                                  "bigs; lots of fast-break pts = transition "
                                  "leaks.)")

                # 2) PLAYER vs TEAM — this player's own history vs this opponent.
                _vs = hist[hist["opponent"] == opp]
                _col = prop_type if prop_type in hist.columns else None
                if _col and len(_vs) >= 2:
                    _va = _vs[_col].mean()
                    _oa = hist[_col].mean()
                    _diff = _va - _oa
                    _lean = ("better" if _diff > 0.5 else "worse"
                             if _diff < -0.5 else "about the same")
                    st.markdown(
                        f"**{sel} vs {opp}** — averages **{_va:.1f}** "
                        f"{PROP_WORDS[prop_type].lower()} in {len(_vs)} career "
                        f"meetings vs {_oa:.1f} overall ({_lean} than usual "
                        f"against them).")
                elif _col:
                    st.caption(f"{sel} has faced {opp} fewer than 2 times on "
                               "record — no reliable head-to-head yet.")

                # 3) OPPONENT'S DEFENDER — who likely guards him.
                try:
                    _pd = primary_defender_note(opp, grp, _season_m)
                except Exception:
                    _pd = None
                if _pd:
                    a = _pd["adv"]
                    st.markdown(
                        f"**Likely defender:** {_pd['name']} ({opp}) allows "
                        f"~{a['matchup_pts']:.1f} pts to his man"
                        + (f", {a['matchup_fg_pct']*100:.0f}% FG"
                           if a.get('matchup_fg_pct') else "")
                        + ". (Likely by position — defenders switch.)")

                # 4) POSITION vs TEAM — what the opp allows to this position.
                if prop_type in ("points", "rebounds", "assists") and grp:
                    try:
                        _cell = (cached_pos_defense_raw(prop_type, cutoff)
                                 .get(opp) or {}).get(grp)
                    except Exception:
                        _cell = None
                    if _cell:
                        st.markdown(
                            f"**{opp} vs {grp}s:** allows **{_cell['allowed']:g} "
                            f"{PROP_WORDS[prop_type].lower()}/game** to {grp}s — "
                            f"#{_cell['rank']} of {_cell['of']} (#1 = most).")
                if prop_type == "steals":
                    try:
                        _o = cached_team_steals(cutoff).get(opp)
                    except Exception:
                        _o = None
                    if _o:
                        st.markdown(
                            f"**{opp} ball security:** gives up "
                            f"**{_o['allowed']:g} steals/game** — "
                            f"#{_o['allowed_rank']} of {_o['of']} (#1 = most "
                            "careless).")

            with st.expander("📊 Extra context (live GOAT data)"):
                cur_season = cutoff[:4] if cutoff else "2026"
                try:
                    cs = int(cur_season) if int(cur_season) >= 2024 else 2024
                except Exception:
                    cs = 2024
                st.markdown(f"**👤 The player — {sel}**")
                savg = goat_season_avg(pid, cs)
                if savg:
                    st.write(f"**Official {cs}-{str(cs+1)[-2:]} season averages** "
                             f"(balldontlie): {savg.get('pts','?')} pts, "
                             f"{savg.get('reb','?')} reb, {savg.get('ast','?')} ast, "
                             f"{savg.get('stl','?')} stl, {savg.get('blk','?')} blk "
                             f"in {savg.get('games_played','?')} games "
                             f"({savg.get('min','?')} min).")
                else:
                    st.caption("No official season-average row for this player/"
                               "season yet.")
                # Advanced efficiency (usage%, assist%, PIE) — how big a role the
                # player plays and how efficiently, beyond raw box-score totals.
                adv = goat_adv_avg(pid, cs)
                if adv and adv.get("usage") is not None:
                    # ONE plain line that actually helps a bet: role + where
                    # points come from. Everything else is tucked away.
                    _mix = []
                    if adv.get("pct_pts_paint") is not None:
                        _mix.append(f"{adv['pct_pts_paint']*100:.0f}% paint")
                    if adv.get("pct_pts_3pt") is not None:
                        _mix.append(f"{adv['pct_pts_3pt']*100:.0f}% threes")
                    if adv.get("pct_pts_ft") is not None:
                        _mix.append(f"{adv['pct_pts_ft']*100:.0f}% free throws")
                    _mix_txt = ("  ·  scores " + ", ".join(_mix)) if _mix else ""
                    st.write(
                        f"**Role:** usage **{adv['usage']*100:.0f}%** of the "
                        f"team's plays{_mix_txt}. Higher usage = more "
                        "shots/touches = a higher, steadier line.")

                    with st.container():
                        st.markdown("---")
                        st.caption("🔬 Deep stats")
                        rows = []
                        if adv.get("ts_pct") is not None:
                            rows.append(f"True shooting {adv['ts_pct']*100:.1f}%")
                        if adv.get("pct_pts") is not None:
                            rows.append(f"{adv['pct_pts']*100:.0f}% of team points")
                        if adv.get("pct_fga") is not None:
                            rows.append(f"{adv['pct_fga']*100:.0f}% of team shots")
                        if adv.get("touches") is not None:
                            rows.append(f"~{adv['touches']:.0f} touches/game")
                        if adv.get("passes") is not None:
                            rows.append(f"~{adv['passes']:.0f} passes/game")
                        if rows:
                            st.caption("Involvement: " + " · ".join(rows))
                        # Shot quality + attempt volume (noisy early season).
                        sq = []
                        if adv.get("uncontested_fga") is not None:
                            sq.append(f"{adv['uncontested_fga']:.1f} open shots")
                        if adv.get("contested_fga") is not None:
                            sq.append(f"{adv['contested_fga']:.1f} contested")
                        if sq:
                            st.caption("Shot volume/game: " + " · ".join(sq))
                        if adv.get("matchup_pts") is not None:
                            st.caption(f"On defense: allows ~"
                                       f"{adv['matchup_pts']:.1f} pts to his man.")
                        # Defense & hustle — who forces TOs, blocks, fouls, etc.
                        _dh = []
                        if adv.get("matchup_tov") is not None:
                            _dh.append(f"forces {adv['matchup_tov']:.1f} "
                                       "TOs/game on his man")
                        if adv.get("deflections") is not None:
                            _dh.append(f"{adv['deflections']:.1f} deflections")
                        if adv.get("contested_shots") is not None:
                            _dh.append(f"{adv['contested_shots']:.1f} contested "
                                       "shots")
                        if adv.get("charges_drawn"):
                            _dh.append(f"{adv['charges_drawn']:.1f} charges drawn")
                        if adv.get("box_outs") is not None:
                            _dh.append(f"{adv['box_outs']:.1f} box-outs")
                        if _dh:
                            st.caption("🛡️ Defense & hustle: " + " · ".join(_dh))
                        # Foul / drawn-foul context (useful for FT-driven props).
                        _f = []
                        if adv.get("fouls_drawn") is not None:
                            _f.append(f"draws {adv['fouls_drawn']:.1f} fouls/game")
                        if adv.get("screen_ast") is not None:
                            _f.append(f"{adv['screen_ast']:.1f} screen assists")
                        if _f:
                            st.caption("🤝 " + " · ".join(_f))

                st.divider()
                st.markdown(f"**🆚 The matchup — {opp}**")
                sm = goat_standings(cs)
                if sm and opp in sm:
                    o = sm[opp]
                    st.write(f"**{opp} record:** {o['wins']}-"
                             f"{o['losses']} "
                             f"(conf. rank {o.get('conf_rank','?')}).")
                # Defensive strength from OUR own data (points allowed vs league
                # average) — reliable, no external API.
                dsum = defense_summary(cutoff, opp)
                if dsum:
                    _p = dsum["pct"]
                    if _p <= -2:
                        _word = f"allows about {abs(_p):.0f}% FEWER than average — tougher"
                    elif _p >= 2:
                        _word = f"allows about {_p:.0f}% MORE than average — softer"
                    else:
                        _word = "right around league average"
                    st.write(f"**{opp} defense:** ranked **#{dsum['rank']} of "
                             f"{dsum['n']}** toughest — {_word}.")
                # Pace only (it's the one extra thing that matters: fast = more
                # possessions = more chances to hit an over).
                tr = goat_team_ratings(cs)
                if tr and opp in tr and tr[opp].get("pace") is not None:
                    _pace = tr[opp]["pace"]
                    _feel = ("fast — more possessions, helps overs" if _pace >= 101
                             else "slow — fewer possessions, helps unders"
                             if _pace <= 98 else "average pace")
                    st.caption(f"{opp} plays at **{_pace:g} pace** ({_feel}).")
                st.caption("Context only — the projection is built from the "
                           "player's own game log, no look-ahead.")

            with st.expander("💡 How the value (EV) was figured"):
                st.write(_plain_verdict(ev))
                st.write(f"Removing the sportsbook margin (**{ev['vig_pct']}%** "
                         f"vig), the fair no-vig probabilities are "
                         f"Over {ev['fair_over']}, Under {ev['fair_under']}.")
                st.write(f"Expected value per \\$1: Over "
                         f"**{ev['ev_over_pct']}%**, Under "
                         f"**{ev['ev_under_pct']}%** (positive = edge). Change "
                         f"the odds above the slip to re-check.")

            st.subheader("Game log")
            g1, g2 = st.columns([1, 1])
            n_games = g1.radio("How many games", [5, 10, 20], index=1,
                               horizontal=True)
            scope = g2.radio("Which games", ["Most recent",
                                             f"Vs {opp} only"], horizontal=True)
            if scope.startswith("Vs"):
                st.caption(
                    f"Only {sel}'s games against {opp}. Teams meet just a few "
                    "times a season (2-4), and those dates can cluster — so a "
                    "short list or a calendar-year gap is the NBA schedule, not "
                    "missing data. Use the Season column to read each game's "
                    "season; switch to **Most recent** for the full log.")

            log = hist.copy()
            # Only games the player actually played in (exclude DNPs / 0-min
            # scratches, which would otherwise clutter the log with empty rows).
            log = log[log["minutes"].fillna(0) > 0]
            if scope.startswith("Vs"):
                log = log[log["opponent"] == opp]
                if log.empty:
                    st.info(f"No games played against {opp} for {sel}.")
            cols_all = ["game_date", "season", "opponent", "is_home", "minutes",
                        "points", "rebounds", "assists", "threes_made",
                        "steals", "blocks", "turnovers", "fouls"]
            recent = log.tail(n_games)[cols_all].copy()
            if not recent.empty:
                recent["is_home"] = recent["is_home"].map(
                    {1: "Home", 0: "Away"}).fillna("?")
                # Season label: a game dated 2026-01-28 belongs to the 2025-26
                # season. Show "'25-26" so the date↔season offset is never
                # confusing (NBA seasons span two calendar years).
                def _season_lbl(s):
                    try:
                        y = int(s)
                        return f"'{y % 100:02d}-{(y + 1) % 100:02d}"
                    except Exception:
                        return ""
                recent["season"] = recent["season"].map(_season_lbl)
                # Add the combo columns for convenience.
                recent["PRA"] = (recent["points"] + recent["rebounds"]
                                 + recent["assists"])
                recent["PR"] = recent["points"] + recent["rebounds"]
                recent["PA"] = recent["points"] + recent["assists"]
                recent["RA"] = recent["rebounds"] + recent["assists"]
                recent = recent.rename(columns={
                    "game_date": "Date", "season": "Season",
                    "opponent": "Opp", "is_home": "Site",
                    "minutes": "Min", "points": "Pts", "rebounds": "Reb",
                    "assists": "Ast", "threes_made": "3PM", "steals": "Stl",
                    "blocks": "Blk", "turnovers": "TO", "fouls": "PF"})
                st.dataframe(
                    recent.iloc[::-1], use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Date": st.column_config.Column(help="Game date"),
                        "Season": st.column_config.Column(
                            help="NBA season this game belongs to (spans two "
                                 "calendar years, e.g. a Jan-2026 game is "
                                 "the '25-26 season)"),
                        "Opp": st.column_config.Column(help="Opponent team"),
                        "Site": st.column_config.Column(help="Home or Away"),
                        "Min": st.column_config.Column(help="Minutes played"),
                        "Pts": st.column_config.Column(help="Points"),
                        "Reb": st.column_config.Column(help="Rebounds"),
                        "Ast": st.column_config.Column(help="Assists"),
                        "3PM": st.column_config.Column(help="3-pointers made"),
                        "Stl": st.column_config.Column(help="Steals"),
                        "Blk": st.column_config.Column(help="Blocks"),
                        "TO": st.column_config.Column(help="Turnovers"),
                        "PF": st.column_config.Column(help="Personal fouls"),
                        "PRA": st.column_config.Column(
                            help="Points + Rebounds + Assists"),
                        "PR": st.column_config.Column(
                            help="Points + Rebounds"),
                        "PA": st.column_config.Column(
                            help="Points + Assists"),
                        "RA": st.column_config.Column(
                            help="Rebounds + Assists"),
                    })
                if scope.startswith("Vs") and len(recent) < 3:
                    st.caption(f"⚠️ Only {len(recent)} game(s) vs {opp} — too "
                               "small a sample to read much into.")

            st.subheader("Double-Double / Triple-Double")
            st.caption("These are yes/no outcomes (not Over/Under a number), so "
                       "they use an event-probability model: the player's recent "
                       "rate, shrunk for small samples. 10+ in 2 categories = "
                       "double-double; 3 = triple-double.")
            dd = milestone_probability(pid, "double_double", cutoff, hist)
            td = milestone_probability(pid, "triple_double", cutoff, hist)
            mc1, mc2 = st.columns(2)
            if dd["probability"] is not None:
                mc1.metric("Double-Double chance",
                           f"{dd['probability']*100:.0f}%",
                           help=f"Observed {dd['hits']}/{dd['games']} games. "
                                f"{dd['confidence']}.")
            if td["probability"] is not None:
                mc2.metric("Triple-Double chance",
                           f"{td['probability']*100:.1f}%",
                           help=f"Observed {td['hits']}/{td['games']} games. "
                                f"{td['confidence']}.")

    # ----------------------- Backtest ----------------------- #
    with tab2:
        st.subheader("Test it on the past")
        st.write("Pick a stat and a date range, hit the button, and the bot "
                 "re-plays those real games — predicting each one without "
                 "peeking at the result — then shows how it did. Think of it as "
                 "a report card on past games. (Pretend money, never real bets.)")

        b1, b2, b3 = st.columns(3)
        bt_prop = b1.selectbox("Stat", ["points", "rebounds", "assists",
                                        "steals", "blocks", "turnovers"],
                               format_func=lambda k: PROP_WORDS[k])
        start = b2.text_input("From date", "2020-10-01")
        end = b3.text_input("To date", date.today().isoformat())
        max_games = st.slider("How many games to test (more = slower)",
                              100, 1200, 400, step=100)

        if st.button("▶ Run the test"):
            with st.spinner("Re-playing past games..."):
                result = run_backtest(config, prop_type=bt_prop,
                                      start_date=start, end_date=end,
                                      max_games=max_games)
            m, base = result["model"], result["baseline"]
            if not m:
                st.warning("Not enough games to test — widen the date range.")
            else:
                import pandas as pd
                acc = m["accuracy"] * 100
                vs = (m["accuracy"] - base["accuracy"]) * 100
                roi = m["roi"] * 100

                # One plain-English verdict line up top.
                if m["roi"] > 0 and m["beat_break_even"]:
                    st.success(
                        f"✅ Over {result['games_evaluated']} past games, the bot "
                        f"picked the right side **{acc:.0f}% of the time** and "
                        f"would've turned a **{roi:+.0f}% pretend profit** — "
                        f"beating both a dumb guess and the sportsbook's cut. "
                        f"Encouraging, but remember: this used a stand-in line, "
                        f"not a real Vegas line, so it's a good sign — not a "
                        f"guarantee of beating a real sportsbook.")
                else:
                    st.info(
                        f"Over {result['games_evaluated']} games the bot was "
                        f"right {acc:.0f}% of the time and would've made "
                        f"{roi:+.0f}% pretend profit — not enough to clear the "
                        f"sportsbook's cut here. That's normal; try different "
                        f"factors on the left and test again.")

                # Three friendly numbers.
                k1, k2, k3 = st.columns(3)
                k1.metric("How often it was right", f"{acc:.0f}%",
                          f"{vs:+.0f}% vs a dumb guess")
                k2.metric("Pretend profit",
                          "Made money" if m["roi"] > 0 else "Lost money",
                          f"{roi:+.0f}%")
                k3.metric("Passed the sportsbook test?",
                          "Yes 👍" if m["beat_break_even"] else "Not yet")

                # Money-over-time chart, plain caption.
                st.markdown("**How the pretend money grew (or shrank)**")
                st.caption("Each step is one bet, in order. The worst dip along "
                           f"the way was about {m['max_drawdown']:.0f} bets down "
                           "from its best point — the rough patch you'd have had "
                           "to sit through.")
                if m.get("equity_curve"):
                    st.line_chart(pd.DataFrame({"Pretend profit": m["equity_curve"]}))

                # Season table, plain.
                if result.get("by_season"):
                    st.markdown("**Year by year** (did it work every season, or "
                                "just one lucky one?)")
                    sdf = pd.DataFrame([
                        {"Season": s, "Games": v["n_bets"],
                         "Right %": f"{v['accuracy']*100:.0f}%",
                         "Pretend profit": f"{v['roi']*100:+.0f}%"}
                        for s, v in result["by_season"].items()])
                    st.dataframe(sdf, use_container_width=True, hide_index=True)

                # Everything technical hidden behind one optional expander.
                with st.container():
                    st.markdown("---")
                    st.caption("🤓 Advanced details — for the curious, you "
                               "don't need these to use the bot.")
                    st.write(f"- **Brier score:** {m['brier']} "
                             f"(lower is better; baseline {base['brier']})")
                    st.write(f"- **Log loss:** {m['log_loss']} "
                             f"(lower is better; baseline {base['log_loss']})")
                    st.write(f"- **Break-even bar:** {m['break_even_rate']*100:.1f}% "
                             f"(you must beat this, not 50%, because of the "
                             f"sportsbook's cut)")
                    st.markdown("**Calibration** — when the bot says a chance, "
                                "does it really happen that often?")
                    cal = pd.DataFrame(m["calibration"])
                    if not cal.empty:
                        cal = cal.rename(columns={
                            "band": "Bot said about…", "n": "Times",
                            "predicted": "…this chance", "actual": "Really happened"})
                        st.dataframe(cal, use_container_width=True,
                                     hide_index=True)
                        st.caption("The last two columns should be close. If the "
                                   "bot says 90% but only 80% happens, it's a "
                                   "little overconfident.")

    # ----------------------- Track Record ----------------------- #
    with tab3:
        st.subheader("Prediction track record")
        st.write("Every prediction you log is saved and graded against the real "
                 "result once that game is in the data — a forward-looking "
                 "honesty check, separate from the backtest. This is paper "
                 "tracking: no real bets, no money.")

        cols = st.columns([1, 1, 2])
        if cols[0].button("Grade finished games"):
            n = grade_pending()
            st.success(f"Graded {n} prediction(s) whose games are now final.")

        summary = tracking_summary()
        acc = (f"{summary['accuracy']*100:.0f}%"
               if summary["accuracy"] is not None else "—")
        m1, m2, m3 = st.columns(3)
        m1.metric("Graded predictions", summary["graded"])
        m2.metric("Live accuracy", acc,
                  help="Hit rate on real games since you started logging. "
                       "Compare to the 52.4% break-even bar.")
        m3.metric("Still pending", summary["pending"],
                  help="Logged but the game hasn't been played / isn't in the "
                       "data yet.")
        if summary.get("void"):
            st.caption(f"{summary['void']} logged prediction(s) were voided "
                       "because the player didn't play (books refund those), "
                       "so they count as neither a hit nor a miss.")
        if summary.get("stale"):
            st.caption(f"⚠ {summary['stale']} pending prediction(s) are more "
                       "than 3 days old with no box score, so their game date "
                       "was probably wrong and they will never grade. Clear them "
                       "with src/models/clear_predictions.py.")

        if summary["recent"]:
            import pandas as pd
            df = pd.DataFrame(summary["recent"])
            df["correct"] = df["correct"].map({1: "✅", 0: "❌"}).fillna("—")
            df["prob"] = (df["prob"] * 100).round(0).astype("Int64").astype(str) + "%"
            df = df.rename(columns={
                "player_name": "Player", "game_date": "Game",
                "prop_type": "Prop", "line": "Line", "pick": "Pick",
                "prob": "Our prob", "result": "Actual", "correct": "Hit?"})
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No graded predictions yet. Analyze props and log them "
                    "(from the Analyze tab), then grade once their games finish.")

        # ---- Forward test: bets the two frozen rules would have made ---------
        st.markdown("---")
        st.markdown("#### 🧪 Paper-trading record (forward test)")
        st.caption("Every time the intraday job runs it logs the bets two fixed "
                   "rules would make at the price available right then, then "
                   "grades them after the game (players who don't play are "
                   "voided). This can't be backfilled or edited, so it's the "
                   "honest test of whether any 'value' flag is real.")
        try:
            from models.paper_bets import summary as _pb_summary
            _pb = _pb_summary()
        except Exception:
            _pb = []
        if not _pb:
            st.info("Nothing logged yet. Run src/collect/intraday.py on a game "
                    "day (see the README) and bets will appear here.")
        else:
            _names = {"model_only_v1": "Model only (the old value flags)",
                      "anchor_shop_v1": "Market-anchored + best price"}

            def _pct(x, signed=False):
                if x is None:
                    return "—"
                return f"{100 * x:+.1f}%" if signed else f"{100 * x:.1f}%"
            _tbl = [{
                "Rule": _names.get(s["rule"], s["rule"]),
                "Logged": s["logged"], "Settled": s["settled"], "Void": s["void"],
                "Hit rate": _pct(s["hit"]),
                "ROI per bet": (_pct(s["roi"], True) + (
                    f" ±{100 * s['roi_ci95']:.1f}" if s["roi_ci95"] is not None else "")),
                "Beat the closing price": _pct(s["pct_beat_close"]),
                "Avg closing-line value": _pct(s["avg_clv_prob"], True),
            } for s in _pb]
            import pandas as pd
            st.dataframe(pd.DataFrame(_tbl), use_container_width=True,
                         hide_index=True)
            st.caption("ROI is per 1-unit bet with a 95% interval; until the "
                       "interval clears zero over a few hundred settled bets, "
                       "treat it as luck. 'Beat the closing price' is the faster "
                       "signal: a real edge gets a better price than the final one.")

    # ----------------------- Value Scanner ----------------------- #
    with tab4:
        st.subheader("Today's value board")
        st.write("Runs **every player in today's games** through your model, "
                 "compares our projection to the **real sportsbook line**, and "
                 "ranks the bets with the biggest edge. A ✅ means the odds give "
                 "you more than the risk — based on our estimate, not a promise.")

        if not sgo_has_key():
            st.warning("Add a free **SPORTSGAMEODDS_KEY** to your `.env` to scan "
                       "real odds. Without it there are no live lines to compare "
                       "against.")
        elif not config:
            st.info("👈 Pick at least one factor in the sidebar first — the "
                    "scanner uses the same model you build there.")
        else:
            sc1, sc2 = st.columns([2, 1])
            scan_stats = sc1.multiselect(
                "Stats to scan", list(PROP_WORDS.keys()),
                default=["points", "rebounds", "assists"],
                format_func=lambda k: PROP_WORDS[k],
                help="More stats = a longer scan. Start with the big three.")
            min_edge = sc2.slider(
                "Min edge to flag ✅", 1, 10, 3,
                help="How much better than the fair line our number must be "
                     "before we call it value. 3% is a sensible default.")
            top_n = st.radio(
                "Show top", [5, 10, 20], index=0, horizontal=True,
                help="How many of the highest-EV plays to list.")

            def _run_scan():
                """Run the scan ONCE (no blocking sleep) and store results.

                The old auto-retry used time.sleep(), which froze the whole app
                for a minute and often still hit the limit. Instead we scan once
                and, if rate-limited, surface a 'Try again' button — responsive,
                honest, and the user stays in control."""
                with st.spinner("Scanning today's players against live odds…"):
                    rows, note = scan_today_value(
                        config, scan_stats, min_edge / 100.0, 300)
                st.session_state["scan_rows"] = rows
                st.session_state["scan_note"] = note
                st.session_state["scan_sig"] = (tuple(scan_stats), min_edge)

            # Auto-load: scan when the tab opens with settings we haven't run yet.
            # The 5-min odds cache means re-opening the tab is usually free.
            sig = (tuple(scan_stats), min_edge)
            refresh = st.button("🔄 Refresh now", key="scanner_refresh")
            if (refresh
                    or "scan_rows" not in st.session_state
                    or st.session_state.get("scan_sig") != sig):
                _run_scan()

            rows = st.session_state.get("scan_rows")
            note = st.session_state.get("scan_note", "")
            # If rate-limited, offer a non-blocking retry instead of freezing.
            if "rate limit" in note.lower():
                st.warning(note + "  The free tier allows 10 pulls per minute; "
                           "it clears within a minute.")
                if st.button("🔁 Try the scan again"):
                    _run_scan()
                    st.rerun()
            if rows is not None and "rate limit" not in note.lower():
                st.caption(note)
                if rows:
                    import pandas as pd
                    value_rows = [r for r in rows if r["Value?"] == "✅"]
                    base = value_rows if value_rows else rows
                    if value_rows:
                        st.markdown(f"**{len(value_rows)} value bet(s)** found.")
                    else:
                        st.markdown(
                            "**No ✅ value bets yet** (early season — the model "
                            "holds the stamp until a player has 5+ games this "
                            "season). Showing the plays the model likes most by "
                            "EV, just not yet trustworthy enough to flag.")

                    view = st.radio("View", ["Ranked list", "By game"],
                                    index=0, horizontal=True)

                    def _fmt(df):
                        df = df.copy()
                        df["EV %"] = df["EV %"].map(lambda v: f"{v:+.1f}%")
                        df["Win %"] = df["Win %"].astype(str) + "%"
                        df["Odds"] = df["Odds"].map(lambda v: f"{int(v):+d}")
                        if "_pid" in df.columns:
                            df = df.drop(columns=["_pid"])
                        return df

                    # Enrich ONLY the rows we're about to show with shot-attempt
                    # volume (one cached adv-stats call per shown player — the
                    # scan itself stays fast by not doing this for all ~65).
                    def _with_attempts(rows_in):
                        import datetime as _dt2
                        out = []
                        _yr = _dt2.date.today().year if _dt2.date.today().month >= 10 \
                            else _dt2.date.today().year - 1
                        for r in rows_in:
                            r2 = dict(r)
                            pid = r.get("_pid")
                            att = ""
                            if pid is not None:
                                a = goat_adv_avg(pid, _yr if _yr >= 2024 else 2024)
                                u, c = a.get("uncontested_fga"), a.get("contested_fga")
                                if u is not None or c is not None:
                                    att = (f"{(u or 0)+(c or 0):.0f} FGA "
                                           f"({(u or 0):.0f}u/{(c or 0):.0f}c)")
                                    if a.get("passes") is not None:
                                        att += f", {a['passes']:.0f} pass"
                            r2["Shots/Passes"] = att
                            out.append(r2)
                        return out

                    if view == "Ranked list":
                        top = _with_attempts(base[:top_n])
                        st.markdown(f"Top {len(top)} by EV:")
                        st.dataframe(_fmt(pd.DataFrame(top)),
                                     use_container_width=True, hide_index=True,
                                     column_config={
                                         "Why": st.column_config.Column(
                                             "Why take it", width="large",
                                             help="Projection vs line, biggest "
                                                  "factor, opponent weakness, "
                                                  "and whether recent history "
                                                  "agrees.")})
                    else:
                        # Group by GAME. A player's Matchup is team-relative
                        # ("DEN @ OKC" vs "OKC vs DEN" for the same game), so key
                        # by the sorted team pair to merge both sides into one.
                        per_game = 3
                        import re as _re
                        games = {}
                        labels = {}
                        for r in base:
                            teams = _re.split(r"\s+(?:@|vs\.?)\s+",
                                              r["Matchup"].strip())
                            key = " / ".join(sorted(t.strip() for t in teams
                                                    if t.strip()))
                            games.setdefault(key, []).append(r)
                            labels.setdefault(key, key.replace(" / ", " vs "))
                        st.caption(f"Each game's top {per_game} plays by EV. "
                                   f"{len(games)} game(s) on the board.")
                        for key in sorted(games):
                            # Rank this game's plays by EV, then take the top N.
                            grows = sorted(games[key],
                                           key=lambda x: -x["EV %"])[:per_game]
                            grows = _with_attempts(grows)
                            st.markdown(f"**🏀 {labels[key]}**")
                            show = [{k: v for k, v in r.items()
                                     if k != "Matchup"} for r in grows]
                            st.dataframe(_fmt(pd.DataFrame(show)),
                                         use_container_width=True,
                                         hide_index=True,
                                         column_config={
                                             "Why": st.column_config.Column(
                                                 "Why take it", width="large")})
                    st.caption("Ranked by EV (best first). 'Value?' ✅ = edge "
                               "clears your bar. 'Shots/Passes' = that player's "
                               "per-game shot attempts (u=uncontested, "
                               "c=contested) and passes. Always sanity-check a "
                               "pick on the Analyze tab before trusting it.")
                else:
                    st.info("No priced props to evaluate right now — check back "
                            "closer to tip-off when sportsbooks post lines.")

    # ----------------------- Game Lines: ML + Spread ----------------------- #
    with tab5:
        st.subheader("Game lines — moneyline & spread")
        st.write("Bet the **game itself**, not just player props. We project "
                 "each game's winner and margin from team ratings + home court, "
                 "compare it to the **real sportsbook line**, and flag where the "
                 "odds give you an edge. Same honest EV math as the prop tools.")

        from models.game_model import project_game, cover_probability
        # Reload game_model so a module cached from before the profiles were
        # added (stale Streamlit process) refreshes to the current version —
        # otherwise the selector can show only the fallback's single profile.
        try:
            import importlib
            import models.game_model as _gm
            importlib.reload(_gm)
            GAME_PROFILES = _gm.GAME_PROFILES
            DEFAULT_PROFILE = _gm.DEFAULT_PROFILE
            GAME_PROFILE_DESC = getattr(_gm, "GAME_PROFILE_DESC", {})
            project_game = _gm.project_game
            cover_probability = _gm.cover_probability
        except Exception:
            # Last-resort fallback so the tab still renders.
            GAME_PROFILES = {"Balanced": {"home_edge": 2.5, "margin_std": 12.0,
                                          "net_scale": 1.0, "pace_gain": 1.0}}
            DEFAULT_PROFILE = "Balanced"
            GAME_PROFILE_DESC = {}
        import datetime as _d5
        from database import nba_today

        gl_date = st.date_input("Game date",
                                _d5.date.fromisoformat(nba_today()),
                                help="Defaults to tonight's NBA slate (US "
                                     "Eastern). Lines appear closer to tip-off."
                                ).isoformat()
        if st.button("🔄 Refresh games"):
            # Clear the 15-min caches so a stale earlier pull (fewer games)
            # can't keep showing — forces a fresh fetch of lines + schedule.
            cached_game_lines.clear()
            cached_schedule.clear()
            st.rerun()
        lines = cached_game_lines(gl_date)
        # Blend THIS season with LAST season for the game model. Early in a new
        # season this year's sample is tiny, so we lean on last year and shift
        # toward current as games accrue (see blended_team_ratings).
        try:
            _gyr = int(gl_date[:4])
            _season = _gyr if int(gl_date[5:7]) >= 10 else _gyr - 1
        except Exception:
            _season = 2025
        ratings = blended_team_ratings(_season)
        # Team strength source. The API team-ratings feed is unreliable early
        # in a season (compressed/flat numbers, missing teams), which made a
        # loaded team like PHI read as merely average. The DB-first, TRADE-AWARE
        # roster strength (current roster x player advanced stats: PIE, def
        # rating) is far more trustworthy now, so it is the PRIMARY source; the
        # API feed only fills teams the roster map somehow lacks. Zero API,
        # ~90ms for all 30 teams.
        try:
            from models.roster_strength_db import team_strength_map
            _dbm = team_strength_map(_season)
            if _dbm:
                merged = dict(ratings or {})          # API feed as the base
                for _ab, _v in _dbm.items():          # roster strength WINS
                    merged[_ab] = {"off": _v["off"], "def": _v["def"],
                                   "pace": _v.get("pace", 100.0)}
                ratings = merged
        except Exception:
            pass
        team_inj = cached_team_injuries()  # {team: [(name, status, ppg), ...]}
        _gl_four_factors = cached_four_factors(_season)  # eFG/TOV/OREB per team
        # Recent form + rest for every team with a line tonight — feeds the game
        # model so projections build on recent play, not just season ratings.
        _gl_cut = (gl_date.isoformat() if hasattr(gl_date, "isoformat")
                   else str(gl_date))
        _gl_team_form = cached_team_form(tuple(sorted((lines or {}).keys())),
                                         _gl_cut)
        # Play-type matchup map: each key play type's team table + def ratings,
        # so the game model can value post-ups/PnR/iso/handoff/spot-up vs the
        # opponent's defense. Cached hourly inside cached_team_style.
        _gl_playtype = {
            "postup": cached_team_style(_season, "playtype", "postup"),
            "prballhandler": cached_team_style(_season, "playtype", "prballhandler"),
            "isolation": cached_team_style(_season, "playtype", "isolation"),
            "handoff": cached_team_style(_season, "playtype", "handoff"),
            "spotup": cached_team_style(_season, "playtype", "spotup"),
            "def": {t: (v.get("def") if isinstance(v, dict) else None)
                    for t, v in (ratings or {}).items()},
        }
        _gl_open_odds = cached_opening_odds(gl_date)  # {game_id: opening lines}
        # Custom positional-defense map {stat: {team: {group: factor}}} — the
        # SAME leak-free stat the prop model uses, now fed into game lines too.
        _gl_positional = {s: positional_map(s, _gl_cut)
                          for s in ("points", "rebounds", "assists")}
        # Games played this season so far — the market anchor leans on the book
        # when this is small and shifts to our model as it grows.
        try:
            _gconn = get_connection()
            _gl_games_played = _gconn.execute(
                "SELECT COUNT(DISTINCT game_id) FROM player_game_logs "
                "WHERE season = ?", (str(_season),)).fetchone()[0] or 0
            _gconn.close()
        except Exception:
            _gl_games_played = 0
        # Per-team home-court edges from real home/road splits (cached).
        try:
            from models.team_form import home_edge_map
            _gl_home_edges = home_edge_map(_season)
        except Exception:
            _gl_home_edges = {}
        try:
            from models.team_form import record_strength_map
            _gl_record = record_strength_map(_season)
        except Exception:
            _gl_record = {}
        try:
            from collect.goat_data import team_api_net_map
            _gl_team_api = team_api_net_map(_season)
        except Exception:
            _gl_team_api = {}
        _gl_iso = cached_team_style(_season, "playtype", "isolation")
        _gl_drives = cached_team_style(_season, "tracking", "drives")

        if not lines:
            st.info("No game lines posted for this date yet. Sportsbook game "
                    "odds usually appear within a few hours of tip-off — check "
                    "back closer to game time.")
        elif not ratings:
            st.warning("Team ratings aren't available right now, so we can't "
                       "project the games. Try again shortly.")
        else:
            # One row per GAME (not per team): use the home entries.
            games = sorted({v["matchup"] for v in lines.values()})
            sched = cached_schedule(gl_date)  # {matchup: status}
            scores = cached_scores(gl_date)   # {matchup: {home,away,final}}
            total_sched = len(sched)
            open_games = [m for m in games if _game_not_started(sched.get(m, ""))]
            live_games = [m for m in games if m not in open_games]

            # Plain-language headline: lead with how many games are SHOWN so it
            # never looks like fewer are displayed, then the open/live split.
            if total_sched:
                msg = (f"**Showing {len(games)} game"
                       f"{'s' if len(games) != 1 else ''}** on {gl_date}"
                       f" — {len(open_games)} open (not tipped off), "
                       f"{len(live_games)} live/final.")
                if total_sched > len(games):
                    msg += (f" {total_sched - len(games)} more scheduled with no "
                            "line posted yet.")
                st.caption(msg + " 🔴 LIVE = pre-game line only (can't bet once "
                           "tipped). ✅ means our estimate beats the price.")
            else:
                st.caption(f"{len(games)} game(s) with lines on {gl_date}. "
                           "Our model vs the market — ✅ means our estimate "
                           "beats the price.")
            ev_bar = st.slider("Min edge to flag ✅ (game bets)", 1, 10, 3,
                               help="How much better than the fair line our "
                                    "number must be before we call it value.")
            prof_name = st.radio(
                "Model profile", list(GAME_PROFILES.keys()),
                index=list(GAME_PROFILES).index(DEFAULT_PROFILE),
                horizontal=True,
                help="Switch how the game model thinks. Balanced is the "
                     "default; others trust the ratings more/less, add home "
                     "court, or react harder to pace. The picks below update "
                     "to match.")
            gprofile = GAME_PROFILES[prof_name]
            _mstd = gprofile["margin_std"]
            _desc = GAME_PROFILE_DESC.get(prof_name)
            if _desc:
                st.caption(f"**{prof_name}:** {_desc}")

            # ---- Day-by-day profile success rate (builds over the season) ----
            with st.expander("📈 Moneyline track record — profile winner "
                             "accuracy (builds day by day)"):
                board, _sb_err = [], None
                try:
                    from models.profile_scoreboard import compute_live_scoreboard
                    board = compute_live_scoreboard(_season)
                except Exception as _e:
                    _sb_err = str(_e)
                if board:
                    import pandas as _pd
                    # Rank purely by WIN RATE — the user tracks wins, not
                    # calibration. Best = highest win%, ties broken by more
                    # games (bigger sample). Rows with no games fall to bottom.
                    def _winpct(r):
                        _c, _t = r[1], r[2]
                        return (_c / _t) if _t else -1.0
                    _best = max(board, key=lambda r: (_winpct(r), r[2]))[0] \
                        if board else None
                    _board = sorted(board, key=lambda r: (_winpct(r), r[2]),
                                    reverse=True)
                    sb = _pd.DataFrame(
                        [{"Profile": ("⭐ " + n if n == _best else n),
                          "Record": f"{c}-{t-c}",
                          "Win %": (f"{round(c/t*100)}%" if t else "—"),
                          "Games": t}
                         for n, c, t, pct, br in _board])
                    st.dataframe(sb, use_container_width=True, hide_index=True)
                    _best_txt = (f"⭐ **{_best}** has the best win rate so far. "
                                 if _best else "")
                    st.caption(
                        "Ranked by **win %** — straight-up winners picked "
                        "right, most wins on top. " + _best_txt
                        + "With a few dozen games the gaps between profiles are "
                          "noise (they mostly pick the same winners), and this "
                          "table re-grades with today's ratings. Judge them by "
                          "the frozen-picks table below, which includes simple "
                          "baselines.")
                    # Export: per-game, per-preset picks so you can see exactly
                    # what each profile selected and whether it hit.
                    try:
                        from models.profile_scoreboard import scoreboard_detail
                        _det = scoreboard_detail(_season)
                        if _det:
                            _csv = _pd.DataFrame(_det).to_csv(index=False)
                            st.download_button(
                                "⬇️ Export moneyline picks (CSV)", _csv,
                                file_name=f"moneyline_picks_{_season}.csv",
                                mime="text/csv",
                                key="dl_ml_picks")
                    except Exception as _de:
                        st.caption(f"(Export unavailable: {_de})")
                elif _sb_err:
                    st.caption(f"Couldn't grade yet: {_sb_err}")
                else:
                    st.caption("No finished games in the feed yet — fills in as "
                               "games go final.")

            # ---- Frozen picks: the honest moneyline test ---------------------- #
            with st.expander("🧊 Frozen picks — locked before tip, with baselines"):
                try:
                    import pandas as _pdf
                    from models.profile_scoreboard import (get_forward_scoreboard,
                                                           forward_pending_count)
                    _fw = get_forward_scoreboard()
                    if _fw:
                        st.dataframe(_pdf.DataFrame(
                            [{"Pick source": n, "Record": f"{c}-{t-c}",
                              "Win %": (f"{round(c / t * 100)}%" if t else "—"),
                              "Games": t,
                              "Brier (lower is better)": (round(b, 3) if b is not None else "—")}
                             for n, c, t, pct, b in _fw]),
                            use_container_width=True, hide_index=True)
                    else:
                        st.caption("Nothing graded yet. Picks are locked when you "
                                   "open this tab for a game that hasn't started, "
                                   "and graded once it goes final.")
                    st.caption(
                        f"{forward_pending_count()} game(s) locked and waiting for "
                        "a final. Read each profile against the baselines: on "
                        "1,558 held-out past games, always-home won 55% and Elo "
                        "(results only) won 68%. A profile that can't beat Elo "
                        "here isn't adding anything. Needs a few hundred games "
                        "before the gaps mean anything.")
                except Exception as _fe:
                    st.caption(f"(Frozen picks unavailable: {_fe})")

            # ---- Separate SPREAD (ATS) track record -------------------- #
            with st.expander("🎯 Spread track record — ATS (our pick vs the "
                             "book's line)"):
                try:
                    from models.spread_scoreboard import (grade_finished,
                                                          get_record,
                                                          get_detail,
                                                          pending_count)
                    from models.profile_scoreboard import _finished_games_live
                    # Grade any captured games that are now final. Re-key the
                    # finished feed to our matchup|date ids so captures match.
                    _fin = _finished_games_live(30)
                    _fin_keyed = [(f"{a} @ {h}|{gd}", gd, h, a, hp, ap)
                                  for (gid, gd, h, a, hp, ap) in _fin]
                    grade_finished(_fin_keyed)
                    _c, _w, _p, _pct = get_record()
                    _decided = _c + _w
                    if _decided:
                        st.metric("ATS record (our side covered)",
                                  f"{_c}-{_w}"
                                  + (f"-{_p} push" if _p else ""),
                                  f"{_pct*100:.0f}% vs 52.4% break-even")
                        st.caption("This is the REAL edge test: our projected "
                                   "margin vs the book's posted spread — did "
                                   "our side cover? Beating 52.4% over a big "
                                   "sample is a genuine edge; a small sample is "
                                   "noise.")
                    else:
                        st.caption(f"No graded spread picks yet — {pending_count()}"
                                   " game(s) captured and waiting to go final. "
                                   "This board is forward-only (we don't store "
                                   "old spreads, so we never fake a backfill); "
                                   "it fills in as tonight's games finish.")
                    # Export: every ATS pick (graded + pending) — what the model
                    # selected vs the book's line, with the result.
                    _sdet = get_detail()
                    if _sdet:
                        import pandas as _pd2
                        _scsv = _pd2.DataFrame(_sdet).to_csv(index=False)
                        st.download_button(
                            "⬇️ Export ATS picks (CSV)", _scsv,
                            file_name="spread_ats_picks.csv",
                            mime="text/csv", key="dl_ats_picks")
                except Exception as _e:
                    st.caption(f"Spread board unavailable: {_e}")

            # Show open (bettable) games first, then live/final ones.
            ordered = open_games + live_games
            for matchup in ordered:
                # matchup is "AWAY @ HOME"
                try:
                    away, home = [t.strip() for t in matchup.split("@")]
                except ValueError:
                    continue
                status = sched.get(matchup, "")
                is_open = matchup in open_games
                if is_open:
                    tag = "🟢 OPEN"
                elif status.strip().lower().startswith("final"):
                    tag = "⚫ FINAL"
                else:
                    tag = f"🔴 LIVE ({status})" if status else "🔴 LIVE"
                g = lines.get(home) or {}
                # Early-season market anchor: de-vig the book's two-way ML into
                # an implied home win prob, and count games played so the model
                # leans on the market now and on itself as the season fills in.
                _mkt_ph = None
                try:
                    _mlh = g.get("moneyline")
                    _mla = (lines.get(away) or {}).get("moneyline")
                    if _mlh is not None and _mla is not None:
                        def _imp0(o):
                            o = int(o)
                            return (100.0/(o+100.0) if o > 0 else -o/(-o+100.0))
                        _ihh, _iaa = _imp0(_mlh), _imp0(_mla)
                        if _ihh + _iaa > 0:
                            _mkt_ph = _ihh / (_ihh + _iaa)
                except Exception:
                    _mkt_ph = None
                try:
                    _games_played = _gl_games_played
                except NameError:
                    _games_played = 0
                # Ratings projection: V2-advanced-driven roster strength +
                # custom positional stat, four factors, form, playtype, injuries.
                try:
                    proj = project_game(home, away, ratings, gprofile, team_inj,
                                        four_factors=_gl_four_factors,
                                        team_form=_gl_team_form,
                                        playtype=_gl_playtype,
                                        positional=_gl_positional,
                                        market_p_home=_mkt_ph,
                                        games_played=_games_played,
                                        home_edges=_gl_home_edges,
                                        record_strength=_gl_record,
                                        team_api=_gl_team_api)
                except TypeError:
                    try:
                        proj = project_game(home, away, ratings, gprofile, team_inj,
                                            four_factors=_gl_four_factors)
                    except TypeError:
                        try:
                            proj = project_game(home, away, ratings, gprofile, team_inj)
                        except TypeError:
                            proj = project_game(home, away, ratings)  # old sig

                # ---- ATS capture: store our spread pick on OPEN games so the
                # separate spread track record can grade it after the final.
                # Keyed by matchup+date (no game_id in the lines map). Honest &
                # forward-only — we never backfill a made-up spread.
                if proj and is_open and g.get("spread") is not None:
                    try:
                        from models.spread_scoreboard import capture_open_game
                        capture_open_game(f"{matchup}|{gl_date}", home, away,
                                          g.get("spread"), proj["exp_margin"])
                    except Exception:
                        pass
                # ---- Market-prob snapshot: store the de-vigged book prob at
                # bet-time so the moneyline backtest can replay the SAME market
                # anchor the live card used (forward-only; first capture wins).
                if proj and is_open and _mkt_ph is not None:
                    try:
                        from models.profile_scoreboard import capture_market_prob
                        capture_market_prob(str(gl_date), home, away,
                                            _mkt_ph, _games_played)
                    except Exception:
                        pass
                # ---- Frozen picks: lock EVERY profile's pick (and the simple
                # baselines) before tip, so the scoreboard can't be flattered by
                # re-grading with ratings that already include the result.
                if proj and is_open:
                    try:
                        from models.profile_scoreboard import capture_forward_picks
                        capture_forward_picks(
                            str(gl_date), home, away, ratings,
                            dict(injuries=team_inj, four_factors=_gl_four_factors,
                                 team_form=_gl_team_form, playtype=_gl_playtype,
                                 positional=_gl_positional, market_p_home=_mkt_ph,
                                 games_played=_games_played,
                                 home_edges=_gl_home_edges,
                                 record_strength=_gl_record, team_api=_gl_team_api),
                            market_p_home=_mkt_ph)
                    except Exception:
                        pass

                # ---- QUICK PICK for the collapsed title: who we'd back ----
                # Only on OPEN games — a pick on a live/final game isn't
                # bettable, so we don't flash a ✅ you can't act on.
                quick = ""
                pick_block = ""
                if proj and is_open:
                    ml_h = g.get("moneyline")
                    ml_a = (lines.get(away) or {}).get("moneyline")
                    sp_h = g.get("spread")
                    ml_pick = None    # moneyline value pick (or None)
                    sp_pick = None    # spread value pick (or None)
                    if ml_h is not None and ml_a is not None:
                        _ev = evaluate_prop(proj["p_home_win"], proj["p_away_win"],
                                            int(ml_h), int(ml_a),
                                            edge_threshold=ev_bar / 100.0)
                        if _ev.get("is_value"):
                            _ml_side = (home if _ev["recommended_side"] == "over"
                                        else away)
                            # The model's actual favorite (by projected margin).
                            _fav = home if proj["exp_margin"] > 0 else away
                            # A value bet on the model's UNDERDOG is a price/EV
                            # edge, NOT a prediction they win — label it so.
                            if _ml_side == _fav:
                                ml_pick = f"{_ml_side} to win"
                            else:
                                _ml_odds = ml_a if _ml_side == away else ml_h
                                ml_pick = (f"{_ml_side} {int(_ml_odds):+d} "
                                           f"(underdog value)")
                    if sp_h is not None:
                        _ph, _pa = cover_probability(proj["exp_margin"], sp_h, _mstd)
                        _evs = evaluate_prop(_ph, _pa, -110, -110,
                                             edge_threshold=ev_bar / 100.0)
                        if _evs.get("is_value"):
                            sp_pick = (f"{home} {sp_h:+g}"
                                       if _evs["recommended_side"] == "over"
                                       else f"{away} {-sp_h:+g}")
                    # Build two CLEARLY SEPARATE lines so ML and Spread never
                    # blur together. Each shows its own bet type or 'no edge'.
                    _fav = home if proj["exp_margin"] > 0 else away
                    # ---- Market-disagreement check FIRST ----------------
                    # If our win prob is far from the book's implied prob, the
                    # "value" is almost certainly OUR thin-data error, not a
                    # real edge. In that case we SUPPRESS the ✅ (don't show a
                    # green check we're telling you to ignore) and explain why.
                    _mkt_gap = None
                    if ml_h is not None and ml_a is not None:
                        def _implied(o):
                            o = int(o)
                            return (100.0 / (o + 100.0) if o > 0
                                    else -o / (-o + 100.0))
                        ih, ia = _implied(ml_h), _implied(ml_a)
                        _tot = ih + ia
                        if _tot > 0:
                            _book_home = ih / _tot
                            _mkt_gap = abs(proj["p_home_win"] - _book_home) * 100
                    _suppress = _mkt_gap is not None and _mkt_gap >= 12

                    if _suppress:
                        # Don't flash ✅ we distrust — show the model lean, muted.
                        quick = " · ⚠️ low confidence"
                        pick_block = (
                            f"⚠️ **Skip — model disagrees with the market by "
                            f"{_mkt_gap:.0f} pts** (we say {home} "
                            f"{proj['p_home_win']*100:.0f}%, the book says "
                            f"~{_book_home*100:.0f}%). Early-season our data is "
                            f"thin, so a gap this big is our miss, not an edge. "
                            f"The model leans **{_fav}**, but there's no "
                            f"trustworthy bet here yet.")
                    else:
                        _any = ml_pick or sp_pick
                        quick = " · ✅ value" if _any else " · no edge"
                        # Informational badge: model favors the OPPOSITE side
                        # from the book, but the gap is small (not suppressed).
                        # We flag it so you know it's a contrarian coin-flip —
                        # the pick is NOT changed.
                        _disagree_note = ""
                        if _mkt_gap is not None and _book_home is not None:
                            _book_fav = home if _book_home >= 0.5 else away
                            if _book_fav != _fav:
                                _disagree_note = (
                                    f"  \n⚠️ **Heads up — disagrees with the "
                                    f"market:** the book leans **{_book_fav}**, "
                                    f"we lean **{_fav}** (close call). Early "
                                    f"season, lean to the market if unsure.")
                                quick = " · ⚠️ vs market"
                        pick_block = (
                            "💰 **Moneyline:** "
                            + (f"✅ {ml_pick}" if ml_pick
                               else f"— no edge (model leans {_fav})")
                            + "  \n📊 **Spread:** "
                            + (f"✅ {sp_pick}" if sp_pick else "— no edge")
                            + _disagree_note)
                # (No extra text for live/final games — the FINAL/LIVE tag and,
                #  for final games, the grade below already say the status.)

                # ---- GRADE on FINAL games: did the model call it right? ----
                sc = scores.get(matchup)
                grade_title = ""
                if proj and sc and sc.get("final"):
                    actual_margin = sc["home"] - sc["away"]  # +ve => home won
                    model_home = proj["exp_margin"] > 0       # model's SU pick
                    home_won = actual_margin > 0
                    su_right = (model_home == home_won)
                    # ATS: did the side the model favored cover the home spread?
                    sp_h = g.get("spread")
                    ats = None
                    if sp_h is not None:
                        home_covered = (actual_margin + sp_h) > 0
                        ats = (model_home == home_covered)
                    winner = home if home_won else away
                    model_pick = home if model_home else away
                    # Plain, LABELED line: who won + score attached to teams,
                    # then whether our pick was right in words.
                    win_txt = (f"{winner} won "
                               f"{max(sc['home'], sc['away'])}-"
                               f"{min(sc['home'], sc['away'])}")
                    pick_txt = (f"we picked {model_pick} — "
                                + ("✅ right" if su_right else "❌ wrong"))
                    ats_txt = ""
                    if ats is not None:
                        ats_txt = ("  ·  spread: "
                                   + ("✅ covered" if ats else "❌ missed"))
                    grade_title = f"  ·  {win_txt}  ·  {pick_txt}{ats_txt}"

                title = f"🏀 {matchup}  ·  {tag}{quick}{grade_title}"
                with st.expander(title, expanded=False):
                    if pick_block:
                        st.markdown(pick_block)
                    if proj and sc and sc.get("final"):
                        actual_margin = sc["home"] - sc["away"]
                        model_home = proj["exp_margin"] > 0
                        home_won = actual_margin > 0
                        su_right = (model_home == home_won)
                        winner = home if home_won else away
                        proj_fav = home if model_home else away
                        box = "#14321f" if su_right else "#2a1414"
                        line_txt = (f"Final: **{away} {sc['away']} – "
                                    f"{sc['home']} {home}** ({winner} won by "
                                    f"{abs(actual_margin)}). "
                                    f"Model picked **{proj_fav}** by "
                                    f"{abs(proj['exp_margin']):.1f} → "
                                    + ("✅ correct winner."
                                       if su_right else "❌ wrong winner."))
                        sp_h = g.get("spread")
                        if sp_h is not None:
                            home_covered = (actual_margin + sp_h) > 0
                            ats_right = (model_home == home_covered)
                            line_txt += (f" Against the spread ({home} "
                                         f"{sp_h:+g}): "
                                         + ("✅ model's side covered."
                                            if ats_right
                                            else "❌ model's side didn't cover."))
                        st.markdown(
                            f'<div style="background:{box};padding:10px 14px;'
                            f'border-radius:10px;color:#e6e9ef;font-size:.9rem;'
                            f'margin-bottom:10px;">{line_txt}</div>',
                            unsafe_allow_html=True)
                    if not is_open:
                        st.caption("This game has tipped off — the line below is "
                                   "the last PRE-GAME number, kept for reference. "
                                   "You generally can't place a pre-game ML/spread "
                                   "bet once the game is live.")
                    if not proj:
                        st.caption("Missing team ratings for one side — can't "
                                   "project this game yet (common very early in "
                                   "the season).")
                        continue
                    c1, c2 = st.columns(2)
                    # Reconcile displayed points with the ADJUSTED margin. The
                    # raw exp_home/away_pts are pre-adjustment (ratings only);
                    # the final margin folds in home edge, form, four factors,
                    # playtype and the TOV battle. Re-split the projected TOTAL
                    # by the final margin so the two numbers can never disagree
                    # about who's favored (total stays fixed, margin honored).
                    _tot = proj["exp_total"]
                    _mgn = proj["exp_margin"]           # +ve => home favored
                    _disp_home = (_tot + _mgn) / 2.0
                    _disp_away = (_tot - _mgn) / 2.0
                    c1.metric(f"{home} (home) projected", f"{_disp_home:.0f}")
                    c2.metric(f"{away} (away) projected", f"{_disp_away:.0f}")
                    fav = home if proj["exp_margin"] > 0 else away
                    st.write(f"**Our projection:** {fav} by "
                             f"**{abs(proj['exp_margin']):.1f}** "
                             f"(total ~{proj['exp_total']:.0f}, "
                             f"pace {proj['pace']:.0f}). Win odds: "
                             f"{home} {proj['p_home_win']*100:.0f}% / "
                             f"{away} {proj['p_away_win']*100:.0f}%.")

                    # ---- Four Factors: eFG% edge folded into the margin ----
                    ffp = proj.get("four_factor_pts")
                    hff = _gl_four_factors.get(home) or {}
                    aff = _gl_four_factors.get(away) or {}
                    if hff.get("efg") is not None and aff.get("efg") is not None:
                        _side = home if ffp and ffp > 0 else away
                        st.caption(
                            f"📐 Four Factors — eFG%: {home} "
                            f"{hff['efg']*100:.1f}% vs {away} "
                            f"{aff['efg']*100:.1f}%"
                            + (f"  →  nudged the margin **{ffp:+.1f}** toward "
                               f"{_side}." if ffp else " (even).")
                            + f"  TOV%: {home} {hff.get('tov_pct',0)*100:.1f}% / "
                            f"{away} {aff.get('tov_pct',0)*100:.1f}%  ·  "
                            f"OREB%: {home} {hff.get('oreb_pct',0)*100:.1f}% / "
                            f"{away} {aff.get('oreb_pct',0)*100:.1f}%")

                    # ---- Polymarket: a SECOND price to diff against the book --
                    pm = cached_polymarket(home, away)
                    if pm:
                        pm_h = pm["p_home"] * 100
                        model_h = proj["p_home_win"] * 100
                        gap = model_h - pm_h
                        vol = pm.get("volume_24h", 0)
                        agree = "same side" if (model_h >= 50) == (pm_h >= 50) \
                            else "OPPOSITE sides"
                        note = ("thin market — treat as weak" if vol < 5000
                                else "")
                        st.info(
                            f"🪙 **Polymarket (crowd):** {home} {pm_h:.0f}% / "
                            f"{away} {100-pm_h:.0f}%  ·  our model {home} "
                            f"{model_h:.0f}%.  Gap **{gap:+.0f} pts**, {agree}."
                            + (f"  ⚠️ {note}." if note else "")
                            + f"  (24h vol ${vol:,.0f}.) A big gap vs the "
                              "sportsbook line below can flag news the book "
                              "hasn't moved on yet — a signal, not a bet.")

                    # ---- Line movement: where the number OPENED vs now ----
                    opn = _gl_open_odds.get(matchup)
                    if opn:
                        from odds.line_movement import movement_label
                        mv = []
                        mt = movement_label(opn.get("total"), g.get("total"))
                        if mt:
                            mv.append(f"Total {mt}")
                        ms = movement_label(opn.get("spread_home"),
                                            g.get("spread"), kind="spread")
                        if ms:
                            mv.append(f"{home} spread {ms}")
                        if mv:
                            st.caption(
                                "📊 Line movement — " + "  ·  ".join(mv)
                                + ". The move since open shows which way the "
                                  "market (often sharp money) leaned.")

                    # ---- Team style: how each side plays (context) ----
                    def _style(abbr):
                        iso = (_gl_iso.get(abbr) or {}).get("ppp")
                        drv = (_gl_drives.get(abbr) or {}).get("drives")
                        bits = []
                        if iso is not None:
                            bits.append(f"ISO {iso:.2f} pts/poss")
                        if drv is not None:
                            bits.append(f"{drv:.0f} drives/game")
                        return f"**{abbr}** " + ", ".join(bits) if bits else None
                    sty = [x for x in (_style(home), _style(away)) if x]
                    if sty:
                        st.caption("🎬 Team style: " + "  ·  ".join(sty)
                                   + ". (ISO efficiency = points per isolation "
                                     "possession; drives = rim pressure.)")

                    # ---- Each team's DEFENSE: points allowed per 100 (#rank) --
                    def _def_line(abbr):
                        row = ratings.get(abbr) or {}
                        dv = row.get("def")
                        if dv is None:
                            return None
                        rk = row.get("def_rank")
                        rktxt = f" (#{rk} of 30)" if rk else ""
                        feel = ("stingy" if (rk and rk <= 10)
                                else "leaky" if (rk and rk >= 21)
                                else "average")
                        return f"**{abbr}** allows {dv:g}/100{rktxt} — {feel} D"
                    dlines = [x for x in (_def_line(home), _def_line(away)) if x]
                    if dlines:
                        st.caption("🛡️ Defense (points allowed per 100 poss, "
                                   "lower = better): " + "  ·  ".join(dlines))

                    # ---- Star Tax: injuries that moved the margin ----
                    htax = proj.get("home_injury_tax") or 0
                    atax = proj.get("away_injury_tax") or 0
                    if htax or atax:
                        bits = []
                        if htax:
                            bits.append(f"**{home}** −{htax:.1f} ("
                                        + ", ".join(proj.get(
                                            "home_injury_reasons", [])) + ")")
                        if atax:
                            bits.append(f"**{away}** −{atax:.1f} ("
                                        + ", ".join(proj.get(
                                            "away_injury_reasons", [])) + ")")
                        st.info("🩹 Injury adjustment (Star Tax) applied to the "
                                "margin: " + "; ".join(bits)
                                + ". A ruled-out star shifts the line toward the "
                                "healthy team — the pick above already reflects "
                                "this.")

                    # ---- Moneyline EV (if the book posted ML) ----
                    ml_home = g.get("moneyline")
                    g_away = lines.get(away) or {}
                    ml_away = g_away.get("moneyline")
                    if ml_home is not None and ml_away is not None:
                        ev = evaluate_prop(proj["p_home_win"], proj["p_away_win"],
                                           int(ml_home), int(ml_away),
                                           edge_threshold=ev_bar / 100.0)
                        st.markdown(f"**Moneyline** — {home} {int(ml_home):+} / "
                                    f"{away} {int(ml_away):+}")
                        if ev.get("is_value"):
                            over = ev["recommended_side"] == "over"
                            side = home if over else away
                            edge = (ev["edge_over_pct"] if over
                                    else ev["edge_under_pct"])
                            st.success(f"✅ Value on **{side}** moneyline "
                                       f"(edge {edge:+.1f}% vs the fair line).")
                        else:
                            st.caption("No moneyline edge — the price matches our "
                                       "estimate.")

                    # ---- Spread EV (if the book posted a spread) ----
                    sp_home = g.get("spread")
                    if sp_home is not None:
                        p_home_cov, p_away_cov = cover_probability(
                            proj["exp_margin"], sp_home, _mstd)
                        # Both sides priced ~ -110 by convention unless we have better.
                        ev_s = evaluate_prop(p_home_cov, p_away_cov, -110, -110,
                                             edge_threshold=ev_bar / 100.0)
                        st.markdown(f"**Spread** — {home} {sp_home:+g} / "
                                    f"{away} {-sp_home:+g}")
                        _favt = home if sp_home < 0 else away
                        _dogt = away if sp_home < 0 else home
                        _num = abs(sp_home)
                        st.caption(
                            f"➖ **minus = favorite**: {_favt} {-_num:g} must "
                            f"**win by more than {_num:g}** to cover. "
                            f"➕ **plus = underdog**: {_dogt} +{_num:g} covers "
                            f"if they **win outright OR lose by less than "
                            f"{_num:g}**. (Land exactly on {_num:g} = push, "
                            f"bet refunded.)")
                        if ev_s.get("is_value"):
                            over = ev_s["recommended_side"] == "over"
                            side = (f"{home} {sp_home:+g}" if over
                                    else f"{away} {-sp_home:+g}")
                            edge = (ev_s["edge_over_pct"] if over
                                    else ev_s["edge_under_pct"])
                            st.success(f"✅ Value on **{side}** "
                                       f"(we have the cover at "
                                       f"{max(p_home_cov, p_away_cov)*100:.0f}%, "
                                       f"edge {edge:+.1f}%).")
                        else:
                            st.caption("No spread edge — our margin agrees with "
                                       "the line.")

                    if g.get("total") is not None:
                        st.caption(f"Game total (O/U): **{g['total']:g}**. "
                                   f"Our projected total: {proj['exp_total']:.0f}.")
            st.caption("⚠️ Game-side projections blend a rotation Monte Carlo "
                       "sim (who plays × their usage/efficiency, averaged over "
                       "thousands of runs) with the team-ratings baseline + home "
                       "court. Treat a ✅ as 'worth a look', and remember sides "
                       "are efficiently priced; your edge is a soft/stale line, "
                       "not out-guessing the market.")

