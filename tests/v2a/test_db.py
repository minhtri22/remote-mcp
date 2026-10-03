from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from remotemcp.durable.db import Database
from remotemcp.durable.errors import DurableError


def test_migration_v1_fresh_and_repeat_bootstrap(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()
    row = db.query_one("SELECT version,checksum_sha256 FROM schema_migrations WHERE version=1")
    assert row is not None
    assert row["version"] == 1
    expected = hashlib.sha256(db.migration_path.read_bytes()).hexdigest()
    assert row["checksum_sha256"] == expected

    db.bootstrap()
    rows = db.query_all("SELECT version FROM schema_migrations")
    assert [r["version"] for r in rows] == [1]


def test_migration_checksum_mismatch_is_startup_fatal(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()

    altered = tmp_path / "altered.sql"
    altered.write_bytes(db.migration_path.read_bytes() + b"\n-- drift\n")
    db.migration_path = altered

    with pytest.raises(DurableError) as exc:
        db.bootstrap()
    assert exc.value.code == "STARTUP_FATAL_SCHEMA_MISMATCH"


def _hash_with_crlf(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.replace("\n", "\r\n").encode("utf-8")).hexdigest()


def test_migration_checksum_accepts_lf_crlf_equivalence_v1(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()

    crlf_checksum = _hash_with_crlf(db.migration_path)
    with db.transaction() as con:
        con.execute(
            "UPDATE schema_migrations SET checksum_sha256=? WHERE version=1",
            (crlf_checksum,),
        )

    db.bootstrap()


def test_migration_checksum_accepts_lf_crlf_equivalence_all_versions(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap(target_version=3)

    crlf = {
        1: _hash_with_crlf(db.migration_path),
        2: _hash_with_crlf(db.migration_v2_path),
        3: _hash_with_crlf(db.migration_v3_path),
    }
    with db.transaction() as con:
        for version, checksum in crlf.items():
            con.execute(
                "UPDATE schema_migrations SET checksum_sha256=? WHERE version=?",
                (checksum, version),
            )

    db.bootstrap(target_version=3)


def test_migration_checksum_still_rejects_non_newline_content_drift(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()

    original = db.migration_path.read_bytes()
    altered = tmp_path / "altered-content.sql"
    altered.write_bytes(original.replace(b"CREATE TABLE operations", b"CREATE TABLE operations_changed", 1))
    db.migration_path = altered

    with pytest.raises(DurableError) as exc:
        db.bootstrap()
    assert exc.value.code == "STARTUP_FATAL_SCHEMA_MISMATCH"
