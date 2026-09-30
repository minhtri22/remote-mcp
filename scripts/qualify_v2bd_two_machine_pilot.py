"""Frozen launcher/preflight for the REAL two-execution-machine pilot.

This file is intentionally NOT executed by
REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION.
The next gate must explicitly authorize physical execution.
"""
from __future__ import annotations
import argparse,json,os,socket
from pathlib import Path

FORBIDDEN=("arcllm","cqg","six","cldp","mindforge","cot-from-zero","remotemcp-src")
EXPECTED={
    "DESKTOP-4PSD0G2":{"project":"pilot-machine-1","marker":"PHYSICAL_MACHINE_1_ONLY"},
    "DESKTOP-VKIC2RU":{"project":"pilot-machine-2","marker":"PHYSICAL_MACHINE_2_ONLY"},
}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--execute",action="store_true")
    p.add_argument("--ack-isolated-pilot",action="store_true")
    a=p.parse_args()
    host=socket.gethostname().upper()
    user=Path(os.environ.get("USERPROFILE",str(Path.home()))).resolve()
    root=(user/"RemoteMCP-V2BD-Pilot"/"workspace").resolve()
    runtime=(user/"RemoteMCP-V2BD-Pilot"/"runtime").resolve()
    lowered=(str(root)+" "+str(runtime)).lower()
    checks={
        "host":host,"expected_host":host in EXPECTED,
        "root":str(root),"runtime":str(runtime),
        "forbidden_research_path":any(x in lowered for x in FORBIDDEN),
        "root_absent_or_empty":(not root.exists()) or not any(root.iterdir()),
        "runtime_absent_or_empty":(not runtime.exists()) or not any(runtime.iterdir()),
    }
    print(json.dumps(checks,indent=2))
    if not a.execute:
        raise SystemExit("PILOT_LOCKED: implementation qualification may not execute the real two-machine pilot")
    if not a.ack_isolated_pilot:
        raise SystemExit("PILOT_LOCKED: --ack-isolated-pilot required")
    if not checks["expected_host"] or checks["forbidden_research_path"] or not checks["root_absent_or_empty"] or not checks["runtime_absent_or_empty"]:
        raise SystemExit("PILOT_PREFLIGHT_FAILED")
    raise SystemExit("PILOT_EXECUTION_NOT_OPENED_BY_CURRENT_GATE")

if __name__=="__main__":main()
