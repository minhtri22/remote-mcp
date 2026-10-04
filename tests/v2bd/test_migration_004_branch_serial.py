from __future__ import annotations

import hashlib
from pathlib import Path

from remotemcp.durable.db import Database


def test_migration_004_order_and_byte_identity(tmp_path):
    db=Database(tmp_path/"rt")
    db.bootstrap(target_version=4)
    rows=db.query_all("select version,checksum_sha256 from schema_migrations order by version")
    assert [r["version"] for r in rows]==[1,2,3,4]
    root=Path(__file__).resolve().parents[2]
    src=root/"specs"/"branch_serial_job_admission_schema_v4.sql"
    dst=root/"remotemcp"/"multiagent"/"migrations"/"004_branch_serial_job_admission.sql"
    assert src.read_bytes()==dst.read_bytes()
    assert rows[-1]["checksum_sha256"]==hashlib.sha256(src.read_bytes()).hexdigest()
    cols={r["name"] for r in db.query_all("pragma table_info(task_job_admissions)")}
    assert {
        "admission_id","task_id","sequence","operation_id","execution_kind",
        "predecessor_job_id","job_id","state","terminal_state",
        "terminal_evidence_json","terminal_at_ms",
    }.issubset(cols)


def test_partial_unique_index_allows_only_one_active_admission_per_task(tmp_path):
    db=Database(tmp_path/"rt")
    db.bootstrap(target_version=4)
    indexes={r["name"] for r in db.query_all("pragma index_list(task_job_admissions)")}
    assert "uq_task_job_admissions_one_active_lane" in indexes
