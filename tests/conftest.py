import os
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1]

# RemoteMCP's current run_command baseline only passes PATH + HOME to children.
# Restore the Windows variables required for Python networking/asyncio before
# importing the application under test. V2-0 freezes this as a known defect;
# these values are test-harness bootstrap, not a runtime fix.
os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", r"C:\Users\minht")
os.environ.setdefault("PUBLIC_URL", "http://localhost:8765")
os.environ.setdefault("OWNER_PASSWORD", "correct-horse-battery")
os.environ.setdefault("MCP_ROOT", str(SRC_ROOT / ".v2test-root"))
os.environ.setdefault("MCP_STATE", str(SRC_ROOT / ".v2test-state.json"))

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
