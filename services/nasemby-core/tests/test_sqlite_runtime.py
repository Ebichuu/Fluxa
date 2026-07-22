from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.sqlite_runtime import SQLiteRuntime


class SQLiteRuntimeTests(unittest.TestCase):
    def test_initializes_wal_foreign_keys_and_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = SQLiteRuntime(Path(directory) / "db" / "media_control_center.sqlite3")
            runtime.initialize()
            with closing(runtime.connect()) as connection:
                journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
                busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
                version = connection.execute("SELECT schema_version FROM schema_meta WHERE id=1").fetchone()[0]
                probe = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='__mcc_fts_probe'"
                ).fetchone()
            self.assertEqual(journal.lower(), "wal")
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(busy_timeout, 5000)
            self.assertEqual(version, 4)
            self.assertIsNone(probe)

    def test_transaction_rolls_back_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = SQLiteRuntime(Path(directory) / "media_control_center.sqlite3")
            runtime.initialize()
            with self.assertRaises(RuntimeError):
                with runtime.transaction(immediate=True) as connection:
                    connection.execute("CREATE TABLE rollback_probe(value TEXT)")
                    connection.execute("INSERT INTO rollback_probe VALUES ('x')")
                    raise RuntimeError("stop")
            with closing(runtime.connect()) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rollback_probe'"
                ).fetchone()
            self.assertIsNone(exists)


if __name__ == "__main__":
    unittest.main()
