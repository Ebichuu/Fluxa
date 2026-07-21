from __future__ import annotations

import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


SCHEMA_VERSION = 3


def resolve_database_path(project_root=None, environment=None, legacy_path=None):
    source = os.environ if environment is None else environment
    explicit = str(source.get("MCC_DATABASE_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if legacy_path:
        return Path(legacy_path).expanduser().resolve().parent / "media_control_center.sqlite3"
    root = Path(project_root or Path(__file__).resolve().parents[1])
    return root / "db" / "media_control_center.sqlite3"


class SQLiteRuntime:
    def __init__(self, database_path):
        self.database_path = Path(database_path)

    def connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def transaction(self, immediate=False):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "schema_version INTEGER NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO schema_meta (id, schema_version) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET schema_version=excluded.schema_version, updated_at=CURRENT_TIMESTAMP",
                (SCHEMA_VERSION,),
            )
            try:
                # Probe FTS5 in memory so repeated repository initialization never locks the data file.
                with closing(sqlite3.connect(":memory:")) as probe:
                    probe.execute("CREATE VIRTUAL TABLE __mcc_fts_probe USING fts5(value)")
            except sqlite3.OperationalError as exc:
                raise RuntimeError("当前 SQLite 不支持 FTS5") from exc
        return self.database_path

    def schema_version(self):
        self.initialize()
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT schema_version FROM schema_meta WHERE id=1").fetchone()
        return int(row["schema_version"] if row else 0)
