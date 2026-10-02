"""Run the existing full OAuth flow against an isolated local RemoteMCP server."""
import os
import subprocess
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("SystemRoot", r"C:\Windows")
os.environ.setdefault("WINDIR", r"C:\Windows")
os.environ.setdefault("USERPROFILE", os.path.expanduser("~"))

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://localhost:8765"


def wait_ready(timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("test server did not become ready")


def main():
    with tempfile.TemporaryDirectory(prefix="remotemcp-v2-0-") as tmp:
        tmp_path = Path(tmp)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "a.txt").write_text("hello\n", encoding="utf-8")

        env = os.environ.copy()
        env.update(
            {
                "PUBLIC_URL": BASE,
                "OWNER_PASSWORD": "correct-horse-battery",
                "MCP_ROOT": str(workspace),
                "MCP_STATE": str(tmp_path / "oauth-state.json"),
                "MCP_RUNTIME_DIR": str(tmp_path / "durable-runtime"),
                "PORT": "8765",
                "PYTHONIOENCODING": "utf-8",
            }
        )

        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "server.py")],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            try:
                wait_ready()
            except Exception:
                if proc.poll() is not None and proc.stdout is not None:
                    print("SERVER EXIT:", proc.returncode)
                    print(proc.stdout.read(), end="")
                raise
            result = subprocess.run(
                [sys.executable, str(ROOT / "test_oauth_flow.py")],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode != 0 or "FAIL " in result.stdout:
                raise SystemExit(result.returncode or 1)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()