from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

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
    async def close(self): pass


class Bundle:
    pass


@pytest.fixture
def make_gateway(tmp_path):
    made=[]
    def factory():
        b=Bundle()
        idx=len(made)
        b.workspace=tmp_path/f"gateway-ws-{idx}"
        b.runtime=tmp_path/f"gateway-rt-{idx}"
        b.workspace.mkdir(parents=True)
        b.auth={"client":f"test-client-{idx}"}
        b.durable=DurableService(DurableConfig(
            b.workspace,b.runtime,DEFAULT_DURABLE_ALLOWED_CMDS,
            max_parallel_jobs=4,poll_ms=20,starting_grace_seconds=2,
        ))
        b.multi=MultiAgentService(
            MultiAgentConfig(
                workspace_root=b.workspace,runtime_dir=b.runtime,
                heartbeat_interval_seconds=30,task_lease_ttl_seconds=120,
                max_project_active_tasks_default=4,poll_ms=20,
            ),
            b.durable,auth_client_resolver=lambda:b.auth["client"],
        )
        b.routing=RoutingService(
            RoutingConfig.from_env(b.runtime,"http://127.0.0.1:9999"),
            b.durable,b.multi,
        )
        made.append(b)
        return b
    return factory


def init_git_repo(path:Path,marker="NODE"):
    path.mkdir(parents=True,exist_ok=True)
    for cmd in (
        ["git","init",str(path)],
        ["git","-C",str(path),"config","user.email","v2bd@example.com"],
        ["git","-C",str(path),"config","user.name","V2BD Test"],
    ):
        subprocess.run(cmd,check=True,capture_output=True,text=True)
    (path/"DEVICE_MARKER.txt").write_text(str(marker)+"\n",encoding="utf-8")
    subprocess.run(["git","-C",str(path),"add","DEVICE_MARKER.txt"],check=True,capture_output=True,text=True)
    subprocess.run(["git","-C",str(path),"commit","-m","pilot base"],check=True,capture_output=True,text=True)
    return path


async def pair_node(bundle,root:Path,runtime:Path,name="node-a"):
    root.mkdir(parents=True,exist_ok=True)
    cfg=NodeConfig.create("http://127.0.0.1:9999",root,runtime)
    ndb=NodeDatabase(runtime);ndb.bootstrap()
    ident=NodeIdentity(runtime,ndb)
    pair=bundle.routing.pairing.begin(f"pair-op-{name}",name)
    ts,nonce,sig=sign_pair(ident,pair["pairing_id"],name)
    resp=bundle.routing.pair_http({
        "pairing_id":pair["pairing_id"],"pairing_code":pair["pairing_code"],
        "device_name":name,"public_key_b64":ident.public_key_b64,
        "timestamp_ms":ts,"nonce":nonce,"signature_b64":sig,
        "platform":{
            "hostname":f"{name}-host",
            "system":"Windows",
            "release":"test",
            "machine":"x86_64",
            "python_version":"test",
        },
        "capabilities":{"outbound_node":True},
    })
    ident.persist_paired(resp,origin=cfg.origin,root=cfg.root,device_name=name)
    node=NodeService(cfg,client_factory=lambda *_:NullClient())
    node.root=cfg.root
    node.device=node.identity.device
    return node,resp


async def pair_harness(bundle,tmp_path:Path,name="node-a",marker="NODE"):
    root=tmp_path/name
    runtime=tmp_path/(name+"-runtime")
    root.mkdir(parents=True,exist_ok=True)
    init_git_repo(root/"pilot",marker)
    node,_=await pair_node(bundle,root,runtime,name)
    return node


async def pump_once(bundle,node):
    d=node.identity.device
    row=bundle.routing.commands.poll(d["device_id"],int(d["route_generation"]))
    if row is None:return False
    env=bundle.routing.commands.envelope(row)
    result=await node.executor.execute(env)
    bundle.routing.result_http(bundle.routing.devices.get(d["device_id"]),env["command_id"],result)
    return True


async def drive(bundle,node,coro,timeout=8):
    task=asyncio.create_task(coro)
    deadline=asyncio.get_running_loop().time()+timeout
    while not task.done():
        await pump_once(bundle,node)
        if asyncio.get_running_loop().time()>=deadline:
            task.cancel()
            raise AssertionError("gateway/node drive timeout")
        await asyncio.sleep(.005)
    return await task


async def pump_until(stop:asyncio.Event,bundle,node):
    while not stop.is_set():
        did=node.identity.device["device_id"]
        try:
            worked=await pump_once(bundle,node)
        except Exception:
            worked=False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(),timeout=.01)
            except asyncio.TimeoutError:
                pass
