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

async def run_job(svc,op,argv,cwd="."):
    sub=await svc.job_submit(op,argv,cwd=cwd)
    for _ in range(300):
        st=svc.job_get(sub["job_id"])
        if st["state"] in TERMINAL: break
        await asyncio.sleep(.05)
    return st,svc.job_logs(sub["job_id"])["data"],svc.job_logs(sub["job_id"],"stderr")["data"]

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2a-tools-") as t:
        rt=Path(t)/"rt"
        cfg=DurableConfig(Path(r"D:/WORK/RESEARCH"),rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=50)
        svc=DurableService(cfg); await svc.start()
        try:
            o,oo,oe=await run_job(svc,"qual-ollama",["ollama","--version"])
            assert o["state"]=="SUCCEEDED",(o,oe)
            llama=Path(r"D:/WORK/RESEARCH/6.LTR/runs/R7_R8/legacy_tensor_runtime_r8_vk_completion_windows_2026-09-18/tools/llama_cpu/llama-cli.exe")
            l,lo,le=await run_job(svc,"qual-llama",[str(llama),"--version"])
            assert l["state"]=="SUCCEEDED",(l,le)
            print(json.dumps({"verdict":"PASS","ollama":oo.strip() or oe.strip(),"llama":lo.strip() or le.strip()},ensure_ascii=False,indent=2))
        finally:
            await svc.stop()
if __name__=="__main__": asyncio.run(main())
