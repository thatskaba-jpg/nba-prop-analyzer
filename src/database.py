"""
database.py — the local storage layer.

We use SQLite, which is just a single file on disk (data/bot.db).
No server to install, no setup. Think of it as an Excel workbook that
Python can read and write quickly and safely.

Every table we create gets a clear, documented purpose so you always
know what's actual data vs. model output.
"""

import os
import sqlite3
import sys
from pathlib import Path


def nba_today() -> str:
    """Today's NBA 'basketball day' in US Eastern (EST/EDT), as YYYY-MM-DD.

    Use this instead of date.today() anywhere 'today' means the slate being
    played. The cloud server runs in UTC, so after ~8pm ET its date is already
    tomorrow — which would show the wrong (tiny) slate. Eastern keeps every tab
    on the night actually in play. zoneinfo handles DST automatically; the
    fallback (UTC-5) is close enough if the tz database is unavailable.
    """
    import datetime as _d
    try:
        from zoneinfo import ZoneInfo
        return _d.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        now_utc = _d.datetime.now(_d.timezone.utc)
        return (now_utc - _d.timedelta(hours=5)).date().isoformat()

def nba_season_start_year() -> int:
    """Start year of the NBA season in play (seasons tip off in October, so
    Jan-Sep belongs to the season that began the previous calendar year)."""
    today = nba_today()
    year, month = int(today[:4]), int(today[5:7])
    return year if month >= 10 else year - 1


# The database file lives in the project's data/ folder.
# This file is src/database.py, so the project root is ONE level up from src/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "bot.db"

# Minimum plausible size (bytes) for a real database. A truncated/failed
# download leaves a tiny file; we treat anything under this as "not really
# here" so a bad download is retried rather than silently used.
_MIN_DB_BYTES = 1_000_000  # 1 MB


def _db_url() -> str:
    """Where to download the database from when it's missing locally (cloud
    deploy). Read from a Streamlit secret (DB_URL) or an env var; empty means
    'no remote source' (normal on your Mac, where the file already exists)."""
    url = os.getenv("DB_URL", "").strip()
    if not url:
        try:
            import streamlit as st  # only present in the dashboard runtime
            url = str(st.secrets.get("DB_URL", "")).strip()
        except Exception:
            url = ""
    return url


def _ensure_db_present():
    """On a host where data/bot.db is missing (e.g. Streamlit Cloud), download
    it once from DB_URL. A no-op on your Mac, where the file already exists.

    Never fabricates data: if there's no file AND no DB_URL, the caller just
    gets an empty database (schema only), which is the honest outcome."""
    try:
        if DB_PATH.exists() and DB_PATH.stat().st_size >= _MIN_DB_BYTES:
            return  # real file already here — nothing to do
    except OSError:
        pass
    url = _db_url()
    if not url:
        return  # no remote source configured; caller gets an empty DB
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".db.part")
    try:
        import urllib.request
        print(f"Database not found locally — downloading from DB_URL…",
              file=sys.stderr)
        urllib.request.urlretrieve(url, tmp)
        # DB_URL may point at a .zip (GitHub Releases reject a bare .db upload,
        # and a zip is much smaller). Detect a zip by content and extract the
        # bot.db inside it.
        import zipfile
        if zipfile.is_zipfile(tmp):
            with zipfile.ZipFile(tmp) as zf:
                # Prefer an entry literally named bot.db; else the first .db.
                names = zf.namelist()
                member = next((n for n in names
                               if n.endswith("bot.db")), None) \
                    or next((n for n in names if n.endswith(".db")), None)
                if not member:
                    raise ValueError("zip has no .db file inside")
                with zf.open(member) as src, open(DB_PATH, "wb") as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
            tmp.unlink(missing_ok=True)
        else:
            if tmp.stat().st_size < _MIN_DB_BYTES:
                raise ValueError(f"downloaded file too small "
                                 f"({tmp.stat().st_size} bytes) — bad URL?")
            tmp.replace(DB_PATH)
        if DB_PATH.stat().st_size < _MIN_DB_BYTES:
            raise ValueError("extracted database is too small — bad archive?")
        print(f"Database ready at {DB_PATH} "
              f"({DB_PATH.stat().st_size // 1_048_576} MB).", file=sys.stderr)
    except Exception as e:
        # Leave no half-written file behind; surface the reason, don't crash.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"Database download failed: {e}", file=sys.stderr)


def get_connection():
    """Open (or create) the database file and return a connection.

    sqlite3.Row lets us read columns by name (row["points"]) instead of
    by number, which keeps the rest of the code readable.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_db_present()
    # timeout: wait up to 30s for a competing write lock instead of raising
    # 'database is locked' immediately (the dashboard and a collector can run
    # at the same time). WAL mode lets readers and a single writer coexist.
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def ensure_indexes(conn=None):
    """Create the indexes the readers rely on (safe to repeat).

    load_player_history() looks players up BY NAME and the team/defense builders
    scan BY DATE; with only the (player_id, game_id) primary key those were full
    scans of ~250K rows on every call."""
    own = conn is None
    if own:
        conn = get_connection()
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_pgl_name_date "
        "ON player_game_logs(player_name, game_date)",
        "CREATE INDEX IF NOT EXISTS idx_pgl_date ON player_game_logs(game_date)",
        "CREATE INDEX IF NOT EXISTS idx_pgl_game ON player_game_logs(game_id)",
    ):
        conn.execute(sql)
    conn.commit()
    if own:
        conn.close()


def init_schema():
    """Create our tables if they don't already exist.

    player_game_logs = ACTUAL historical results, one row per player per game.
    This is raw truth from NBA.com — never model output, never odds.
    """
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_game_logs (
            player_id      INTEGER NOT NULL,
            player_name    TEXT    NOT NULL,
            game_id        TEXT    NOT NULL,
            game_date      TEXT    NOT NULL,   -- YYYY-MM-DD, used to prevent future-peeking
            season         TEXT    NOT NULL,
            matchup        TEXT,                -- e.g. "LAL vs. BOS"
            is_home        INTEGER,             -- 1 = home, 0 = away
            opponent       TEXT,
            minutes        REAL,
            points         INTEGER,
            rebounds       INTEGER,
            assists        INTEGER,
            threes_made    INTEGER,
            threes_att     INTEGER,             -- 3-point attempts (fg3a)
            fgm            INTEGER,             -- field goals made
            fga            INTEGER,             -- field-goal attempts
            ftm            INTEGER,             -- free throws made
            fta            INTEGER,             -- free-throw attempts
            steals         INTEGER,
            blocks         INTEGER,
            turnovers      INTEGER,
            fouls          INTEGER,
            PRIMARY KEY (player_id, game_id)
        )
        """
    )
    # Migration: add counting-stat columns to pre-existing databases that were
    # created before these columns existed. ALTER TABLE ADD COLUMN is safe and
    # non-destructive (existing rows get NULL until a reload fills them).
    existing = {r[1] for r in conn.execute("PRAGMA table_info(player_game_logs)")}
    for col in ("steals", "blocks", "turnovers", "fouls",
                "threes_att", "fgm", "fga", "ftm", "fta"):
        if col not in existing:
            conn.execute(f"ALTER TABLE player_game_logs ADD COLUMN {col} INTEGER")

    # Phase 9: prediction tracking. Every prediction we log here is graded
    # against the actual result once the game's box score lands — the brief's
    # "track predictions even when you don't bet." result/correct stay NULL
    # until graded. This is model output + a record, never raw truth.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    TEXT NOT NULL,     -- when we made the prediction
            model_name    TEXT,
            player_id     INTEGER NOT NULL,
            player_name   TEXT,
            game_date     TEXT NOT NULL,     -- the game being predicted
            opponent      TEXT,
            prop_type     TEXT NOT NULL,
            line          REAL NOT NULL,
            projection    REAL,
            pick          TEXT,              -- 'over' / 'under'
            prob          REAL,              -- model P of the picked side
            odds          INTEGER,           -- American odds, if the user gave them
            ev_pct        REAL,              -- expected value %, if odds given
            result        REAL,              -- actual stat once known (NULL=pending)
            correct       INTEGER,           -- 1/0 once graded (NULL=pending)
            graded_at     TEXT,
            UNIQUE(player_id, game_date, prop_type, line, model_name)
        )
        """)
    ensure_indexes(conn)
    conn.commit()
    conn.close()
    print(f"Database ready at: {DB_PATH}")


if __name__ == "__main__":
    init_schema()
