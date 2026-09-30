from __future__ import annotations

import hashlib
import sqlite3

from remotemcp.durable.db import Database
from remotemcp.durable.models import now_ms


def test_fresh_database_applies_001_then_002(tmp_path):
    db=Database(tmp_path/"runtime")
    db.bootstrap(target_version=2)
    rows=db.query_all("SELECT version,checksum_sha256 FROM schema_migrations ORDER BY version")
    assert [r["version"] for r in rows]==[1,2]
    assert rows[0]["checksum_sha256"]==hashlib.sha256(db.migrations[0][1].read_bytes()).hexdigest()
    assert rows[1]["checksum_sha256"]==hashlib.sha256(db.migrations[1][1].read_bytes()).hexdigest()
    assert db.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name='owner_accounts'")
    assert db.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name='cas_mutations'")


def test_existing_v1_database_upgrades_in_place(tmp_path):
    db=Database(tmp_path/"runtime")
    db.runtime_dir.mkdir(parents=True)
    sql=db.migrations[0][1].read_text(encoding="utf-8")
    checksum=hashlib.sha256(db.migrations[0][1].read_bytes()).hexdigest()
    con=sqlite3.connect(db.path)
    con.executescript(sql)
    con.execute(
        "INSERT INTO schema_migrations(version,applied_at_ms,checksum_sha256) VALUES(1,?,?)",
        (now_ms(),checksum),
    )
    con.commit();con.close()
    db.bootstrap(target_version=2)
    assert [r["version"] for r in db.query_all("SELECT version FROM schema_migrations ORDER BY version")]==[1,2]


def test_migration_002_is_byte_identical_to_frozen_spec():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    assert (root/"specs"/"v2b_schema_v2.sql").read_bytes()==(root/"remotemcp"/"multiagent"/"migrations"/"002_v2b.sql").read_bytes()