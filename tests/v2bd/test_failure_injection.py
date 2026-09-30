from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import pair_harness
from remotemcp.durable.errors import DurableError
from remotemcp.durable.models import now_ms
from remotemcp.durable.process import windows_process_options


def test_revoke_queued_and_leased_command_semantics(make_gateway,tmp_path):
    async def run():
        g=make_gateway(); n=await pair_harness(g,tmp_path,"node-a"); did=n.device["device_id"]
        q,_=g.routing.commands.create(device_id=did,command_type="PROJECT_PROBE",payload={"path":"pilot"})
        l,_=g.routing.commands.create(device_id=did,command_type="PROJECT_PROBE",payload={"path":"pilot"})
        g.routing.node_poll(did)
        # oldest is q and is leased; l remains queued.
        await g.routing.device_revoke("rev",did,"inject")
        assert g.routing.commands.get(q["command_id"])["state"]=="IN_DOUBT"
        assert g.routing.commands.get(l["command_id"])["state"]=="CANCELLED"
    asyncio.run(run())


def test_offline_device_refuses_new_command_without_failover(make_gateway,tmp_path):
    async def run():
        g=make_gateway(); n=await pair_harness(g,tmp_path,"node-a")
        with g.durable.db.transaction() as con:
            con.execute("update devices set state='OFFLINE' where device_id=?",(n.device["device_id"],))
        with pytest.raises(DurableError) as exc:
            g.routing.commands.create(device_id=n.device["device_id"],command_type="PROJECT_PROBE",payload={"path":"pilot"})
        assert exc.value.code=="DEVICE_OFFLINE"
    asyncio.run(run())


def test_rjob_cannot_enter_gateway_local_job_api(make_gateway):
    g=make_gateway()
    with pytest.raises(DurableError) as exc:g.routing.guard_local_job_id("rjob_fake")
    assert exc.value.code=="ROUTED_JOB_USE_TASK_TOOLS"

def _visible_window_pids() -> set[int]:
    if os.name != "nt":
        return set()
    user32=ctypes.windll.user32
    pids=set()
    WNDENUMPROC=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    def cb(hwnd,_):
        if user32.IsWindowVisible(hwnd):
            pid=ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value:
                pids.add(int(pid.value))
        return True
    user32.EnumWindows(WNDENUMPROC(cb),0)
    return pids


def _foreground_pid() -> int:
    if os.name != "nt":
        return 0
    user32=ctypes.windll.user32
    hwnd=user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid=ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    return int(pid.value)


def test_windows_headless_creation_flags_default_and_debug(monkeypatch):
    if os.name!="nt":
        pytest.skip("Windows-only")
    monkeypatch.delenv("REMOTEMCP_SHOW_CONSOLE",raising=False)
    flags,si=windows_process_options()
    assert flags & getattr(subprocess,"CREATE_NO_WINDOW",0x08000000)
    assert flags & getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0x00000200)
    assert not (flags & getattr(subprocess,"DETACHED_PROCESS",0x00000008))
    assert si is not None
    assert si.dwFlags & getattr(subprocess,"STARTF_USESHOWWINDOW",0x00000001)
    assert si.wShowWindow==getattr(subprocess,"SW_HIDE",0)

    monkeypatch.setenv("REMOTEMCP_SHOW_CONSOLE","1")
    debug_flags,debug_si=windows_process_options()
    assert not (debug_flags & getattr(subprocess,"CREATE_NO_WINDOW",0x08000000))
    assert debug_flags & getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0x00000200)
    assert debug_si is None


def test_windows_headless_process_execution(make_gateway,monkeypatch):
    if os.name!="nt":
        pytest.skip("Windows-only")

    async def run():
        monkeypatch.setenv("REMOTEMCP_SHOW_CONSOLE","0")
        g=make_gateway()
        probe=g.workspace/"headless_probe.py"
        probe.write_text(
            "import ctypes,os,time\n"
            "print('payload_pid='+str(os.getpid()),flush=True)\n"
            "print('payload_console='+str(int(ctypes.windll.kernel32.GetConsoleWindow() or 0)),flush=True)\n"
            "time.sleep(2.0)\n",
            encoding="utf-8",
        )
        foreground_before=_foreground_pid()
        await g.durable.start()
        try:
            job=await g.durable.job_submit(
                "headless-long-job",
                [sys.executable,str(probe)],
                ".",
            )
            running=None
            worker_meta=None
            deadline=time.time()+8
            while time.time()<deadline:
                state=g.durable.job_get(job["job_id"])
                meta_path=g.runtime/"jobs"/job["job_id"]/"worker.json"
                if meta_path.exists():
                    worker_meta=json.loads(meta_path.read_text(encoding="utf-8"))
                if state["state"]=="RUNNING" and worker_meta and worker_meta.get("payload_fingerprint"):
                    running=state
                    break
                await asyncio.sleep(.05)
            assert running is not None
            assert worker_meta is not None
            worker_pid=int(worker_meta["worker_fingerprint"]["pid"])
            payload_pid=int(worker_meta["payload_fingerprint"]["pid"])
            assert int(worker_meta["worker_console_window_handle"])==0
            visible=_visible_window_pids()
            assert worker_pid not in visible
            assert payload_pid not in visible
            assert _foreground_pid() not in {worker_pid,payload_pid}
            if foreground_before:
                assert foreground_before not in {worker_pid,payload_pid}

            deadline=time.time()+8
            while time.time()<deadline:
                state=g.durable.job_get(job["job_id"])
                if state["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
                    break
                await asyncio.sleep(.05)
            assert state["state"]=="SUCCEEDED"
            logs=g.durable.job_logs(job["job_id"],"stdout",0,65536)
            assert "payload_console=0" in logs["data"]

            ollama=shutil.which("ollama")
            if ollama:
                oj=await g.durable.job_submit(
                    "headless-ollama-version",
                    [ollama,"--version"],
                    ".",
                )
                deadline=time.time()+8
                ometa=None
                while time.time()<deadline:
                    state=g.durable.job_get(oj["job_id"])
                    mp=g.runtime/"jobs"/oj["job_id"]/"worker.json"
                    if mp.exists():
                        ometa=json.loads(mp.read_text(encoding="utf-8"))
                    if state["state"] in {"SUCCEEDED","FAILED","CANCELLED","LOST"}:
                        break
                    await asyncio.sleep(.05)
                assert state["state"]=="SUCCEEDED"
                assert ometa is not None
                assert int(ometa["worker_console_window_handle"])==0
                out=g.durable.job_logs(oj["job_id"],"stdout",0,65536)["data"]
                assert "ollama" in out.lower()
        finally:
            await g.durable.stop()

    asyncio.run(run())
