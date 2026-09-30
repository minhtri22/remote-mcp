import os
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",r"C:\Users\minht")

import asyncio,json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService

def git(cmd):
    subprocess.run(cmd,check=True,capture_output=True,text=True)

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-scale-") as td:
        base=Path(td); ws=base/"ws"; rt=base/"rt"; ws.mkdir()
        repo=ws/"gitproj"; repo.mkdir()
        git(["git","init",str(repo)]); git(["git","-C",str(repo),"config","user.email","q@example.com"]); git(["git","-C",str(repo),"config","user.name","Q"])
        (repo/"README.md").write_text("base\n",encoding="utf-8")
        git(["git","-C",str(repo),"add","README.md"]); git(["git","-C",str(repo),"commit","-m","base"])
        nongit=ws/"nongit"; nongit.mkdir()
        auth={"client":"client-a"}
        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=3,poll_ms=20))
        m=MultiAgentService(MultiAgentConfig(ws,rt,task_lease_ttl_seconds=10,poll_ms=20),d,auth_client_resolver=lambda:auth["client"])
        agents=[]
        for i,c in enumerate(("client-a","client-b","client-c")):
            auth["client"]=c
            agents.append(await m.agent_register(f"reg-{i}",f"agent-{i}",f"install-{i}",[]))
        assert len({a["owner_account_id"] for a in agents})==1
        assert len({a["session_id"] for a in agents})==3
        pg=await m.project_register("pg","gitproj",4)
        pn=await m.project_register("pn","nongit",4)
        t1=await m.task_create("t1",pg["project_id"],"git-one")
        t2=await m.task_create("t2",pg["project_id"],"git-two")
        t3=await m.task_create("t3",pn["project_id"],"non-git")
        c1=await m.task_claim("c1",t1["task_id"],agents[0]["agent_id"],agents[0]["session_id"])
        c2=await m.task_claim("c2",t2["task_id"],agents[1]["agent_id"],agents[1]["session_id"])
        c3=await m.task_claim("c3",t3["task_id"],agents[2]["agent_id"],agents[2]["session_id"])
        s1=m.task_status(t1["task_id"]); s2=m.task_status(t2["task_id"]); s3=m.task_status(t3["task_id"])
        assert s1["state"]==s2["state"]==s3["state"]=="RUNNING"
        assert s1["worktree_rel"]!=s2["worktree_rel"]
        assert m.execution_root(t1["task_id"]).exists() and m.execution_root(t2["task_id"]).exists()
        for i,a in enumerate(agents,1):
            assert m.agent_heartbeat(a["agent_id"],a["session_id"],1)["renewed"]
        print(json.dumps({
            "verdict":"PASS","agents":3,"projects":2,
            "same_git_project_concurrent_tasks":2,
            "distinct_worktrees":True,
            "multi_login_clients":3,
            "owner_account_id":agents[0]["owner_account_id"]
        },indent=2))
if __name__=="__main__": asyncio.run(main())