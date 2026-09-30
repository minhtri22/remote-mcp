from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import DurableError
from .models import now_ms


class Database:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir.resolve()
        self.path = self.runtime_dir / "runtime.db"
        self.jobs_dir = self.runtime_dir / "jobs"
        self.migration_path = Path(__file__).resolve().parent / "migrations" / "001_v2a.sql"

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def bootstrap(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

        sql_bytes = self.migration_path.read_bytes()
        checksum = hashlib.sha256(sql_bytes).hexdigest()

        con = self.connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("BEGIN IMMEDIATE")

            table_exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()

            if table_exists:
                row = con.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version=1"
                ).fetchone()
                if row is None:
                    raise DurableError(
                        "STARTUP_FATAL_SCHEMA_MISMATCH",
                        "schema_migrations exists but version 1 is absent",
                    )
                if row["checksum_sha256"] != checksum:
                    raise DurableError(
                        "STARTUP_FATAL_SCHEMA_MISMATCH",
                        "migration 001 checksum mismatch",
                        expected=row["checksum_sha256"],
                        actual=checksum,
                    )
            else:
                sql_text = sql_bytes.decode("utf-8")
                statements = []
                for part in sql_text.split(";"):
                    stmt = part.strip()
                    if not stmt:
                        continue
                    if stmt.upper().startswith("PRAGMA "):
                        continue
                    statements.append(stmt)
                for stmt in statements:
                    con.execute(stmt)
                con.execute(
                    "INSERT INTO schema_migrations(version,applied_at_ms,checksum_sha256) "
                    "VALUES(1,?,?)",
                    (now_ms(), checksum),
                )
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()

    @contextmanager
    def transaction(self, immediate: bool = True):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield con
            con.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DurableError("DB_BUSY", str(exc)) from exc
            raise
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()

    def query_one(self, sql: str, params: tuple = ()):
        con = self.connect()
        try:
            return con.execute(sql, params).fetchone()
        finally:
            con.close()

    def query_all(self, sql: str, params: tuple = ()):
        con = self.connect()
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()
