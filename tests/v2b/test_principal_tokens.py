from __future__ import annotations

import asyncio


def test_two_oauth_clients_share_owner_but_keep_independent_sessions(make_bundle):
    async def run():
        b=make_bundle()
        a=await b.multi.agent_register("reg-a","agent-a","install-a",[])
        b.auth["client"]="oauth-client-b"
        c=await b.multi.agent_register("reg-b","agent-b","install-b",[])
        assert a["owner_account_id"]==c["owner_account_id"]
        assert a["auth_client_id"]=="oauth-client-a"
        assert c["auth_client_id"]=="oauth-client-b"
        assert a["session_id"]!=c["session_id"]
        assert b.multi.agents.session(a["agent_id"],a["session_id"])["state"]=="ACTIVE"
        assert b.multi.agents.session(c["agent_id"],c["session_id"])["state"]=="ACTIVE"
    asyncio.run(run())


def test_claim_replay_returns_same_token_without_persisting_cleartext(make_bundle):
    async def run():
        b=make_bundle()
        agent=await b.multi.agent_register("reg","a","install",[])
        p=await b.multi.project_register("preg",".",4)
        task=await b.multi.task_create("tc",p["project_id"],"t")
        first=await b.multi.task_claim("claim",task["task_id"],agent["agent_id"],agent["session_id"])
        replay=await b.multi.task_claim("claim",task["task_id"],agent["agent_id"],agent["session_id"])
        assert first["lease_token"]==replay["lease_token"]
        row=b.durable.db.query_one("SELECT * FROM task_leases WHERE task_id=?",(task["task_id"],))
        assert first["lease_token"] not in tuple(str(v) for v in row)
        assert row["lease_token_hash"]==b.multi.tokens.digest(first["lease_token"])
    asyncio.run(run())

def test_missing_owner_account_file_with_persisted_state_fails_closed(make_bundle):
    import pytest
    from remotemcp.durable.errors import DurableError
    from remotemcp.multiagent.principal import OwnerIdentity

    b=make_bundle()
    async def seed():
        await b.multi.agent_register("reg","a","install",[])
    asyncio.run(seed())
    b.multi.identity.path.unlink()
    with pytest.raises(DurableError) as exc:
        OwnerIdentity(b.runtime,b.durable.db,lambda:"oauth-client-a")
    assert exc.value.code=="STARTUP_FATAL_OWNER_ACCOUNT"