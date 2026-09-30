import os
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",r"C:\Users\minht")

import asyncio,json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService

def run(cmd):subprocess.run(cmd,check=True,capture_output=True,text=True)

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-wtrec-") as td:
        base=Path(td);ws=base/"ws";rt=base/"rt";ws.mkdir();repo=ws/"repo";repo.mkdir()
        run(["git","init",str(repo)]);run(["git","-C",str(repo),"config","user.email","q@example.com"]);run(["git","-C",str(repo),"config","user.name","Q"])
        (repo/"README.md").write_text("base\n",encoding="utf-8");run(["git","-C",str(repo),"add","README.md"]);run(["git","-C",str(repo),"commit","-m","base"])
        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS))
        m=MultiAgentService(MultiAgentConfig(ws,rt,task_lease_ttl_seconds=120),d,auth_client_resolver=lambda:"client")
        a=await m.agent_register("ra","a","ia",[])
        p=await m.project_register("p","repo",4)

        # Crash window 1: DB claim committed before worktree creation.
        t1=await m.task_create("t1",p["project_id"],"one")
        op,_=d.operations.reserve("claim1","TASK_CLAIM",{"task_id":t1["task_id"],"agent_id":a["agent_id"],"session_id":a["session_id"]},principal_key=m.identity.owner_account_id,agent_id=a["agent_id"],task_id=t1["task_id"])
        d.operations.mark_executing("claim1")
        l1=m.leases.claim_phase1(t1["task_id"],a["agent_id"],a["session_id"])
        assert m.task_status(t1["task_id"])["state"]=="CLAIMED"
        m.reconciler.reconcile_all()
        assert m.task_status(t1["task_id"])["state"]=="RUNNING"
        assert d.operations.get("claim1")["state"]=="SUCCEEDED"

        # Crash window 2: worktree already created before RUNNING DB finalize.
        t2=await m.task_create("t2",p["project_id"],"two")
        op,_=d.operations.reserve("claim2","TASK_CLAIM",{"task_id":t2["task_id"],"agent_id":a["agent_id"],"session_id":a["session_id"]},principal_key=m.identity.owner_account_id,agent_id=a["agent_id"],task_id=t2["task_id"])
        d.operations.mark_executing("claim2")
        l2=m.leases.claim_phase1(t2["task_id"],a["agent_id"],a["session_id"])
        task2=m.tasks.get(t2["task_id"]); project=m.projects.get(p["project_id"])
        m.worktrees.provision(m.projects.root_path(project),task2)
        m.reconciler.reconcile_all()
        assert m.task_status(t2["task_id"])["state"]=="RUNNING"
        rows=m.worktrees.list_worktrees(repo)
        branches=[str(x.get("branch","")).removeprefix("refs/heads/") for x in rows]
        assert branches.count(m.task_status(t1["task_id"])["branch_name"])==1
        assert branches.count(m.task_status(t2["task_id"])["branch_name"])==1
        print(json.dumps({"verdict":"PASS","claim_before_worktree_recovered":True,"worktree_before_finalize_recovered":True,"duplicate_worktrees":False},indent=2))
if __name__=="__main__": asyncio.run(main())