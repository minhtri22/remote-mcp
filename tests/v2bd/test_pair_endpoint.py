from __future__ import annotations
import asyncio,httpx
from mcp.server.fastmcp import FastMCP
from remotemcp.node.config import NodeConfig
from remotemcp.node.db import NodeDatabase
from remotemcp.node.identity import NodeIdentity
from remotemcp.node.signing import sign_pair
from remotemcp.routing.http import register_device_routes

def test_pair_custom_route_without_oauth(make_gateway,tmp_path):
    async def run():
        b=make_gateway(); p=b.routing.pairing.begin("p","http-node")
        cfg=NodeConfig.create("http://127.0.0.1:9999",tmp_path/"r",tmp_path/"n")
        db=NodeDatabase(cfg.runtime_dir);db.bootstrap(); ident=NodeIdentity(cfg.runtime_dir,db)
        ts,n,s=sign_pair(ident,p["pairing_id"],"http-node")
        m=FastMCP("route-test",stateless_http=True,json_response=True)
        register_device_routes(m,b.routing)
        tr=httpx.ASGITransport(app=m.streamable_http_app())
        async with httpx.AsyncClient(transport=tr,base_url="http://test") as c:
            resp=await c.post("/device/v1/pair",json={"pairing_id":p["pairing_id"],"pairing_code":p["pairing_code"],"device_name":"http-node","public_key_b64":ident.public_key_b64,"timestamp_ms":ts,"nonce":n,"signature_b64":s})
            assert resp.status_code==200,resp.text
            assert resp.json()["device_id"].startswith("dev_")
    asyncio.run(run())
