from __future__ import annotations
import pytest
from remotemcp.durable.errors import DurableError
from remotemcp.node.runtime_lock import RuntimeLock

def test_second_runtime_process_lock_fails(tmp_path):
    a=RuntimeLock(tmp_path).acquire()
    try:
        b=RuntimeLock(tmp_path)
        with pytest.raises(DurableError) as exc:b.acquire()
        assert exc.value.code=="NODE_ALREADY_RUNNING"
    finally:a.release()
    c=RuntimeLock(tmp_path).acquire();c.release()
