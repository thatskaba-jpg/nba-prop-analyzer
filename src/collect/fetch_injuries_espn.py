"""
fetch_injuries_espn.py — FREE NBA injury news from ESPN's public API.

balldontlie's injuries feed (fetch_injuries.py) needs the GOAT tier; ESPN's
public site API is free, needs NO key, and is where fast injury/scratch news
lands first. This fetcher pulls ESPN's current injury report and merges it into
the SAME `injuries` table the rest of the app reads via current_injury(), so
every projection and the value scanner automatically account for who's hurt.

Endpoint (verified live, no auth):
    https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
    -> {injuries: [ {displayName: team, injuries: [
          {status, date, shortComment, athlete:{displayName, ...}} ]} ]}

MATCHING: ESPN uses its own athlete IDs (not our balldontlie/CSV IDs), so we
match to our players BY NAME (the same normalized key the odds client uses) and
store under our player_id. A player ESPN lists but we don't have is skipped — we
never invent a player_id.

STATUS NORMALIZATION: ESPN uses "Out", "Day-To-Day", "Questionable", etc. We
keep the text as-is; the injury FACTOR already matches on 'out'/'doubtful'/
'question' substrings, so ESPN's wording slots straight in.

HONESTY: no key means this can't be blocked by tier — but ESPN is an unofficial
public API, so if the shape ever changes we report it rather than guess. Run:
    python3 src/collect/fetch_injuries_espn.py --verify
    python3 src/collect/fetch_injuries_espn.py           # merge current injuries
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

try:
    import requests
except ImportError:
    raise SystemExit("Run:  pip install -r requirements.txt")

from database import get_connection  # noqa: E402

ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
            "injuries")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 "
                   "Safari/537.36"),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _name_key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS injuries (
            player_id   INTEGER PRIMARY KEY,
            player_name TEXT,
            status      TEXT,
            description TEXT,
            fetched_at  TEXT
        )
        """)
    conn.commit()


def _fetch_raw():
    r = requests.get(ESPN_URL, headers=_HEADERS, timeout=25)
    r.raise_for_status()
    return r.json()


def _flatten(payload):
    """Yield (player_name, status, comment) for every injured player ESPN lists."""
    for team in (payload.get("injuries") or []):
        for item in (team.get("injuries") or []):
            ath = item.get("athlete") or {}
            name = ath.get("displayName") or (
                f"{ath.get('firstName','')} {ath.get('lastName','')}".strip())
            status = (item.get("status") or "").strip()
            comment = (item.get("shortComment")
                       or item.get("longComment") or "").strip()
            if name and status:
                yield name, status, comment


def fetch_injuries_espn():
    """Pull ESPN's injury report and MERGE it into the injuries table by name.

    Merge (not replace): this augments whatever balldontlie stored, so having
    both sources only improves coverage. Returns the number matched/stored.
    """
    try:
        payload = _fetch_raw()
    except Exception as e:
        print(f"ESPN injuries request failed: {e}")
        print("ESPN's public API may be rate-limiting or its shape changed — "
              "not fabricating injuries.")
        return 0

    conn = get_connection()
    _ensure_table(conn)
    # Build a name -> our player_id map from the game logs.
    id_by_name = {}
    for row in conn.execute(
            "SELECT DISTINCT player_name, player_id FROM player_game_logs"):
        id_by_name.setdefault(_name_key(row["player_name"]),
                              (row["player_id"], row["player_name"]))

    stored, unmatched = 0, 0
    conn.execute("BEGIN")
    for name, status, comment in _flatten(payload):
        hit = id_by_name.get(_name_key(name))
        if not hit:
            unmatched += 1
            continue
        pid, db_name = hit
        conn.execute(
            "INSERT OR REPLACE INTO injuries "
            "(player_id, player_name, status, description, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (int(pid), db_name, status, comment, _now()))
        stored += 1
    conn.commit()
    conn.close()
    print(f"ESPN: stored {stored} injuries (matched to our players); "
          f"{unmatched} ESPN players not in our data (skipped).")
    return stored


def verify():
    import json
    try:
        payload = _fetch_raw()
    except Exception as e:
        print(f"Verify failed: {e}")
        return
    teams = payload.get("injuries") or []
    total = sum(len(t.get("injuries") or []) for t in teams)
    print(f"ESPN injuries: {len(teams)} teams, {total} injured players.")
    sample = list(_flatten(payload))[:5]
    print("Sample (name — status — note):")
    for name, status, comment in sample:
        print(f"  {name} — {status} — {comment[:70]}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verify":
        verify()
    else:
        fetch_injuries_espn()
