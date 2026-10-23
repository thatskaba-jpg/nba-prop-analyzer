"""
Shared test helpers.

Importing this module puts src/ on sys.path (the project's modules import each
other as top-level names like `database` and `models.x`). TempDBTestCase gives
every test its own empty SQLite database with the real schema, so nothing ever
touches data/bot.db.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TempDBTestCase(unittest.TestCase):
    """Point database.DB_PATH at a throwaway file for the length of each test."""

    def setUp(self):
        import database
        self._tmp = tempfile.TemporaryDirectory()
        self._patches = [
            mock.patch.object(database, "DB_PATH",
                              Path(self._tmp.name) / "test.db"),
            # A tiny test DB would otherwise look "missing" and try DB_URL.
            mock.patch.object(database, "_ensure_db_present", lambda: None),
        ]
        for p in self._patches:
            p.start()
        database.init_schema()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    # ---- tiny fixture helpers -------------------------------------------
    def add_log(self, player_id, game_id, game_date, minutes, points=0,
                rebounds=0, assists=0):
        """Insert one box-score row (minutes=0 is a DNP)."""
        from database import get_connection
        conn = get_connection()
        conn.execute(
            "INSERT INTO player_game_logs (player_id, player_name, game_id, "
            "game_date, season, matchup, is_home, opponent, minutes, points, "
            "rebounds, assists, threes_made, steals, blocks, turnovers) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (player_id, f"Player {player_id}", str(game_id), game_date, "2025",
             "AAA vs BBB", 1, "BBB", minutes, points, rebounds, assists,
             0, 0, 0, 0))
        conn.commit()
        conn.close()

    def fetch_all(self, sql, params=()):
        from database import get_connection
        conn = get_connection()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows
