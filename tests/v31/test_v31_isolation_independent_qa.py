"""Independent QA: read SQL rows directly; never use policy as its own comparator.

All cases are synthetic and run on each OS. No operator files are opened.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from test_v31_expired_four_isolation import (
    DEVICE, PROJECT, TASK, PROXY, setup,
)
from remotemcp.durable.errors import DurableError


def independent_sql_snapshot(db):
    return db.conn.execute(
        "SELECT command_id,device_id,route_generation,operation_id,operation_step,"
        "project_id,task_id,command_type,request_hash,payload_json,state,"
        "command_expires_at_ms,lease_expires_at_ms,delivery_attempt,"
        "created_at_ms,updated_at_ms,finished_at_ms,result_json,error_code,error_json "
        "FROM device_commands ORDER BY command_id"
    ).fetchall()


def independent_digest(rows):
    canonical = [dict(r) for r in rows]
    return hashlib.sha256(json.dumps(
        canonical, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    ).encode()).hexdigest()


def test_readonly_baseline_four_writes_do_not_appear_after_reject(tmp_path,monkeypatch):
    repo,db,dev,payloads,manifest=setup(monkeypatch,tmp_path)
    baseline=independent_sql_snapshot(db)
    before=independent_digest(baseline)
    assert len(baseline)==4
    with pytest.raises(DurableError):
        repo.create(
            DEVICE, "JOB_SUBMIT",
            {"proxy_job_id":PROXY,"task_id":TASK,"argv":["fixture"]},
            project_id=PROJECT,task_id=TASK,operation_id="op_1",operation_step=0
        )
    assert independent_digest(independent_sql_snapshot(db))==before
    assert len(independent_sql_snapshot(db))==4


def test_independent_sql_assessment_reads_only_historical_before_and_after_poll(tmp_path,monkeypatch):
    repo,db,_,payloads,manifest=setup(monkeypatch,tmp_path)
    before=independent_digest(independent_sql_snapshot(db))
    # New explicitly separate operation is allowed to use ordinary read rules.
    row,created=repo.create(
        DEVICE,"TASK_LIST_DIR",{"task_id":TASK},
        project_id=PROJECT,task_id=TASK,operation_id="op_future_read"
    )
    assert created
    leased=repo.poll(DEVICE,1)
    assert leased["command_id"]==row["command_id"]
    original_rows=db.conn.execute(
        "SELECT * FROM device_commands WHERE command_id IN (?,?,?,?) ORDER BY command_id",
        tuple(sorted(repo.isolation.ids)),
    ).fetchall()
    assert independent_digest(original_rows)==before


def test_public_code_does_not_embed_operational_identifiers_or_enable_by_default():
    root=Path(__file__).resolve().parents[2]
    controller=(root/"remotemcp/routing/commands.py").read_text(encoding="utf-8")
    isolation=(root/"remotemcp/routing/historical_isolation.py").read_text(encoding="utf-8")
    assert "self.isolation=None" in controller
    assert "if any(v is not None for v in isolation_vars)" in controller
    assert "ExpiredFourIsolation.load_signed" in controller
    assert "self.isolation.assert_bound(con,device_id)" in controller
    assert controller.count("exclusion") >= 5
    assert "before_create" in controller
    assert "JOB_RECOVER_ROUTED_JOB" in isolation
    jobs=(root/"remotemcp/routing/routed_jobs.py").read_text(encoding="utf-8")
    service=(root/"remotemcp/routing/service.py").read_text(encoding="utf-8")
    assert "self._reject_protected(proxy_job_id=proxy_job_id)" in jobs
    assert "self._reject_protected(operation_id=operation_id)" in jobs
    assert "RoutedJobRepository(self.db,isolation=self.commands.isolation)" in service
    assert "private_manifest" in isolation
    assert "verify(signature" in isolation
    assert "REMOTEMCP_V31_EXPIRED_ISOLATION_" in controller
    import re
    for p in (controller,isolation):
        assert not re.search(r"\bdev_[0-9a-f]{32}\b",p)
        assert not re.search(r"\bcmd_[0-9a-f]{32}\b",p)
        assert "Stop-Process" not in p
        assert "subprocess.Popen" not in p


def test_failing_bound_identity_does_not_modify_protected_table(tmp_path,monkeypatch):
    repo,db,_,payloads,manifest=setup(monkeypatch,tmp_path)
    target=repo.isolation.ids[1]
    # Deliberate fixture drift occurs BEFORE the protection is exercised.
    db.conn.execute(
        "UPDATE device_commands SET request_hash=? WHERE command_id=?",
        ("f"*64,target),
    )
    db.conn.commit()
    before=independent_digest(independent_sql_snapshot(db))
    with pytest.raises(DurableError) as e:
        repo.poll(DEVICE,1)
    assert e.value.code=="HISTORICAL_ISOLATION_INVALID_HOLD"
    assert independent_digest(independent_sql_snapshot(db))==before
