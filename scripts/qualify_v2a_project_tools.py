import os
os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", r"C:\Users\minht")

import asyncio, json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from remotemcp.durable.config import DurableConfig, DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService

TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}

async def run_job(svc,op,argv,cwd="."):
    sub=await svc.job_submit(op,argv,cwd=cwd)
    for _ in range(400):
        st=svc.job_get(sub["job_id"])
        if st["state"] in TERMINAL: break
        await asyncio.sleep(.05)
    out=svc.job_logs(sub["job_id"])["data"]
    err=svc.job_logs(sub["job_id"],"stderr")["data"]
    assert st["state"]=="SUCCEEDED",(argv,st,err)
    return (out.strip() or err.strip())

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2a-tools-") as t:
        rt=Path(t)/"rt"
        cfg=DurableConfig(Path(r"D:/WORK/RESEARCH"),rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=50)
        svc=DurableService(cfg); await svc.start()
        try:
            llama=Path(r"D:/WORK/RESEARCH/6.LTR/runs/R7_R8/legacy_tensor_runtime_r8_vk_completion_windows_2026-09-18/tools/llama_cpu/llama-cli.exe")
            checks={
                "ollama": await run_job(svc,"qual-ollama",["ollama","--version"]),
                "llama": await run_job(svc,"qual-llama",[str(llama),"--version"]),
                "cmake": await run_job(svc,"qual-cmake",["cmake","--version"]),
                "ctest": await run_job(svc,"qual-ctest",["ctest","--version"]),
                "ninja": await run_job(svc,"qual-ninja",["ninja","--version"]),
                "uv": await run_job(svc,"qual-uv",["uv","--version"]),
                "ffmpeg": await run_job(svc,"qual-ffmpeg",["ffmpeg","-version"]),
            }
            print(json.dumps({"verdict":"PASS","tools":checks},ensure_ascii=False,indent=2))
        finally:
            await svc.stop()
if __name__=="__main__": asyncio.run(main())
