import os
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",r"C:\Users\minht")

import asyncio,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService
from remotemcp.durable.errors import DurableError

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-claim-") as td:
        base=Path(td); ws=base/"ws";rt=base/"rt";ws.mkdir()
        auth={"client":"a"}
        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,poll_ms=20))
        m=MultiAgentService(MultiAgentConfig(ws,rt,task_lease_ttl_seconds=120,poll_ms=20),d,auth_client_resolver=lambda:auth["client"])
        a=await m.agent_register("ra","a","ia",[])
        auth["client"]="b"; b=await m.agent_register("rb","b","ib",[])
        p=await m.project_register("p",".",4); t=await m.task_create("t",p["project_id"],"same")
        async def claim(op,x):
            try:return await m.task_claim(op,t["task_id"],x["agent_id"],x["session_id"])
            except DurableError as e:return {"error":e.code}
        r=await asyncio.gather(claim("c1",a),claim("c2",b))
        wins=[x for x in r if "lease_token" in x]
        losses=[x for x in r if "error" in x]
        assert len(wins)==1 and len(losses)==1
        assert m.db.query_one("SELECT COUNT(*) AS n FROM task_leases WHERE task_id=?",(t["task_id"],))["n"]==1
        print(json.dumps({"verdict":"PASS","winners":1,"losers":1,"loser_error":losses[0]["error"],"lease_epoch":wins[0]["lease_epoch"]},indent=2))
if __name__=="__main__": asyncio.run(main())