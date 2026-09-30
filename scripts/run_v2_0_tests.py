"""Run only the frozen V2-0 regression denominator (17 tests).

The bootstrap keeps the test runner independent from the invoking process
environment and does not add V2-A tests to the V2-0 denominator.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", r"C:\Users\minht")

import pytest

ROOT = Path(__file__).resolve().parents[1]
args = [
    str(ROOT / "tests" / "test_baseline_contract.py"),
    str(ROOT / "tests" / "test_oauth_provider_unit.py"),
    "-q",
]
raise SystemExit(pytest.main(args))