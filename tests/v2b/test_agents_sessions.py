from __future__ import annotations

import asyncio
import pytest

from remotemcp.durable.errors import DurableError


def test_heartbeat_monotonic_and_no_last_login_wins(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        b.auth["client"]="oauth-b"
        z=await b.multi.agent_register("rz","z","iz",[])
        x=b.multi.agent_heartbeat(a["agent_id"],a["session_id"],1)
        y=b.multi.agent_heartbeat(z["agent_id"],z["session_id"],1)
        assert x["renewed"] and y["renewed"]
        dup=b.multi.agent_heartbeat(a["agent_id"],a["session_id"],1)
        assert dup["renewed"] is False
        with pytest.raises(DurableError) as exc:
            b.multi.agent_heartbeat(a["agent_id"],a["session_id"],0)
        assert exc.value.code=="HEARTBEAT_STALE"
        assert b.multi.agents.session(z["agent_id"],z["session_id"])["state"]=="ACTIVE"
    asyncio.run(run())


def test_closing_one_session_does_not_close_other(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("ra","a","ia",[])
        b.auth["client"]="oauth-b"
        z=await b.multi.agent_register("rz","z","iz",[])
        await b.multi.session_close("close-a",a["agent_id"],a["session_id"])
        assert b.multi.agents.session(a["agent_id"],a["session_id"])["state"]=="CLOSED"
        assert b.multi.agents.session(z["agent_id"],z["session_id"])["state"]=="ACTIVE"
    asyncio.run(run())