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
        durable_dir = Path(__file__).resolve().parent
        package_root = durable_dir.parent
        # V2-A compatibility: migration_path remains the mutable v1 path used
        # by the frozen checksum-drift regression harness.
        self.migration_path = durable_dir / "migrations" / "001_v2a.sql"
        self.migration_v2_path = package_root / "multiagent" / "migrations" / "002_v2b.sql"
        self.migration_v3_path = package_root / "routing" / "migrations" / "003_v2bd.sql"

    @property
    def migrations(self):
        return [
            (1, self.migration_path),
            (2, self.migration_v2_path),
            (3, self.migration_v3_path),
        ]

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

    @staticmethod
    def _statements(sql_text: str) -> list[str]:
        statements = []
        for part in sql_text.split(";"):
            stmt = part.strip()
            if not stmt:
                continue
            if stmt.upper().startswith("PRAGMA "):
                continue
            statements.append(stmt)
        return statements

    def bootstrap(self, target_version: int = 1) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

        for version, path in self.migrations:
            if version > int(target_version):
                break
            if not path.exists():
                if version in (2, 3):
                    continue
                raise DurableError(
                    "STARTUP_FATAL_SCHEMA_MISMATCH",
                    f"migration file missing: {path}",
                )

            sql_bytes = path.read_bytes()
            checksum = hashlib.sha256(sql_bytes).hexdigest()

            con = self.connect()
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("BEGIN IMMEDIATE")

                ledger_exists = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()

                if not ledger_exists:
                    if version != 1:
                        raise DurableError(
                            "STARTUP_FATAL_SCHEMA_MISMATCH",
                            "schema ledger missing before non-initial migration",
                        )
                    for stmt in self._statements(sql_bytes.decode("utf-8")):
                        con.execute(stmt)
                    con.execute(
                        "INSERT INTO schema_migrations(version,applied_at_ms,checksum_sha256) "
                        "VALUES(1,?,?)",
                        (now_ms(), checksum),
                    )
                    con.execute("COMMIT")
                    continue

                row = con.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version=?",
                    (version,),
                ).fetchone()
                if row is not None:
                    if row["checksum_sha256"] != checksum:
                        raise DurableError(
                            "STARTUP_FATAL_SCHEMA_MISMATCH",
                            f"migration {version:03d} checksum mismatch",
                            expected=row["checksum_sha256"],
                            actual=checksum,
                        )
                    con.execute("COMMIT")
                    continue

                # Apply the next ordered migration in-place.
                applied = {
                    int(r["version"])
                    for r in con.execute("SELECT version FROM schema_migrations").fetchall()
                }
                expected_previous = set(range(1, version))
                if not expected_previous.issubset(applied):
                    raise DurableError(
                        "STARTUP_FATAL_SCHEMA_MISMATCH",
                        f"migration ordering violation before version {version}",
                        applied=sorted(applied),
                    )

                for stmt in self._statements(sql_bytes.decode("utf-8")):
                    con.execute(stmt)
                con.execute(
                    "INSERT INTO schema_migrations(version,applied_at_ms,checksum_sha256) "
                    "VALUES(?,?,?)",
                    (version, now_ms(), checksum),
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