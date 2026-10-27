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

# Market-probability snapshots captured at bet-time (pre-tip), so the backtest
# can apply the SAME early-season market anchor the live card used. Keyed by
# "DATE|AWAY @ HOME". Committed JSON so it survives redeploys.
_MKT_PATH = Path(__file__).resolve().parents[2] / "data" / \
    "market_snapshots.json"


def _mkt_load():
    if _MKT_PATH.exists():
        try:
            return json.loads(_MKT_PATH.read_text())
        except Exception:
            pass
    return {}


def capture_market_prob(gdate, home, away, market_p_home, games_played,
                        extra=None, overwrite=False):
    """Log the book's de-vigged home-win prob for a game, FOR THE RECORD ONLY
    (grading and backtests). Tonight's picks never read this: they use the live
    odds feed directly.

    overwrite=True keeps the LATEST pre-tip line (the dashboard calls it on
    every view of an OPEN game, so when tip-off passes the stored number is the
    closing line). Skips the disk write when nothing changed."""
    if market_p_home is None or not (0.0 < market_p_home < 1.0):
        return
    key = f"{gdate}|{away} @ {home}"
    data = _mkt_load()
    p = round(float(market_p_home), 4)
    if key in data and not overwrite:
        return
    if key in data and data[key].get("market_p_home") == p and \
            (not extra or all(data[key].get(k) == v for k, v in extra.items())):
        return                                   # unchanged: no disk write
    data[key] = {"market_p_home": p, "games_played": int(games_played or 0)}
    if extra:                       # e.g. spread/ML/total, kept for later checks
        data[key].update(extra)
    try:
        _MKT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MKT_PATH.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


_MKT_TRIED = set()     # dates we already asked the odds feed about (this run)


def _fetch_market_for_date(gdate):
    """Ask the odds API for this date's lines and save them. Tried at most once
    per date per process, so grading never hammers the API."""
    if gdate in _MKT_TRIED:
        return
    _MKT_TRIED.add(gdate)
    try:
        from collect.backfill_game_lines import backfill_date_live
        backfill_date_live(gdate)
    except Exception:
        pass


def _market_for_game(gdate, home, away):
    """(market_p_home, games_played_at_capture) for a game, or (None, 0).

    Uses the saved line when there is one. If not, it asks the odds API for that
    date (and saves the answer), so older games get graded against the book
    too. (None, 0) only if the API has no line for that game either."""
    key = f"{gdate}|{away} @ {home}"
    snap = _mkt_load().get(key)
    if not snap:
        _fetch_market_for_date(gdate)
        snap = _mkt_load().get(key)
    if not snap:
        return None, 0
    return snap.get("market_p_home"), snap.get("games_played", 0)


_HOME_EDGES_CACHE = {}


def _home_edges(season):
    """Cached per-team home-court edges (real home/road splits) for grading."""
    if season not in _HOME_EDGES_CACHE:
        try:
            from models.team_form import home_edge_map
            _HOME_EDGES_CACHE[season] = home_edge_map(season)
        except Exception:
            _HOME_EDGES_CACHE[season] = {}
    return _HOME_EDGES_CACHE[season]


_RECORD_CACHE = {}


def _record_strength(season):
    """Cached per-team real-record (W-L) strength in points for grading."""
    if season not in _RECORD_CACHE:
        try:
            from models.team_form import record_strength_map
            _RECORD_CACHE[season] = record_strength_map(season)
        except Exception:
            _RECORD_CACHE[season] = {}
    return _RECORD_CACHE[season]


_TEAM_API_CACHE = {}


def _team_api(season):
    """Cached roster-safe current-season API team net/efg cross-check."""
    if season not in _TEAM_API_CACHE:
        try:
            from collect.goat_data import team_api_net_map
            _TEAM_API_CACHE[season] = team_api_net_map(season)
        except Exception:
            _TEAM_API_CACHE[season] = {}
    return _TEAM_API_CACHE[season]


def _blend_ratings(season):
    """Team ratings for the game model, roster-strength FIRST.

    The balldontlie team-ratings API feed is unreliable early in a season
    (compressed/flat numbers, missing teams), which made loaded rosters read as
    average and flipped winners. So we lead with the DB-first, trade-aware
    roster strength (current roster x player advanced stats: PIE, def rating)
    and fall back to the blended API feed only for any team the roster map
    lacks. Mirrors the dashboard's game-lines tab so every surface agrees."""
    # Primary: roster-based strength (zero API, trade-aware, player-advanced).
    roster = {}
    try:
        from models.roster_strength_db import team_strength_map
        roster = team_strength_map(season) or {}
    except Exception:
        roster = {}

    # Fallback base: blended API team ratings (this + last season).
    from collect.goat_data import team_ratings_map
    try:
        from collect.local_store import get_or_refresh
        cur = get_or_refresh(f"team_ratings_{season}",
                             lambda: team_ratings_map(season), 12) or {}
        prev = get_or_refresh(f"team_ratings_{season - 1}",
                              lambda: team_ratings_map(season - 1), 12) or {}
    except Exception:
        cur = team_ratings_map(season) or {}
        prev = team_ratings_map(season - 1) or {}
    api = {}
    if cur or prev:
        base, other = (cur, prev) if cur else (prev, {})
        for abbr, c in base.items():
            p = other.get(abbr)
            b = dict(c)
            if p:
                n = c.get("games") or 5
                w = max(0.0, min(1.0, n / 30.0))
                for f in ("off", "def", "pace"):
                    if c.get(f) is not None and p.get(f) is not None:
                        b[f] = round(w * c[f] + (1 - w) * p[f], 2)
            api[abbr] = b

    # Merge: games-played-weighted blend. Early season leans on roster strength;
    # as the API accumulates real games it takes over automatically (a team's
    # API 'games' count drives the weight). A team only in one source uses it.
    K = 8.0
    teams = set(roster) | set(api)
    out = {}
    for ab in teams:
        r = roster.get(ab)
        a = api.get(ab)
        if not r:
            b = dict(a)
            if b.get("off") is not None and b.get("def") is not None:
                b["net"] = round(b["off"] - b["def"], 1)
            out[ab] = b; continue
        if not a or a.get("off") is None or a.get("def") is None:
            out[ab] = {"off": r["off"], "def": r["def"],
                       "pace": r.get("pace", 100.0),
                       "net": round(r["off"] - r["def"], 1)}
            continue
        n = a.get("games") or 0
        w_api = max(0.0, min(1.0, n / (n + K)))
        b = dict(a)
        for f in ("off", "def"):
            b[f] = round(w_api * a[f] + (1 - w_api) * r[f], 1)
        b["pace"] = a.get("pace") or r.get("pace", 100.0)
        # net MUST equal off-def. The API's raw net_rating is a 1-game ±40
        # artifact early season; recompute it from the blended off/def so every
        # surface and the team_api cross-check see a consistent number.
        b["net"] = round(b["off"] - b["def"], 1)
        out[ab] = b
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


_POS_STATS = ("points", "rebounds", "assists")
_pos_cache = {}


def _positional_for_date(gdate):
    """Leak-free custom positional-defense map for a game dated `gdate`:
    {stat: {team: {group: factor}}} built ONLY from games strictly before that
    date (positional_defense_factors filters on cutoff), so grading game N never
    sees game N or later. Memoized by date — a night with many games queries
    once, not per game."""
    if gdate in _pos_cache:
        return _pos_cache[gdate]
    out = {}
    try:
        from features.positional_defense import positional_defense_factors
        for s in _POS_STATS:
            out[s] = positional_defense_factors(s, gdate) or {}
    except Exception:
        out = {}
    _pos_cache[gdate] = out
    return out


_ff_cache = {}
_form_cache = {}


def _four_factors_for_date(gdate):
    """Leak-free team four-factors (eFG% and TOV%) reconstructed from box-score
    game logs STRICTLY BEFORE gdate. OREB% isn't in the logs (no off/def reb
    split) and playtype is API-season-only, so we include only the two we can
    compute without leakage — enough to drive the shooting/ball specialists.
    Memoized by date. {abbr: {'efg','tov_pct'}}."""
    if gdate in _ff_cache:
        return _ff_cache[gdate]
    out = {}
    try:
        from database import get_connection
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT opponent AS team_na, matchup,
                   SUBSTR(matchup,1,3) AS tm,
                   SUM(fgm) fgm, SUM(fga) fga, SUM(threes_made) tpm,
                   SUM(turnovers) tov, SUM(fga)+0.44*SUM(fta)+SUM(turnovers) poss
            FROM player_game_logs
            WHERE game_date < ? AND fga IS NOT NULL AND matchup LIKE '% %'
            GROUP BY tm
            """,
            (gdate,)).fetchall()
        conn.close()
        for r in rows:
            tm = r["tm"]
            if not tm or not r["fga"]:
                continue
            efg = (r["fgm"] + 0.5 * (r["tpm"] or 0)) / r["fga"]
            poss = r["poss"] or 1.0
            tov_pct = (r["tov"] or 0) / poss
            out[tm] = {"efg": round(efg, 4), "tov_pct": round(tov_pct, 4)}
    except Exception:
        out = {}
    _ff_cache[gdate] = out
    return out


def _form_for_date(teams, gdate):
    """Leak-free recent-form + rest map (already cutoff-based). Memoized."""
    key = (tuple(sorted(teams)), gdate)
    if key in _form_cache:
        return _form_cache[key]
    try:
        from models.team_form import team_form_map
        m = team_form_map(tuple(teams), gdate) or {}
    except Exception:
        m = {}
    _form_cache[key] = m
    return m


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
        _pos = _positional_for_date(gdate)
        _ff = _four_factors_for_date(gdate)
        _form = _form_for_date((home, away), gdate)
        _mkt, _mg = _market_for_game(gdate, home, away)
        for pname, prof in GAME_PROFILES.items():
            proj = project_game(home, away, ratings, prof, positional=_pos,
                                four_factors=_ff, team_form=_form,
                                market_p_home=_mkt, games_played=_mg,
                                home_edges=_home_edges(season),
                                record_strength=_record_strength(season),
                                team_api=_team_api(season))
            if not proj:
                continue
            rec = tally.setdefault(pname, [0, 0, 0.0])  # [correct, total, brier]
            rec[1] += 1
            if (proj["exp_margin"] > 0) == home_won:
                rec[0] += 1
            # Calibration: squared error of the win probability vs the result
            # (0 = perfect, lower is better). Averaged -> a plain 0-100 score.
            rec[2] += (proj["p_home_win"] - (1 if home_won else 0)) ** 2
    rows = [(n, c, t, (c / t if t else 0.0),
             (br / t if t else None))
            for n, (c, t, br) in tally.items()]
    rows.sort(key=lambda x: (-x[3], -x[2]))
    # Best-effort persist for longer history; never let a write break the view.
    try:
        update_scoreboard(season)
    except Exception:
        pass
    return rows


def scoreboard_detail(season=2026, days_back=30):
    """Per-GAME, per-MODEL moneyline picks, graded from the live feed.

    Returns a flat list of dicts (one per game per model) laid out for a HUMAN
    reading the CSV — plain headers, each value self-explanatory:
        Date, Game ("AWAY @ HOME"), Model, Model pick ("OKC by 7.6"),
        Actual result ("OKC won by 12 (120-108)"), Hit? ("✅ WIN"/"❌ LOSS").
    In-memory, no file I/O."""
    ratings = _blend_ratings(season)
    games = _finished_games_live(days_back)
    out = []
    for gid, gdate, home, away, hp, ap in games:
        home_won = hp > ap
        winner = home if home_won else away
        loser = away if home_won else home
        actual_margin = abs(hp - ap)
        _pos = _positional_for_date(gdate)
        _ff = _four_factors_for_date(gdate)
        _form = _form_for_date((home, away), gdate)
        _mkt, _mg = _market_for_game(gdate, home, away)
        for pname, prof in GAME_PROFILES.items():
            proj = project_game(home, away, ratings, prof, positional=_pos,
                                four_factors=_ff, team_form=_form,
                                market_p_home=_mkt, games_played=_mg,
                                home_edges=_home_edges(season),
                                record_strength=_record_strength(season),
                                team_api=_team_api(season))
            if not proj:
                continue
            picked_home = proj["p_home_win"] >= 0.5
            pick = home if picked_home else away
            pm = abs(proj["exp_margin"])
            hit = (picked_home == home_won)
            out.append({
                "Date": gdate,
                "Game": f"{away} @ {home}",
                "Model": pname,
                # The model's pick, stated in plain English with its margin.
                "Model pick": f"{pick} by {pm:.1f}",
                # What actually happened: winner + final score, unambiguous.
                "Actual result": f"{winner} won by {actual_margin} "
                                 f"({max(hp, ap)}-{min(hp, ap)})",
                # Did the model's pick match the actual winner?
                "Hit?": "✅ WIN" if hit else "❌ LOSS",
            })
    out.sort(key=lambda r: (r["Date"], r["Game"], r["Model"]))
    return out


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
    # Compare ids as TEXT everywhere. The live feed hands back ints but the DB
    # fallback hands back text ("21717855" != 21717855 inside a set), so the
    # same game used to be graded twice whenever the two sources alternated.
    done = {str(g) for g in data.get("graded_game_ids", [])}
    ratings = _blend_ratings(season)
    games = (_finished_games_live() if use_live else []) \
        or _finished_games(season)
    newly = 0
    for gid, gdate, home, away, hp, ap in games:
        if str(gid) in done:
            continue
        home_won = hp > ap
        _pos = _positional_for_date(gdate)
        _ff = _four_factors_for_date(gdate)
        _form = _form_for_date((home, away), gdate)
        _mkt, _mg = _market_for_game(gdate, home, away)
        for pname, prof in GAME_PROFILES.items():
            proj = project_game(home, away, ratings, prof, positional=_pos,
                                four_factors=_ff, team_form=_form,
                                market_p_home=_mkt, games_played=_mg,
                                home_edges=_home_edges(season),
                                record_strength=_record_strength(season),
                                team_api=_team_api(season))
            if not proj:
                continue
            picked_home = proj["exp_margin"] > 0
            correct = (picked_home == home_won)
            rec = data["profiles"].setdefault(pname, {"correct": 0, "total": 0})
            rec["total"] += 1
            if correct:
                rec["correct"] += 1
        done.add(str(gid))
        newly += 1
    data["graded_game_ids"] = sorted(done)
    if newly:
        _save(data)
    try:                                 # also settle the frozen (forward) picks
        grade_forward(games)
    except Exception:
        pass
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


# --------------------------------------------------------------------------- #
# FROZEN PICKS: the honest version of this scoreboard
#
# The board above re-grades finished games with TODAY'S ratings, which already
# include those games, so it can only flatter. Here every profile's pick (plus
# a few dumb baselines) is written down BEFORE tip, first write wins, and graded
# after the final. Elo (results only) scored 68% on 1,558 held-out games, so a
# profile that can't beat the baselines here is not adding anything.
# --------------------------------------------------------------------------- #

_FWD_PATH = Path(__file__).resolve().parents[2] / "data" / "profile_forward.json"
BASELINE_HOME = "Baseline: always home"
BASELINE_ELO = "Baseline: Elo (results only)"
BASELINE_MARKET = "Baseline: market favorite"
_elo_cache = {"at": None, "elo": None}


def _fwd_load():
    try:
        data = json.loads(_FWD_PATH.read_text())
        data.setdefault("pending", {})
        data.setdefault("record", {})
        data.setdefault("graded_keys", [])
        return data
    except Exception:
        return {"pending": {}, "record": {}, "graded_keys": []}


def _fwd_save(data):
    _FWD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FWD_PATH.write_text(json.dumps(data, indent=1))


def _elo_now():
    """Elo ratings learned from every finished game (rebuilt at most hourly)."""
    import time
    if _elo_cache["elo"] is None or time.time() - (_elo_cache["at"] or 0) > 3600:
        from models.elo import current_ratings
        _elo_cache["elo"], _elo_cache["at"] = current_ratings(k=20, hca=50, carry=0.6), time.time()
    return _elo_cache["elo"]


def build_picks(home, away, ratings, project_kwargs, market_p_home=None, elo=None):
    """Every profile's pick for one game, plus the baselines.
    -> {name: {"home": bool, "p_home": float|None, "margin": float|None}}"""
    picks = {}
    for pname, prof in GAME_PROFILES.items():
        proj = project_game(home, away, ratings, prof, **(project_kwargs or {}))
        if proj:
            picks[pname] = {"home": proj["exp_margin"] > 0,
                            "p_home": proj["p_home_win"], "margin": proj["exp_margin"]}
    picks[BASELINE_HOME] = {"home": True, "p_home": None, "margin": None}
    if elo is not None:
        p = elo.p_home(home, away)
        picks[BASELINE_ELO] = {"home": p >= 0.5, "p_home": round(p, 4),
                               "margin": round(elo.margin(home, away), 1)}
    if market_p_home is not None and 0.0 < market_p_home < 1.0:
        picks[BASELINE_MARKET] = {"home": market_p_home >= 0.5,
                                  "p_home": round(float(market_p_home), 4), "margin": None}
    return picks


def capture_forward_picks(gdate, home, away, ratings, project_kwargs=None,
                          market_p_home=None, elo=None):
    """Freeze the picks for a game that has NOT started. First write wins: once a
    game is captured (or graded), later calls change nothing. Returns True if
    this call stored it."""
    key = f"{gdate}|{away} @ {home}"
    data = _fwd_load()
    if key in data["pending"] or key in data["graded_keys"]:
        return False
    if elo is None:
        try:
            elo = _elo_now()
        except Exception:
            elo = None
    picks = build_picks(home, away, ratings, project_kwargs, market_p_home, elo)
    if len(picks) <= 1:                     # nothing but the trivial baseline
        return False
    data["pending"][key] = {"date": gdate, "home": home, "away": away,
                            "picks": picks}
    _fwd_save(data)
    return True


def grade_forward(finished_games):
    """Grade frozen games that are now final. `finished_games` is
    [(game_id, date, home, away, home_pts, away_pts), ...]. Returns how many."""
    data = _fwd_load()
    if not data["pending"]:
        return 0
    by_key = {f"{g[1]}|{g[3]} @ {g[2]}": g for g in finished_games}
    graded = 0
    for key in list(data["pending"]):
        g = by_key.get(key)
        if not g:
            continue
        home_won = g[4] > g[5]
        for name, pick in data["pending"][key]["picks"].items():
            rec = data["record"].setdefault(name, {"correct": 0, "total": 0, "brier_sum": 0.0, "brier_n": 0})
            rec["total"] += 1
            rec["correct"] += 1 if pick["home"] == home_won else 0
            if pick.get("p_home") is not None:
                rec["brier_sum"] += (pick["p_home"] - (1.0 if home_won else 0.0)) ** 2
                rec["brier_n"] += 1
        data["graded_keys"].append(key)
        del data["pending"][key]
        graded += 1
    if graded:
        _fwd_save(data)
    return graded


def get_forward_scoreboard():
    """[(name, correct, total, pct, brier)] best win% first; baselines included."""
    rows = []
    for name, r in _fwd_load()["record"].items():
        t = r["total"]
        rows.append((name, r["correct"], t, (r["correct"] / t) if t else 0.0,
                     (r["brier_sum"] / r["brier_n"]) if r.get("brier_n") else None))
    rows.sort(key=lambda x: (-x[3], -x[2]))
    return rows


def forward_pending_count():
    return len(_fwd_load()["pending"])


if __name__ == "__main__":
    d = update_scoreboard()
    print("Graded games:", len(d.get("graded_game_ids", [])))
    for pname, c, t, pct in get_scoreboard():
        print(f"  {pname:<28} {c}/{t}  {pct*100:.1f}%")
