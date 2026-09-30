import os
os.environ.setdefault("SystemRoot", r"C:\\Windows")
os.environ.setdefault("WINDIR", r"C:\\Windows")
os.environ.setdefault("USERPROFILE", r"C:\\Users\\minht")

import asyncio, json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2a-dup-") as t:
        base=Path(t); ws=base/"ws"; rt=base/"rt"; ws.mkdir()
        marker=ws/"executions.txt"
        cfg=DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=50)
        svc=DurableService(cfg); await svc.start()
        try:
            code=(
                "from pathlib import Path; "
                f"p=Path(r'{marker}'); "
                "f=p.open('a',encoding='utf-8'); f.write('one\\n'); f.close()"
            )
            a=await svc.job_submit("qual-dup-op",[sys.executable,"-c",code])
            b=await svc.job_submit("qual-dup-op",[sys.executable,"-c",code])
            assert a["job_id"]==b["job_id"]
            assert b["replayed"] is True
            for _ in range(300):
                st=svc.job_get(a["job_id"])
                if st["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}: break
                await asyncio.sleep(.05)
            assert st["state"]=="SUCCEEDED", st
            lines=marker.read_text(encoding="utf-8").splitlines()
            assert lines==["one"], lines
            n=svc.db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"]
            assert n==1
            print(json.dumps({"verdict":"PASS","job_id":a["job_id"],"replayed":b["replayed"],"payload_executions":len(lines),"jobs":n},indent=2))
        finally:
            await svc.stop()
if __name__=="__main__": asyncio.run(main())
