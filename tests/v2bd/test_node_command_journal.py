from __future__ import annotations
import pytest
from remotemcp.durable.errors import DurableError
from remotemcp.node.command_journal import NodeCommandJournal
from remotemcp.node.db import NodeDatabase

def test_command_replay_and_conflict(tmp_path):
    db=NodeDatabase(tmp_path/"rt");db.bootstrap();j=NodeCommandJournal(db)
    env={"command_id":"cmd_1","route_generation":1,"operation_id":"op","request_hash":"abc","command_type":"TASK_READ_FILE","payload":{},"device_id":"dev","project_id":None,"task_id":None,"issued_at_ms":1,"command_expires_at_ms":9999999999999}
    row,new=j.receive(env);assert new
    row2,new2=j.receive(env);assert not new2 and row2["command_id"]=="cmd_1"
    j.mark_executing("cmd_1")
    term=j.terminal("cmd_1","SUCCEEDED",result={"x":1})
    assert j.response(term)["result"]=={"x":1}
    assert j.response(j.terminal("cmd_1","SUCCEEDED",result={"x":1}))["result"]=={"x":1}
    bad=dict(env);bad["request_hash"]="different"
    with pytest.raises(DurableError) as exc:j.receive(bad)
    assert exc.value.code=="COMMAND_CONFLICT"
