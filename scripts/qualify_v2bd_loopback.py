from __future__ import annotations
import asyncio,json,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2bd_qual_common import gateway,init_git,pair_node,drive

async def main():
    with tempfile.TemporaryDirectory(prefix="rmcp-v2bd-loop-") as td:
        base=Path(td);g=gateway(base)
        r1=base/"n1";r2=base/"n2";r1.mkdir();r2.mkdir()
        init_git(r1/"pilot-a","PHYSICAL_MACHINE_1_ONLY")
        init_git(r2/"pilot-b","PHYSICAL_MACHINE_2_ONLY")
        n1,d1=pair_node(g,r1,base/"rt1","loop-a")
        n2,d2=pair_node(g,r2,base/"rt2","loop-b")
        p1=await drive(g,n1,g.routing.project_register_on_device("p1",d1["device_id"],"pilot-a",4))
        p2=await drive(g,n2,g.routing.project_register_on_device("p2",d2["device_id"],"pilot-b",4))
        a=await g.multi.agent_register("areg","qual-agent","qual-install",[])
        t1=await g.routing.task_create_or_local("t1",p1["project_id"],"one")
        t2=await g.routing.task_create_or_local("t2",p2["project_id"],"two")
        c1=await drive(g,n1,g.routing.task_claim_or_local("c1",t1["task_id"],a["agent_id"],a["session_id"]))
        c2=await drive(g,n2,g.routing.task_claim_or_local("c2",t2["task_id"],a["agent_id"],a["session_id"]))
        x1=await drive(g,n1,g.routing.task_read_file(t1["task_id"],"DEVICE_MARKER.txt"))
        x2=await drive(g,n2,g.routing.task_read_file(t2["task_id"],"DEVICE_MARKER.txt"))
        assert "PHYSICAL_MACHINE_1_ONLY" in x1["data"]
        assert "PHYSICAL_MACHINE_2_ONLY" in x2["data"]
        assert n1.db.query_one("select 1 from node_tasks where task_id=?",(t2["task_id"],)) is None
        assert n2.db.query_one("select 1 from node_tasks where task_id=?",(t1["task_id"],)) is None
        print(json.dumps({"verdict":"PASS","device_a":d1["device_id"],"device_b":d2["device_id"],"no_cross_route":True,"claims":[c1["device_id"],c2["device_id"]]},indent=2))
if __name__=="__main__":asyncio.run(main())