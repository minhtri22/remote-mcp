import os
from pathlib import Path
os.environ.setdefault("SystemRoot",r"C:\Windows")
os.environ.setdefault("WINDIR",r"C:\Windows")
os.environ.setdefault("USERPROFILE",os.path.expanduser("~"))
import pytest
ROOT=Path(__file__).resolve().parents[1]
raise SystemExit(pytest.main([str(ROOT/"tests"/"v2bd"),"-q"]))
