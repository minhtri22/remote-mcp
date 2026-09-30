"""Run V2-0 pytest suite from the frozen RemoteMCP run_command environment.

The current run_command intentionally passes only PATH + HOME. On Windows this
breaks asyncio/anyio before pytest can load conftest.py. This bootstrap restores
only the host variables required to start the test runner. It does NOT change
RemoteMCP runtime behavior.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", r"C:\Users\minht")

import pytest

ROOT = Path(__file__).resolve().parents[1]
args = [str(ROOT / "tests"), "-q"]
raise SystemExit(pytest.main(args))