import asyncio
from unittest.mock import AsyncMock

import pytest
from codetrace_backend.config import AppConfig
from codetrace_backend.docker_adapter import DockerUnavailableError, ImageUnavailableError
from codetrace_backend.runs import RunRejected
from codetrace_backend.transport.lifecycle import Runtime

from .helpers import FakeRuntime


@pytest.mark.parametrize(
    "reason,error",
    [
        ("DOCKER_UNAVAILABLE", DockerUnavailableError()),
        ("IMAGE_UNAVAILABLE", ImageUnavailableError("identity")),
    ],
)
def test_actual_health_checks_docker_and_image(monkeypatch, tmp_path, reason, error):
    async def scenario():
        fake = FakeRuntime(tmp_path)
        runtime = Runtime(fake.config, fake.manager, fake.service)
        runtime.image = fake.image
        from codetrace_backend.transport import lifecycle

        monkeypatch.setattr(
            lifecycle.DockerAdapter,
            "check_available",
            AsyncMock(side_effect=error if reason == "DOCKER_UNAVAILABLE" else None),
        )
        monkeypatch.setattr(
            lifecycle.DockerAdapter,
            "verify_image",
            AsyncMock(side_effect=error if reason == "IMAGE_UNAVAILABLE" else None),
        )
        health = await runtime.refresh()
        assert not health["available"] and health["reason"] == reason
        await runtime.shutdown()

    asyncio.run(scenario())


def test_unconfirmed_storage_cleanup_blocks_runs(monkeypatch, tmp_path):
    async def scenario():
        from codetrace_backend.transport import lifecycle

        runtime = Runtime(AppConfig(temp_root=tmp_path, image_path=tmp_path / "missing"))
        monkeypatch.setattr(lifecycle, "cleanup_stale_results", AsyncMock(side_effect=OSError()))
        await runtime.startup()
        assert runtime.storage_failure
        with pytest.raises(RunRejected):
            await runtime.start("x=1", "8ba106dd-35b6-4945-a5db-e1d3120c743d", lambda event: None)
        await runtime.shutdown()

    asyncio.run(scenario())


def test_initialization_attempts_cleanup_before_admission(monkeypatch, tmp_path):
    async def scenario():
        from codetrace_backend.transport import lifecycle

        fake = FakeRuntime(tmp_path)
        runtime = Runtime(fake.config, fake.manager, fake.service)
        monkeypatch.setattr(lifecycle, "cleanup_stale_results", AsyncMock())
        monkeypatch.setattr(lifecycle.ImageIdentity, "load", lambda path: fake.image)
        fake.manager.startup_cleanup = AsyncMock()
        fake.manager.retry_cleanup = AsyncMock()
        monkeypatch.setattr(lifecycle.DockerAdapter, "check_available", AsyncMock())
        monkeypatch.setattr(lifecycle.DockerAdapter, "verify_image", AsyncMock())
        await runtime.startup()
        fake.manager.startup_cleanup.assert_awaited_once()
        assert (await runtime.refresh())["available"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_concurrent_start_serializes_register_start_bind_and_busy(tmp_path):
    async def scenario():
        runtime = FakeRuntime(tmp_path)
        first, second = await asyncio.gather(
            runtime.start("one", "8ba106dd-35b6-4945-a5db-e1d3120c743d", lambda event: None),
            runtime.start("two", "dd53c9a6-1c0e-4a60-b075-c95b00a23854", lambda event: None),
            return_exceptions=True,
        )
        assert isinstance(second, RunRejected) and second.code == "RUN_BUSY"
        assert not isinstance(first, BaseException)
        assert runtime.log.count("register") == 1 and runtime.log.count("bind") == 1
        await runtime.shutdown()

    asyncio.run(scenario())


def test_public_client_protocol_method_preserves_manager_scope(monkeypatch):
    from types import SimpleNamespace

    from codetrace_backend.runs import RunManager

    async def scenario():
        manager = RunManager()
        calls = []
        session = SimpleNamespace(
            runId="8ba106dd-35b6-4945-a5db-e1d3120c743d", terminate=calls.append
        )
        # Test-only injection exercises the added public API without changing WATCHDOG tests.
        manager._slot = SimpleNamespace(session=session)
        manager.client_protocol_error("dd53c9a6-1c0e-4a60-b075-c95b00a23854")
        assert not calls
        manager.client_protocol_error(session.runId)
        assert calls == ["CLIENT_PROTOCOL_ERROR"]

    asyncio.run(scenario())
