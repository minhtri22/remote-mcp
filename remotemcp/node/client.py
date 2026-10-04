from __future__ import annotations

import asyncio
import json
import platform
import socket
import sys
from pathlib import Path

import httpx

from remotemcp.durable.errors import DurableError
from .signing import sign_pair,signed_headers


def node_platform() -> dict:
    return {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


class NodeClient:
    def __init__(self,config,identity):
        self.config=config;self.identity=identity
        self.http=httpx.AsyncClient(base_url=config.origin,timeout=httpx.Timeout(35.0,connect=10.0))

    async def close(self):
        await self.http.aclose()

    async def pair(self,pairing_id:str,pairing_code:str,device_name:str)->dict:
        ts,nonce,sig=sign_pair(self.identity,pairing_id,device_name)
        payload={
            "pairing_id":pairing_id,"pairing_code":pairing_code,"device_name":device_name,
            "public_key_b64":self.identity.public_key_b64,"timestamp_ms":ts,"nonce":nonce,"signature_b64":sig,
            "platform":node_platform(),"capabilities":{"outbound_node":True},
        }
        r=await self.http.post("/device/v1/pair",json=payload)
        if r.status_code>=400:raise DurableError("DEVICE_PAIRING_INVALID",f"pair endpoint HTTP {r.status_code}",body=r.text[:1000])
        return r.json()

    async def _signed_post(self,path:str,payload:dict,*,timeout=None):
        body=json.dumps(payload,sort_keys=True,separators=(",",":")).encode("utf-8")
        headers={"Content-Type":"application/json",**signed_headers(self.identity,"POST",path,body)}
        r=await self.http.post(path,content=body,headers=headers,timeout=timeout)
        if r.status_code>=400:
            code="DEVICE_OFFLINE"
            try: code=r.json().get("error_code") or code
            except Exception: pass
            raise DurableError(code,f"node route HTTP {r.status_code}",body=r.text[:1000])
        return r

    async def heartbeat(
        self,
        active_node_jobs:int,
        unresolved_node_jobs:int=0,
        capacity_reconciliation_complete:bool=True,
        candidate_nonterminal_routed_jobs:int|None=None,
    )->dict:
        payload={
            "node_time_ms":__import__("time").time_ns()//1_000_000,
            "capabilities":{"outbound_node":True},
            "platform":node_platform(),
            "active_node_jobs":int(active_node_jobs),
            "active_node_jobs_unresolved":int(unresolved_node_jobs),
            "capacity_reconciliation_complete":bool(capacity_reconciliation_complete),
        }
        if candidate_nonterminal_routed_jobs is not None:
            payload["candidate_nonterminal_routed_jobs"]=int(
                candidate_nonterminal_routed_jobs
            )
        r=await self._signed_post(
            "/device/v1/heartbeat",
            payload,
        )
        return r.json()

    async def poll(self):
        r=await self._signed_post("/device/v1/poll",{"node_time_ms":__import__("time").time_ns()//1_000_000},timeout=httpx.Timeout(35.0,connect=10.0))
        if r.status_code==204:return None
        return r.json()

    async def result(self,command_id:str,payload:dict)->dict:
        r=await self._signed_post(f"/device/v1/commands/{command_id}/result",payload)
        return r.json()