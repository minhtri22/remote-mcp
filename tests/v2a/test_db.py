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
