from __future__ import annotations
import hashlib
from pathlib import Path
from remotemcp.durable.db import Database

def test_migration_003_order_and_byte_identity(tmp_path):
    db=Database(tmp_path/"rt");db.bootstrap(target_version=3)
    assert [r["version"] for r in db.query_all("select version from schema_migrations order by version")]==[1,2,3]
    root=Path(__file__).resolve().parents[2]
    src=root/"specs"/"v2bd_schema_v3.sql"; dst=root/"remotemcp"/"routing"/"migrations"/"003_v2bd.sql"
    assert src.read_bytes()==dst.read_bytes()
    row=db.query_one("select checksum_sha256 from schema_migrations where version=3")
    assert row["checksum_sha256"]==hashlib.sha256(src.read_bytes()).hexdigest()
    cols={r["name"] for r in db.query_all("pragma table_info(project_device_bindings)")}
    assert "node_root_rel" in cols
    cols={r["name"] for r in db.query_all("pragma table_info(routed_jobs)")}
    assert "terminal_result_json" in cols
