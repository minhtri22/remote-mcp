import os
os.environ.setdefault("SystemRoot", r"C:\\Windows")
os.environ.setdefault("WINDIR", r"C:\\Windows")
os.environ.setdefault("USERPROFILE", r"C:\\Users\\minht")

import argparse, asyncio, json, shutil, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService

BASE=ROOT/".qualification"/"v2a-long"
STATE=BASE/"state.json"
TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}

def config():
    ws=BASE/"workspace"; rt=BASE/"runtime"; ws.mkdir(parents=True,exist_ok=True)
    return DurableConfig(ws,rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=100)

async def start():
    if BASE.exists(): shutil.rmtree(BASE)
    cfg=config(); svc=DurableService(cfg); await svc.start()
    try:
        sub=await svc.job_submit(
            "qual-long-610-op",
            [sys.executable,"-c","import time; print('LONG_START',flush=True); time.sleep(610); print('LONG_DONE',flush=True)"],
        )
        job_id=sub["job_id"]
        for _ in range(300):
            row=svc.jobs.get(job_id)
            log=svc.job_logs(job_id)["data"]
            if row["state"]=="RUNNING" and row["payload_fingerprint_json"] and "LONG_START" in log:
                break
            await asyncio.sleep(.05)
        assert row["state"]=="RUNNING", dict(row)
        payload=json.loads(row["payload_fingerprint_json"])
        data={"job_id":job_id,"operation_id":"qual-long-610-op","created_at_ms":row["created_at_ms"],"started_at_ms":row["started_at_ms"],"payload":payload}
        STATE.parent.mkdir(parents=True,exist_ok=True)
        STATE.write_text(json.dumps(data,indent=2),encoding="utf-8")
        print(json.dumps({"verdict":"STARTED","job_id":job_id,"payload_pid":payload["pid"],"started_at_ms":row["started_at_ms"]},indent=2))
    finally:
        await svc.stop()

def status():
    if not STATE.exists(): raise SystemExit("no long-job state")
    saved=json.loads(STATE.read_text(encoding="utf-8"))
    cfg=config(); svc=DurableService(cfg)
    row=svc.job_get(saved["job_id"])
    out={"job_id":saved["job_id"],"state":row["state"],"created_at_ms":row["created_at_ms"],"started_at_ms":row["started_at_ms"],"finished_at_ms":row["finished_at_ms"],"exit_code":row["exit_code"],"terminal_event_id":row["terminal_event_id"]}
    if row["state"] in TERMINAL:
        out["duration_ms"]=(row["finished_at_ms"] or 0)-(row["started_at_ms"] or row["created_at_ms"])
        out["stdout"]=svc.job_logs(saved["job_id"])["data"]
        out["result"]=svc.job_result(saved["job_id"])
        if row["state"]=="SUCCEEDED" and out["duration_ms"]>=610000 and "LONG_DONE" in out["stdout"]:
            out["verdict"]="PASS"
        else:
            out["verdict"]="FAIL"
    else:
        out["verdict"]="RUNNING"
    print(json.dumps(out,indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("action",choices=["start","status"]); a=p.parse_args()
    if a.action=="start": asyncio.run(start())
    else: status()
