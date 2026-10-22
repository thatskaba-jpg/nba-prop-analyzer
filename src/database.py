"""
database.py — the local storage layer.

We use SQLite, which is just a single file on disk (data/bot.db).
No server to install, no setup. Think of it as an Excel workbook that
Python can read and write quickly and safely.

Every table we create gets a clear, documented purpose so you always
know what's actual data vs. model output.
"""

import sqlite3
from pathlib import Path

# The database file lives in the project's data/ folder.
# This file is src/database.py, so the project root is ONE level up from src/.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "bot.db"


def get_connection():
    """Open (or create) the database file and return a connection.

    sqlite3.Row lets us read columns by name (row["points"]) instead of
    by number, which keeps the rest of the code readable.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    for col in ("steals", "blocks", "turnovers", "fouls"):
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
    conn.commit()
    conn.close()
    print(f"Database ready at: {DB_PATH}")


if __name__ == "__main__":
    init_schema()
