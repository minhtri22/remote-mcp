import os
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",os.path.expanduser("~"))

import asyncio,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms

TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}

async def wait(d,jid,timeout=10):
    for _ in range(int(timeout/0.05)):
        s=d.job_get(jid)
        if s["state"] in TERMINAL:return s
        await asyncio.sleep(.05)
    raise RuntimeError("timeout")

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-takeover-") as td:
        base=Path(td);ws=base/"ws";rt=base/"rt";ws.mkdir()
        auth={"client":"a"}
        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=2,poll_ms=20))
        m=MultiAgentService(MultiAgentConfig(ws,rt,task_lease_ttl_seconds=120,poll_ms=20),d,auth_client_resolver=lambda:auth["client"])
        a=await m.agent_register("ra","a","ia",[]); auth["client"]="b"; b=await m.agent_register("rb","b","ib",[])
        p=await m.project_register("p",".",4); t=await m.task_create("t",p["project_id"],"job")
        c1=await m.task_claim("c1",t["task_id"],a["agent_id"],a["session_id"])
        await m.path_lease_acquire("root1",t["task_id"],c1["lease_token"],c1["lease_epoch"],".","TREE")
        await d.start(); await m.start()
        try:
            j=await m.task_job_submit("j1",t["task_id"],c1["lease_token"],c1["lease_epoch"],[sys.executable,"-c","import time; print('alive',flush=True); time.sleep(3); print('done',flush=True)"])
            for _ in range(100):
                if d.job_get(j["job_id"])["state"]=="RUNNING":break
                await asyncio.sleep(.03)
            with d.db.transaction() as con:
                con.execute("UPDATE task_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
                con.execute("UPDATE path_leases SET expires_at_ms=? WHERE task_id=?",(now_ms()-1,t["task_id"]))
            m.reconciler.expire_sessions_and_leases()
            assert d.job_get(j["job_id"])["state"] in {"RUNNING","SUCCEEDED"}
            c2=await m.task_claim("c2",t["task_id"],b["agent_id"],b["session_id"])
            assert c2["lease_epoch"]==c1["lease_epoch"]+1
            assert any(x["job_id"]==j["job_id"] for x in m.task_jobs(t["task_id"])["jobs"])
            stale_error=None
            try: await m.task_job_cancel("oldcancel",t["task_id"],c1["lease_token"],c1["lease_epoch"],j["job_id"])
            except DurableError as e: stale_error=e.code
            assert stale_error=="LEASE_STALE"
            final=await wait(d,j["job_id"],8)
            assert final["state"]=="SUCCEEDED"

            await m.path_lease_acquire("root2",t["task_id"],c2["lease_token"],c2["lease_epoch"],".","TREE")
            j2=await m.task_job_submit("j2",t["task_id"],c2["lease_token"],c2["lease_epoch"],[sys.executable,"-c","import time; print('cancelme',flush=True); time.sleep(10)"])
            for _ in range(100):
                if d.job_get(j2["job_id"])["state"]=="RUNNING":break
                await asyncio.sleep(.03)
            cancel=await m.task_job_cancel("newcancel",t["task_id"],c2["lease_token"],c2["lease_epoch"],j2["job_id"])
            end2=await wait(d,j2["job_id"],6)
            assert cancel["job_id"]==j2["job_id"]
            assert end2["state"] in {"CANCELLED","FAILED"}
            print(json.dumps({"verdict":"PASS","survived_job":final["state"],"old_token_error":stale_error,"takeover_epoch":c2["lease_epoch"],"current_cancel_terminal":end2["state"]},indent=2))
        finally:
            await m.stop(); await d.stop()
if __name__=="__main__": asyncio.run(main())