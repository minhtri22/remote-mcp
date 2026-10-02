import os
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",os.path.expanduser("~"))

import asyncio,concurrent.futures,hashlib,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService
from remotemcp.durable.errors import DurableError

def h(b):return hashlib.sha256(b).hexdigest()

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2b-casrace-") as td:
        base=Path(td);ws=base/"ws";rt=base/"rt";ws.mkdir()
        d=DurableService(DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS))
        m=MultiAgentService(MultiAgentConfig(ws,rt,task_lease_ttl_seconds=120),d,auth_client_resolver=lambda:"client")
        a=await m.agent_register("ra","a","ia",[])
        p=await m.project_register("p",".",4);t=await m.task_create("t",p["project_id"],"cas")
        c=await m.task_claim("c",t["task_id"],a["agent_id"],a["session_id"])
        await m.path_lease_acquire("l",t["task_id"],c["lease_token"],c["lease_epoch"],"race.txt","FILE")
        target=ws/"race.txt";target.write_text("base",encoding="utf-8")
        expected=h(b"base")
        def one(op,text):
            try:
                return ("ok",m.cas.write(op,t["task_id"],c["lease_token"],c["lease_epoch"],"race.txt",expected,text))
            except DurableError as e:
                return ("err",e.code)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            r=list(ex.map(lambda x:one(*x),[("w1","one"),("w2","two")]))
        oks=[x for x in r if x[0]=="ok"];errs=[x for x in r if x[0]=="err"]
        assert len(oks)==1 and len(errs)==1, r
        assert errs[0][1]=="CAS_MISMATCH",r
        assert target.read_text(encoding="utf-8") in {"one","two"}
        assert d.db.query_one("SELECT COUNT(*) AS n FROM cas_mutations WHERE state='COMMITTED'")["n"]==1
        print(json.dumps({"verdict":"PASS","committed":1,"cas_mismatch":1,"final":target.read_text(encoding="utf-8")},indent=2))
if __name__=="__main__": asyncio.run(main())