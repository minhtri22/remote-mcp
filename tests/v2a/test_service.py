from __future__ import annotations

import asyncio
import sys

import pytest

from remotemcp.durable.errors import DurableError


TERMINAL={"SUCCEEDED","FAILED","CANCELLED","LOST"}


async def wait_terminal(svc, job_id, timeout=10):
    for _ in range(int(timeout*20)):
        row=svc.job_get(job_id)
        if row["state"] in TERMINAL:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("job did not become terminal")


def test_duplicate_submit_one_payload_and_conflict(make_service):
    async def run():
        svc,root,_=make_service()
        marker=root/"marker.txt"
        code=f"from pathlib import Path; p=Path(r'{marker}'); p.write_text((p.read_text() if p.exists() else '')+'x')"
        first=await svc.job_submit("op1",[sys.executable,"-c",code])
        second=await svc.job_submit("op1",[sys.executable,"-c",code])
        assert first["job_id"]==second["job_id"]
        assert second["replayed"] is True
        row=await wait_terminal(svc,first["job_id"])
        assert row["state"]=="SUCCEEDED"
        assert marker.read_text()=="x"
        with pytest.raises(DurableError) as exc:
            await svc.job_submit("op1",[sys.executable,"-c","print('different')"])
        assert exc.value.code=="OPERATION_CONFLICT"
        await svc.stop()
    asyncio.run(run())


def test_terminal_wait_redelivery_then_ack_suppresses(make_service):
    async def run():
        svc,_,_=make_service()
        sub=await svc.job_submit("op2",[sys.executable,"-c","print('hello')"])
        row=await wait_terminal(svc,sub["job_id"])
        assert row["state"]=="SUCCEEDED"

        a=await svc.job_wait(sub["job_id"],"subscriber",timeout_seconds=0)
        b=await svc.job_wait(sub["job_id"],"subscriber",timeout_seconds=0)
        assert a["changed"] and b["changed"]
        assert a["event_id"]==b["event_id"] and a["event_id"]>0

        res=svc.job_result(
            sub["job_id"],"subscriber",a["event_id"],"ack-op"
        )
        assert res["acked_event_id"]==a["event_id"]
        replay=svc.job_result(
            sub["job_id"],"subscriber",a["event_id"],"ack-op"
        )
        assert replay["acked_event_id"]==a["event_id"]

        c=await svc.job_wait(sub["job_id"],"subscriber",timeout_seconds=0)
        assert c["changed"] is False
        await svc.stop()
    asyncio.run(run())


def test_job_logs_byte_cursor_and_cancel(make_service):
    async def run():
        svc,_,_=make_service()
        sub=await svc.job_submit(
            "op3",
            [sys.executable,"-c","import time; print('abc', flush=True); time.sleep(10)"],
        )
        job_id=sub["job_id"]
        for _ in range(100):
            row=svc.job_get(job_id)
            if row["state"]=="RUNNING":
                break
            await asyncio.sleep(0.05)
        log=svc.job_logs(job_id,limit_bytes=2)
        assert log["next_cursor"]-log["cursor"]<=2
        cancel=await svc.job_cancel("cancel-op",job_id)
        assert cancel["job_id"]==job_id
        row=await wait_terminal(svc,job_id)
        assert row["state"] in {"CANCELLED","FAILED"}
        replay=await svc.job_cancel("cancel-op",job_id)
        assert replay["replayed"] is True
        await svc.stop()
    asyncio.run(run())

def test_job_wait_bounds(make_service):
    async def run():
        svc,_,_=make_service()
        sub=await svc.job_submit("op-bound",[sys.executable,"-c","print('x')"])
        with pytest.raises(DurableError):
            await svc.job_wait(sub["job_id"],"s",timeout_seconds=56)
        immediate=await svc.job_wait(sub["job_id"],"s",timeout_seconds=0)
        assert immediate["job_id"]==sub["job_id"]
        await svc.stop()
    asyncio.run(run())
