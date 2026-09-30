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

TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2a-restart-") as t:
        base=Path(t); ws=base/"ws"; rt=base/"rt"; ws.mkdir()
        marker=ws/"executions.txt"
        cfg=DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=50)
        svc1=DurableService(cfg); await svc1.start()
        code=(
            "import time; from pathlib import Path; "
            f"p=Path(r'{marker}'); "
            "f=p.open('a',encoding='utf-8'); f.write('one\\n'); f.close(); "
            "print('before-restart',flush=True); time.sleep(3); print('after-restart',flush=True)"
        )
        sub=await svc1.job_submit("qual-restart-op",[sys.executable,"-c",code])
        job_id=sub["job_id"]
        for _ in range(200):
            row=svc1.jobs.get(job_id)
            if row["state"]=="RUNNING" and row["payload_fingerprint_json"]:
                break
            await asyncio.sleep(.05)
        assert row["state"]=="RUNNING", dict(row)
        before=json.loads(row["payload_fingerprint_json"])
        await svc1.stop()

        svc2=DurableService(cfg); await svc2.start()
        try:
            for _ in range(300):
                st=svc2.job_get(job_id)
                if st["state"] in TERMINAL: break
                await asyncio.sleep(.05)
            assert st["state"]=="SUCCEEDED", st
            lines=marker.read_text(encoding="utf-8").splitlines()
            assert lines==["one"], lines
            after_row=svc2.jobs.get(job_id)
            after=json.loads(after_row["payload_fingerprint_json"])
            assert before["pid"]==after["pid"]
            assert before["start_token"]==after["start_token"]
            n=svc2.db.query_one("SELECT COUNT(*) AS n FROM jobs")["n"]
            assert n==1
            log=svc2.job_logs(job_id)["data"]
            assert "before-restart" in log and "after-restart" in log
            print(json.dumps({"verdict":"PASS","job_id":job_id,"same_payload_pid":True,"payload_executions":len(lines),"jobs":n},indent=2))
        finally:
            await svc2.stop()
if __name__=="__main__": asyncio.run(main())
