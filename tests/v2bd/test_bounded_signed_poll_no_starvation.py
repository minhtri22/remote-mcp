from __future__ import annotations

import asyncio
import dataclasses
import time

from conftest import pair_harness


def test_idle_signed_poll_finishes_before_upstream_idle_deadline(make_gateway, tmp_path):
    async def run():
        gw = make_gateway()
        node = await pair_harness(gw, tmp_path, "poll-short", "ZERO_SCIENCE")
        dev = gw.routing.devices.get(node.identity.device["device_id"])
        # Defensive bound must hold even if future configs request 25s.
        gw.routing.config = dataclasses.replace(
            gw.routing.config, poll_long_wait_seconds=25
        )
        for _ in range(3):
            started = time.monotonic()
            # Simulate a reverse proxy that times out idle responses after 4s.
            response = await asyncio.wait_for(gw.routing.poll_http(dev), timeout=4)
            elapsed = time.monotonic() - started
            assert response is None
            assert elapsed < 3.5, elapsed
    asyncio.run(run())


def test_six_managed_reads_both_tasks_do_not_starve_or_replay_jobs(make_gateway, tmp_path):
    async def run():
        gw = make_gateway()
        node = await pair_harness(gw, tmp_path, "poll-reader", "FIXTURE_MARKER")
        device = node.identity.device
        dev_id = device["device_id"]
        device_row = gw.routing.devices.get(dev_id)
        stop = asyncio.Event()
        counters = {"polls": 0, "commands": 0}

        async def signed_poll_pump():
            while not stop.is_set():
                # The real node signs its polling POST; the loopback harness
                # performs the equivalent service call with a paired identity.
                cmd = await asyncio.wait_for(gw.routing.poll_http(device_row), timeout=4)
                counters["polls"] += 1
                if cmd is None:
                    continue
                counters["commands"] += 1
                receipt = await node.executor.execute(cmd)
                gw.routing.result_http(device_row, cmd["command_id"], receipt)

        pump = asyncio.create_task(signed_poll_pump())
        try:
            # No scientific task gets submitted; only two ephemeral fixture
            # task worktrees and their read-only directory commands.
            project = await asyncio.wait_for(
                gw.routing.project_register_on_device("poll-project", dev_id, "pilot", 4),
                timeout=12,
            )
            agent = gw.multi.agent_register("poll-agent", "infra-ci", "test-install", [])
            ids = []
            for idx in range(2):
                task = await asyncio.wait_for(
                    gw.routing.task_create_or_local(f"poll-task-{idx}", project["project_id"], f"probe-{idx}"),
                    timeout=12,
                )
                await asyncio.wait_for(
                    gw.routing.task_claim_or_local(
                        f"poll-claim-{idx}",task["task_id"],agent["agent_id"],agent["session_id"],
                    ),
                    timeout=12,
                )
                ids.append(task["task_id"])

            durations = []
            for i in range(6):
                started = time.monotonic()
                report = await asyncio.wait_for(
                    gw.routing.task_list_dir(ids[i % 2], "."),
                    timeout=12,
                )
                durations.append(time.monotonic() - started)
                assert "entries" in report
                assert report["task_id"] == ids[i % 2]
                assert any(x["name"] == "DEVICE_MARKER.txt" for x in report["entries"])

            assert max(durations) < 8.0, durations
            assert counters["commands"] >= 10
            row = gw.routing.db.query_one("SELECT COUNT(*) n FROM routed_jobs")
            assert int(row["n"]) == 0
            cmd_rows = gw.routing.db.query_all(
                "SELECT state,command_type,created_at_ms,finished_at_ms,delivery_attempt "
                "FROM device_commands WHERE command_type='TASK_LIST_DIR'"
            )
            assert len(cmd_rows) == 6
            assert all(r["state"] == "SUCCEEDED" for r in cmd_rows)
            assert all(int(r["delivery_attempt"]) == 1 for r in cmd_rows)
            assert all(int(r["finished_at_ms"]) - int(r["created_at_ms"]) < 8000 for r in cmd_rows)
        finally:
            stop.set()
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
    asyncio.run(run())
