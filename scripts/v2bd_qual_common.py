from __future__ import annotations
import asyncio,subprocess,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from remotemcp.durable.config import DurableConfig,DEFAULT_DURABLE_ALLOWED_CMDS
from remotemcp.durable.service import DurableService
from remotemcp.multiagent.config import MultiAgentConfig
from remotemcp.multiagent.service import MultiAgentService
from remotemcp.node.config import NodeConfig
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity
from remotemcp.node.service import NodeService
from remotemcp.node.signing import sign_pair
from remotemcp.routing.config import RoutingConfig
from remotemcp.routing.service import RoutingService

class NullClient:
    async def close(self):pass

class Bundle:pass

def gateway(base:Path):
    b=Bundle();b.workspace=base/"gateway-ws";b.runtime=base/"gateway-rt";b.workspace.mkdir(parents=True)
    b.auth={"client":"qual-client"}
    b.durable=DurableService(DurableConfig(b.workspace,b.runtime,DEFAULT_DURABLE_ALLOWED_CMDS,max_parallel_jobs=4,poll_ms=20,starting_grace_seconds=2))
    b.multi=MultiAgentService(MultiAgentConfig(workspace_root=b.workspace,runtime_dir=b.runtime,heartbeat_interval_seconds=30,task_lease_ttl_seconds=120,max_project_active_tasks_default=4,poll_ms=20),b.durable,auth_client_resolver=lambda:b.auth["client"])
    b.routing=RoutingService(RoutingConfig.from_env(b.runtime,"http://127.0.0.1:9999"),b.durable,b.multi)
    return b

def init_git(path:Path,marker:str):
    path.mkdir(parents=True,exist_ok=True)
    for cmd in (
        ["git","init",str(path)],
        ["git","-C",str(path),"config","user.email","qual@example.com"],
        ["git","-C",str(path),"config","user.name","V2BD Qual"],
    ):subprocess.run(cmd,check=True,capture_output=True,text=True)
    (path/"DEVICE_MARKER.txt").write_text(marker+"\n",encoding="utf-8")
    subprocess.run(["git","-C",str(path),"add","DEVICE_MARKER.txt"],check=True,capture_output=True,text=True)
    subprocess.run(["git","-C",str(path),"commit","-m","pilot"],check=True,capture_output=True,text=True)

def pair_node(g,root:Path,runtime:Path,name:str):
    cfg=NodeConfig.create("http://127.0.0.1:9999",root,runtime)
    db=NodeDatabase(runtime);db.bootstrap();ident=NodeIdentity(runtime,db)
    p=g.routing.pairing.begin("pair-"+name,name)
    ts,n,s=sign_pair(ident,p["pairing_id"],name)
    resp=g.routing.pair_http({"pairing_id":p["pairing_id"],"pairing_code":p["pairing_code"],"device_name":name,"public_key_b64":ident.public_key_b64,"timestamp_ms":ts,"nonce":n,"signature_b64":s})
    ident.persist_paired(resp,origin=cfg.origin,root=cfg.root,device_name=name)
    node=NodeService(cfg,client_factory=lambda *_:NullClient())
    node.device=node.identity.device;node.root=cfg.root
    return node,resp

async def pump_once(g,node):
    d=node.identity.device
    row=g.routing.commands.poll(d["device_id"],int(d["route_generation"]))
    if row is None:return False
    env=g.routing.commands.envelope(row)
    result=await node.executor.execute(env)
    g.routing.result_http(g.routing.devices.get(d["device_id"]),env["command_id"],result)
    return True

async def drive(g,node,coro,timeout=8):
    task=asyncio.create_task(coro); deadline=asyncio.get_running_loop().time()+timeout
    while not task.done():
        await pump_once(g,node)
        if asyncio.get_running_loop().time()>=deadline:
            task.cancel();raise RuntimeError("qualification drive timeout")
        await asyncio.sleep(.005)
    return await task