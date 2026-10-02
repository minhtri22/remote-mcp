"""Frozen launcher/preflight for the REAL two-execution-machine pilot.

This file is intentionally NOT executed by
REMOTE_MCP_V2BD_ROUTING_IMPLEMENTATION_AND_ZERO_SCIENCE_QUALIFICATION.
The next gate must explicitly authorize physical execution.
"""
from __future__ import annotations
import argparse,json,os,socket
from pathlib import Path

FORBIDDEN=("arcllm","cqg","six","cldp","mindforge","cot-from-zero","remotemcp-src")

def load_private_env():
    env_path=Path(__file__).resolve().parents[1]/".env"
    if not env_path.is_file():
        return
    allowed={"REMOTEMCP_PILOT_HOST_A","REMOTEMCP_PILOT_HOST_B"}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
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

def expected_hosts():
    host_a=os.environ.get("REMOTEMCP_PILOT_HOST_A","").strip().upper()
    host_b=os.environ.get("REMOTEMCP_PILOT_HOST_B","").strip().upper()
    if not host_a or not host_b:
        raise SystemExit(
            "PILOT_CONFIG_REQUIRED: set REMOTEMCP_PILOT_HOST_A and "
            "REMOTEMCP_PILOT_HOST_B in the private deployment environment"
        )
    if host_a==host_b:
        raise SystemExit("PILOT_CONFIG_INVALID: pilot host names must be distinct")
    return {
        host_a:{"project":"pilot-machine-1","marker":"PHYSICAL_MACHINE_1_ONLY"},
        host_b:{"project":"pilot-machine-2","marker":"PHYSICAL_MACHINE_2_ONLY"},
    }

def main():
    load_private_env()
    p=argparse.ArgumentParser()
    p.add_argument("--execute",action="store_true")
    p.add_argument("--ack-isolated-pilot",action="store_true")
    a=p.parse_args()
    host=socket.gethostname().upper()
    EXPECTED=expected_hosts()
    user=Path(os.environ.get("USERPROFILE",str(Path.home()))).resolve()
    root=(user/"RemoteMCP-V2BD-Pilot"/"workspace").resolve()
    runtime=(user/"RemoteMCP-V2BD-Pilot"/"runtime").resolve()
    lowered=(str(root)+" "+str(runtime)).lower()
    repo=Path(__file__).resolve().parents[1]
    plan=(repo/"docs"/"REMOTE_MCP_V2_PLAN.md").read_text(encoding="utf-8")
    gate="REMOTE_MCP_V2BD_REAL_TWO_EXECUTION_MACHINE_ISOLATED_PILOT_EXECUTION"
    checks={
        "host":host,"expected_host":host in EXPECTED,
        "root":str(root),"runtime":str(runtime),
        "forbidden_research_path":any(x in lowered for x in FORBIDDEN),
        "root_absent_or_empty":(not root.exists()) or not any(root.iterdir()),
        "runtime_absent_or_empty":(not runtime.exists()) or not any(runtime.iterdir()),
        "gate_open":f"Current gate: {gate}" in plan,
    }
    print(json.dumps(checks,indent=2))
    if not a.execute:
        raise SystemExit("PILOT_LOCKED: implementation qualification may not execute the real two-machine pilot")
    if not a.ack_isolated_pilot:
        raise SystemExit("PILOT_LOCKED: --ack-isolated-pilot required")
    if (
        not checks["expected_host"]
        or checks["forbidden_research_path"]
        or not checks["root_absent_or_empty"]
        or not checks["runtime_absent_or_empty"]
        or not checks["gate_open"]
    ):
        raise SystemExit("PILOT_PREFLIGHT_FAILED")
    print(json.dumps({
        "verdict":"PASS",
        "gate":"REMOTE_MCP_V2BD_REAL_TWO_EXECUTION_MACHINE_ISOLATED_PILOT_EXECUTION",
        "host":host,
        "expected":EXPECTED[host],
        "root":str(root),
        "runtime":str(runtime),
    },indent=2))

if __name__=="__main__":main()