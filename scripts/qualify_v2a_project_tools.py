import os
os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", os.path.expanduser("~"))

import asyncio, json, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def load_private_qualification_env():
    path=ROOT/".env"
    if not path.is_file():
        return
    allowed={"REMOTEMCP_QUAL_WORKSPACE","REMOTEMCP_QUAL_LLAMA_CLI"}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line=raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key,value=line.split("=",1)
        key=key.strip()
        if key not in allowed or key in os.environ:
            continue
        value=value.strip().strip('"').strip("'")
        if value:
            os.environ[key]=value
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
    load_private_qualification_env()
    workspace_raw=os.environ.get("REMOTEMCP_QUAL_WORKSPACE","").strip()
    llama_raw=os.environ.get("REMOTEMCP_QUAL_LLAMA_CLI","").strip()
    if not workspace_raw or not llama_raw:
        raise SystemExit(
            "QUALIFICATION_CONFIG_REQUIRED: set REMOTEMCP_QUAL_WORKSPACE and "
            "REMOTEMCP_QUAL_LLAMA_CLI in the private environment"
        )
    with tempfile.TemporaryDirectory(prefix="rmcp-v2a-tools-") as t:
        rt=Path(t)/"rt"
        cfg=DurableConfig(Path(workspace_raw).resolve(),rt,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=1,poll_ms=50)
        svc=DurableService(cfg); await svc.start()
        try:
            llama=Path(llama_raw).resolve()
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
