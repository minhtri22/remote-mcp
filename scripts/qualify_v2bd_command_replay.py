from __future__ import annotations
import asyncio,json,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2bd_qual_common import gateway,pair_node
from remotemcp.durable.errors import DurableError

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-cmd-") as td:
        base=Path(td);g=gateway(base);root=base/"node";root.mkdir()
        node,dev=pair_node(g,root,base/"rt","cmd-node")
        g.durable.operations.reserve("op","QUAL_COMMAND_REPLAY",{"x":1},principal_key=g.routing.owner_account_id)
        cmd,_=g.routing.commands.create(dev["device_id"],"PROJECT_PROBE",{"path":"."},operation_id="op",operation_step=0)
        leased=g.routing.commands.poll(dev["device_id"],1)
        env=g.routing.commands.envelope(leased)
        first=await node.executor.execute(env)
        second=await node.executor.execute(env)
        assert first==second
        a=g.routing.result_http(g.routing.devices.get(dev["device_id"]),cmd["command_id"],first)
        b=g.routing.result_http(g.routing.devices.get(dev["device_id"]),cmd["command_id"],second)
        assert a["central_state"]==b["central_state"]=="SUCCEEDED"
        conflict=None
        bad=dict(second);bad["result"]={"different":True}
        try:g.routing.result_http(g.routing.devices.get(dev["device_id"]),cmd["command_id"],bad)
        except DurableError as e:conflict=e.code
        assert conflict=="COMMAND_CONFLICT"
        print(json.dumps({"verdict":"PASS","node_same_command_replayed":True,"central_duplicate_result_accepted":True,"conflicting_result":conflict},indent=2))
if __name__=="__main__":asyncio.run(main())