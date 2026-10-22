"""
fetch_player_advanced.py — store each player's V2 ADVANCED season averages.

The game model should know how good the PLAYERS on a team actually are, not just
the team's stale season box-score ratings. The richest per-player signal is in
balldontlie's V2 advanced feed: PIE (overall impact), usage%, true-shooting%,
offensive/defensive rating, net rating. But that endpoint is a per-player live
call — fetching it on every page render would storm the API. So, DB-first:
this collector pulls a season's advanced rows ONCE, averages the full-game
(period 0) rows per player, and stores them in `player_advanced`. The strength
model then reads the table with zero API calls.

Run (GOAT tier):
    python3 src/collect/fetch_player_advanced.py --verify
    python3 src/collect/fetch_player_advanced.py 2025 2026
"""

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from database import get_connection  # noqa: E402
from collect.goat_data import advanced_stats  # noqa: E402


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_advanced (
            player_id   INTEGER NOT NULL,
            player_name TEXT,
            season      TEXT    NOT NULL,
            games       INTEGER,
            pie         REAL,
            usage       REAL,
            ts_pct      REAL,
            off_rating  REAL,
            def_rating  REAL,
            net_rating  REAL,
            pace        REAL,
            efg_pct     REAL,
            ast_pct     REAL,
            ast_ratio   REAL,
            ast_to_tov  REAL,
            tov_ratio   REAL,
            oreb_pct    REAL,
            dreb_pct    REAL,
            reb_pct     REAL,
            fetched_at  TEXT,
            PRIMARY KEY (player_id, season)
        )
        """)
    # Migrate older DBs that predate the full V2 field set.
    have = {r[1] for r in conn.execute("PRAGMA table_info(player_advanced)")}
    for col in ("pace", "efg_pct", "ast_pct", "ast_ratio", "ast_to_tov",
                "tov_ratio", "oreb_pct", "dreb_pct", "reb_pct"):
        if col not in have:
            conn.execute(f"ALTER TABLE player_advanced ADD COLUMN {col} REAL")
    conn.commit()


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _aggregate(rows):
    """player_id -> averaged advanced line, from full-game (period 0) rows."""
    agg = defaultdict(lambda: defaultdict(list))
    names = {}
    for r in rows:
        if r.get("period", 0) not in (0, None):
            continue
        p = r.get("player") or {}
        pid = p.get("id")
        if pid is None:
            continue
        names[pid] = f"{p.get('first_name','')} {p.get('last_name','')}".strip()
        a = agg[pid]
        a["pie"].append(r.get("pie"))
        a["usage"].append(r.get("usage_percentage"))
        a["ts"].append(r.get("true_shooting_percentage"))
        a["off"].append(r.get("offensive_rating"))
        a["def"].append(r.get("defensive_rating"))
        a["net"].append(r.get("net_rating"))
        a["pace"].append(r.get("pace"))
        a["efg"].append(r.get("effective_field_goal_percentage"))
        a["ast_pct"].append(r.get("assist_percentage"))
        a["ast_ratio"].append(r.get("assist_ratio"))
        a["ast_to"].append(r.get("assist_to_turnover"))
        a["tov_ratio"].append(r.get("turnover_ratio"))
        a["oreb"].append(r.get("offensive_rebound_percentage"))
        a["dreb"].append(r.get("defensive_rebound_percentage"))
        a["reb"].append(r.get("rebound_percentage"))
    out = {}
    for pid, a in agg.items():
        out[pid] = {
            "name": names.get(pid, ""),
            "games": len(a["pie"]),
            "pie": _avg(a["pie"]), "usage": _avg(a["usage"]),
            "ts_pct": _avg(a["ts"]), "off_rating": _avg(a["off"]),
            "def_rating": _avg(a["def"]), "net_rating": _avg(a["net"]),
            "pace": _avg(a["pace"]), "efg_pct": _avg(a["efg"]),
            "ast_pct": _avg(a["ast_pct"]), "ast_ratio": _avg(a["ast_ratio"]),
            "ast_to_tov": _avg(a["ast_to"]), "tov_ratio": _avg(a["tov_ratio"]),
            "oreb_pct": _avg(a["oreb"]), "dreb_pct": _avg(a["dreb"]),
            "reb_pct": _avg(a["reb"]),
        }
    return out


def fetch_player_advanced(season: int) -> int:
    """Pull a season's advanced rows and store per-player averages. Returns the
    number of players stored. Refreshes this season's rows (full snapshot)."""
    conn = get_connection()
    _ensure_table(conn)
    rows = advanced_stats(season=season)
    players = _aggregate(rows)
    stored = 0
    conn.execute("DELETE FROM player_advanced WHERE season = ?", (str(season),))
    for pid, a in players.items():
        conn.execute(
            "INSERT OR REPLACE INTO player_advanced "
            "(player_id, player_name, season, games, pie, usage, ts_pct, "
            " off_rating, def_rating, net_rating, pace, efg_pct, ast_pct, "
            " ast_ratio, ast_to_tov, tov_ratio, oreb_pct, dreb_pct, reb_pct, "
            " fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(pid), a["name"], str(season), a["games"], a["pie"],
             a["usage"], a["ts_pct"], a["off_rating"], a["def_rating"],
             a["net_rating"], a["pace"], a["efg_pct"], a["ast_pct"],
             a["ast_ratio"], a["ast_to_tov"], a["tov_ratio"], a["oreb_pct"],
             a["dreb_pct"], a["reb_pct"], _now()))
        stored += 1
    conn.commit()
    conn.close()
    print(f"Season {season}: stored advanced averages for {stored} players.")
    return stored


def advanced_by_name(season):
    """{player_name_key: {pie, usage, ts_pct, off_rating, def_rating,
    net_rating}} for a season, from the local table. Keyed by normalized name so
    the strength model (which joins on name) can look players up. {} if the
    table is empty for that season."""
    key = lambda s: "".join(ch for ch in (s or "").lower() if ch.isalnum())
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='player_advanced'").fetchone()
        if not exists:
            return {}
        rows = conn.execute(
            "SELECT player_name, pie, usage, ts_pct, off_rating, def_rating, "
            "net_rating, pace, efg_pct, ast_pct, ast_ratio, ast_to_tov, "
            "tov_ratio, oreb_pct, dreb_pct, reb_pct "
            "FROM player_advanced WHERE season = ?",
            (str(season),)).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    _g = lambda r, k: (r[k] if k in r.keys() else None)
    out = {}
    for r in rows:
        out[key(r["player_name"])] = {
            "pie": r["pie"], "usage": r["usage"], "ts_pct": r["ts_pct"],
            "off_rating": r["off_rating"], "def_rating": r["def_rating"],
            "net_rating": r["net_rating"], "pace": _g(r, "pace"),
            "efg_pct": _g(r, "efg_pct"), "ast_pct": _g(r, "ast_pct"),
            "ast_ratio": _g(r, "ast_ratio"), "ast_to_tov": _g(r, "ast_to_tov"),
            "tov_ratio": _g(r, "tov_ratio"), "oreb_pct": _g(r, "oreb_pct"),
            "dreb_pct": _g(r, "dreb_pct"), "reb_pct": _g(r, "reb_pct"),
        }
    return out


_ADV_FIELDS = ("pie", "usage", "ts_pct", "off_rating", "def_rating",
               "net_rating", "pace", "efg_pct", "ast_pct", "ast_ratio",
               "ast_to_tov", "tov_ratio", "oreb_pct", "dreb_pct", "reb_pct")


def advanced_by_name_blended(season, K=9.0):
    """Like advanced_by_name, but EVERY advanced field is blended this-season
    toward last-season by games played (w = g/(g+K)). Early in the season the
    current sample is tiny, so this leans on last year's proven numbers and
    ramps to current data as games accumulate — for ALL V2 advanced stats, not
    just PIE/def_rating.

    Keyed by normalized player NAME, so a traded player's blended line follows
    HIM (the caller joins name -> current player_teams), never the old team.
    """
    cur = advanced_by_name(season) or {}
    prev = advanced_by_name(season - 1) or {}
    out = {}
    for nk in set(cur) | set(prev):
        c = cur.get(nk) or {}
        p = prev.get(nk) or {}
        g = c.get("games") or 0
        w = g / (g + K)
        blended = {}
        for f in _ADV_FIELDS:
            cv, pv = c.get(f), p.get(f)
            if cv is not None and pv is not None:
                blended[f] = w * cv + (1 - w) * pv
            else:
                blended[f] = cv if cv is not None else pv
        blended["games"] = g
        out[nk] = blended
    return out


def verify():
    rows = advanced_stats(season=2026)
    agg = _aggregate(rows)
    print(f"2026 advanced rows -> {len(agg)} players aggregated.")
    for pid, a in list(agg.items())[:5]:
        print(f"  {a['name']}: PIE={a['pie']} usage={a['usage']} "
              f"def_rtg={a['def_rating']} net={a['net_rating']} ({a['games']}g)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        verify()
    elif len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            fetch_player_advanced(int(arg))
    else:
        print("Usage: python3 src/collect/fetch_player_advanced.py SEASON [...]")
        print("       python3 src/collect/fetch_player_advanced.py --verify")
