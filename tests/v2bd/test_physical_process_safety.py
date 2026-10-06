import json

from remotemcp.node.process_safety import ProcessSafetyProbe


class FakeProbe(ProcessSafetyProbe):
    def __init__(self,processes,roots):
        self._processes=processes
        self._roots=roots
        self.max_processes=8192
        self.max_job_roots=4096
        self.jobs=None

    def _enumerate_processes(self):
        return list(self._processes)

    def _job_roots(self):
        return list(self._roots),0


def test_terminal_job_descendant_surviving_after_root_is_blocker(monkeypatch):
    monkeypatch.delenv("REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS",raising=False)
    probe=FakeProbe(
        [
            {
                "pid":101,
                "ppid":100,
                "name":"llama-server.exe",
                "command_line":"llama-server.exe --port 8080",
                "created_at_ms":1_500,
            }
        ],
        [
            {
                "proxy_job_id":"rjob_x",
                "node_job_id":"job_x",
                "pid":100,
                "state":"SUCCEEDED",
                "started_at_ms":1_000,
                "terminal_at_ms":2_000,
            }
        ],
    )
    out=probe.snapshot()
    assert out["physical_process_safety_resolved"]==1
    assert out["physical_process_blocker_count"]==1
    assert out["physical_process_residual_job_count"]==1
    summary=json.loads(out["physical_process_blocker_summary_json"])
    assert summary[0]["source"]=="terminal_job_process_tree"
    assert summary[0]["pid"]==101
    assert summary[0]["proxy_job_id"]=="rjob_x"


def test_active_job_tree_is_observed_but_not_double_blocked(monkeypatch):
    monkeypatch.delenv("REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS",raising=False)
    probe=FakeProbe(
        [
            {
                "pid":100,
                "ppid":1,
                "name":"python.exe",
                "command_line":"python worker.py",
                "created_at_ms":1_100,
            },
            {
                "pid":101,
                "ppid":100,
                "name":"child.exe",
                "command_line":"child.exe",
                "created_at_ms":1_200,
            },
        ],
        [
            {
                "proxy_job_id":"rjob_x",
                "node_job_id":"job_x",
                "pid":100,
                "state":"RUNNING",
                "started_at_ms":1_000,
                "terminal_at_ms":None,
            }
        ],
    )
    out=probe.snapshot()
    assert out["physical_process_safety_resolved"]==1
    assert out["physical_process_blocker_count"]==0
    assert out["physical_process_active_job_tree_count"]==2


def test_declared_long_lived_process_is_blocker_without_job_provenance(monkeypatch):
    monkeypatch.setenv(
        "REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS",
        "llama-server;ollama serve",
    )
    probe=FakeProbe(
        [
            {
                "pid":201,
                "ppid":1,
                "name":"llama-server.exe",
                "command_line":"llama-server.exe --model model.gguf",
                "created_at_ms":5_000,
            },
            {
                "pid":202,
                "ppid":1,
                "name":"notepad.exe",
                "command_line":"notepad.exe",
                "created_at_ms":5_000,
            },
        ],
        [],
    )
    out=probe.snapshot()
    assert out["physical_process_blocker_count"]==1
    assert out["physical_process_declared_long_lived_count"]==1
    assert out["physical_process_declared_pattern_count"]==2
    summary=json.loads(out["physical_process_blocker_summary_json"])
    assert summary[0]["source"]=="declared_long_lived"
    assert summary[0]["pid"]==201
    assert "command_line" not in summary[0]


def test_pid_reuse_before_job_start_is_not_residual(monkeypatch):
    monkeypatch.delenv("REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS",raising=False)
    probe=FakeProbe(
        [
            {
                "pid":100,
                "ppid":1,
                "name":"unrelated.exe",
                "command_line":"unrelated.exe",
                "created_at_ms":100,
            }
        ],
        [
            {
                "proxy_job_id":"rjob_old",
                "node_job_id":"job_old",
                "pid":100,
                "state":"SUCCEEDED",
                "started_at_ms":10_000,
                "terminal_at_ms":11_000,
            }
        ],
    )
    out=probe.snapshot()
    assert out["physical_process_blocker_count"]==0
    assert out["physical_process_residual_job_count"]==0


def test_probe_failure_is_fail_closed(monkeypatch):
    class BrokenProbe(FakeProbe):
        def _enumerate_processes(self):
            raise RuntimeError("boom")

    monkeypatch.delenv("REMOTEMCP_LONG_LIVED_PROCESS_PATTERNS",raising=False)
    out=BrokenProbe([],[]).snapshot()
    assert out["physical_process_safety_resolved"]==0
    assert out["physical_process_blocker_count"]==-1
    assert out["physical_process_error"]=="RuntimeError"
