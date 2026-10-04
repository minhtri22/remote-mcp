from __future__ import annotations

import asyncio
import pytest

from remotemcp.durable.errors import DurableError
from conftest import pair_node,drive,init_git_repo


def test_same_relative_path_on_two_devices_and_rebind_guard(make_gateway,tmp_path):
    async def run():
        g=make_gateway()
        r1=tmp_path/"n1";r2=tmp_path/"n2";r1.mkdir();r2.mkdir()
        init_git_repo(r1/"repo","ONE");init_git_repo(r2/"repo","TWO")
        n1,d1=await pair_node(g,r1,tmp_path/"rt1","node1")
        n2,d2=await pair_node(g,r2,tmp_path/"rt2","node2")
        p1=await drive(g,n1,g.routing.project_register_on_device("p1",d1["device_id"],"repo",4))
        p2=await drive(g,n2,g.routing.project_register_on_device("p2",d2["device_id"],"repo",4))
        assert p1["node_root_rel"]==p2["node_root_rel"]=="repo"
        assert p1["root_rel"]!=p2["root_rel"]
        assert p1["device_id"]!=p2["device_id"]
        task=await drive(g,n1,g.routing.task_create_or_local("t1",p1["project_id"],"active"))
        with pytest.raises(DurableError) as exc:
            await drive(g,n2,g.routing.project_bind_device("rebind",p1["project_id"],d2["device_id"]))
        assert exc.value.code=="PROJECT_DEVICE_MIGRATION_FORBIDDEN"
    asyncio.run(run())
