from __future__ import annotations

import pytest

from remotemcp.durable.db import Database
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import OperationState
from remotemcp.durable.operations import OperationRepository


def repo(tmp_path):
    db = Database(tmp_path / "runtime")
    db.bootstrap()
    return OperationRepository(db)


def test_same_id_same_hash_replays_and_different_hash_conflicts(tmp_path):
    ops = repo(tmp_path)
    row, created = ops.reserve("op1", "X", {"a": 1})
    assert created and row["state"] == OperationState.RESERVED.value

    row2, created2 = ops.reserve("op1", "X", {"a": 1})
    assert not created2
    assert row2["request_hash"] == row["request_hash"]

    with pytest.raises(DurableError) as exc:
        ops.reserve("op1", "X", {"a": 2})
    assert exc.value.code == "OPERATION_CONFLICT"

    ops.mark_executing("op1")
    ops.succeed("op1", {"ok": True})
    final = ops.get("op1")
    assert final["state"] == OperationState.SUCCEEDED.value
    assert ops.replay_result(final) == {"ok": True}


def test_failed_retryable_reenters_executing_same_operation(tmp_path):
    ops = repo(tmp_path)
    ops.reserve("op2", "X", {"a": 1})
    ops.mark_executing("op2")
    ops.fail("op2", "DB_BUSY", {"x": 1}, retryable=True)
    assert ops.get("op2")["state"] == OperationState.FAILED_RETRYABLE.value
    row = ops.mark_executing("op2")
    assert row["state"] == OperationState.EXECUTING.value
    assert row["attempt_count"] == 2


def test_in_doubt_is_terminal_for_automatic_execution(tmp_path):
    ops = repo(tmp_path)
    ops.reserve("op3", "X", {"a": 1})
    ops.mark_executing("op3")
    ops.mark_in_doubt("op3", {"reason": "crash"})
    row = ops.mark_executing("op3")
    assert row["state"] == OperationState.IN_DOUBT.value
