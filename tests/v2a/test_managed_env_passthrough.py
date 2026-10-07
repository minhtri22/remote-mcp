from __future__ import annotations

import asyncio
import os
import sys

import pytest

from remotemcp.durable import config as durable_config


def test_safe_child_env_passes_nvidia_key_without_leaking_arbitrary_secret(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-synthetic")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    env = durable_config.safe_child_env(tmp_path)
    assert env["NVIDIA_API_KEY"] == "nvapi-test-synthetic"
    assert "UNRELATED_SECRET" not in env


def test_safe_child_env_supports_explicit_additional_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv(
        durable_config.MANAGED_ENV_ALLOWLIST_VAR,
        "CUSTOM_MODEL_KEY, SECOND_PROVIDER_TOKEN",
    )
    monkeypatch.setenv("CUSTOM_MODEL_KEY", "custom-synthetic")
    monkeypatch.setenv("SECOND_PROVIDER_TOKEN", "token-synthetic")
    env = durable_config.safe_child_env(tmp_path)
    assert env["CUSTOM_MODEL_KEY"] == "custom-synthetic"
    assert env["SECOND_PROVIDER_TOKEN"] == "token-synthetic"
    assert env[durable_config.MANAGED_ENV_ALLOWLIST_VAR] == (
        "CUSTOM_MODEL_KEY, SECOND_PROVIDER_TOKEN"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows persistent environment fallback")
def test_safe_child_env_recovers_allowlisted_value_from_persistent_windows_env(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv(durable_config.MANAGED_ENV_ALLOWLIST_VAR, raising=False)

    def fake_registry(key: str):
        if key == "NVIDIA_API_KEY":
            return "persisted-nvapi-synthetic"
        return None

    monkeypatch.setattr(durable_config, "_windows_registry_env_value", fake_registry)
    env = durable_config.safe_child_env(tmp_path)
    assert env["NVIDIA_API_KEY"] == "persisted-nvapi-synthetic"


def test_durable_worker_and_payload_receive_allowlisted_managed_env(
    make_service, monkeypatch
):
    async def run():
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-e2e-synthetic")
        monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak-e2e")
        svc, _root, _runtime = make_service(max_parallel_jobs=1, poll_ms=20)
        await svc.start()
        try:
            job = await svc.job_submit(
                "managed-env-e2e",
                [
                    sys.executable,
                    "-c",
                    (
                        "import os;"
                        "print(os.environ.get('NVIDIA_API_KEY','ABSENT'));"
                        "print(os.environ.get('UNRELATED_SECRET','ABSENT'))"
                    ),
                ],
            )
            state = None
            for _ in range(300):
                state = svc.job_get(job["job_id"])["state"]
                if state in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}:
                    break
                await asyncio.sleep(0.02)
            assert state == "SUCCEEDED"
            logs = svc.job_logs(job["job_id"], stream="stdout")
            lines = [line.strip() for line in logs["data"].splitlines() if line.strip()]
            assert "nvapi-e2e-synthetic" in lines
            assert "must-not-leak-e2e" not in lines
            assert "ABSENT" in lines
        finally:
            await svc.stop()

    asyncio.run(run())
