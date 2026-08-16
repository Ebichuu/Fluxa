from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from app.sqlite_runtime import BUSY_TIMEOUT_MS, SQLiteRuntime


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
            self.assertEqual(busy_timeout, BUSY_TIMEOUT_MS)
            self.assertEqual(version, 8)
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

    def test_new_runtime_connects_while_existing_writer_holds_wal_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            first = SQLiteRuntime(database)
            first.initialize()
            writer = first.connect()
            try:
                writer.execute("BEGIN IMMEDIATE")
                second = SQLiteRuntime(database)
                with closing(second.connect()) as connection:
                    journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(journal.lower(), "wal")
            finally:
                writer.rollback()
                writer.close()

    def test_transaction_waits_for_a_short_competing_write(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            runtime = SQLiteRuntime(database)
            runtime.initialize()
            writer = runtime.connect()
            writer.execute("CREATE TABLE lock_probe(value TEXT)")
            writer.execute("BEGIN IMMEDIATE")
            completed = threading.Event()
            errors = []

            def write_after_lock():
                try:
                    with runtime.transaction(immediate=True) as connection:
                        connection.execute("INSERT INTO lock_probe VALUES ('ok')")
                except Exception as exc:
                    errors.append(exc)
                finally:
                    completed.set()

            thread = threading.Thread(target=write_after_lock)
            thread.start()
            time.sleep(0.05)
            writer.commit()
            writer.close()
            self.assertTrue(completed.wait(timeout=2))
            thread.join(timeout=1)

            self.assertEqual(errors, [])
            with closing(runtime.connect()) as connection:
                row = connection.execute("SELECT value FROM lock_probe").fetchone()
            self.assertEqual(row["value"], "ok")


if __name__ == "__main__":
    unittest.main()
