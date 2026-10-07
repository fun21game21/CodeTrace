"""Application lifetime and serialized WATCHDOG/RECORDER admission, without Run logic."""

import asyncio
from typing import cast

from codetrace_contracts.config import PYTHON_VERSION

from ..config import AppConfig
from ..docker_adapter import DockerAdapter, ImageIdentity
from ..docker_adapter.errors import DockerOperationError
from ..runs import RunManager, RunRejected, SlotLease
from ..runs.types import Subscriber
from ..trace_recorder import PublicationHandle, ResultService, TraceRecorder, cleanup_stale_results


class Runtime:
    def __init__(
        self,
        config: AppConfig,
        manager: RunManager | None = None,
        service: ResultService | None = None,
    ) -> None:
        self.config = config
        self.manager = RunManager() if manager is None else manager
        self.service = ResultService(clock=self.manager.clock) if service is None else service
        self.image: ImageIdentity | None = None
        self.storage_failure = False
        self.reason: str | None = "IMAGE_UNAVAILABLE"
        self.gate = asyncio.Lock()

    async def startup(self) -> None:
        try:
            await cleanup_stale_results(self.config.temp_root)
        except OSError:
            self.storage_failure = True
        try:
            self.image = ImageIdentity.load(self.config.image_path)
        except (OSError, ValueError, TypeError, KeyError):
            self.reason = "IMAGE_UNAVAILABLE"
            return
        await self.manager.startup_cleanup(self.image)
        await self.refresh()

    async def refresh(self) -> dict[str, object]:
        reason: str | None = None
        if self.image is None:
            reason = "IMAGE_UNAVAILABLE"
        else:
            adapter = DockerAdapter(self.image, self.config.limits)
            try:
                await adapter.check_available()
                await adapter.verify_image()
            except DockerOperationError as error:
                reason = error.reason
            if reason is None and not self.manager.availability.available:
                # Only the public manager recovery API may inspect/clean containers.
                try:
                    await self.manager.retry_cleanup(self.image)
                    self.service.recover_failed_ownership(self.manager)
                except RunRejected:
                    pass
        if reason is None:
            reason = "CLEANUP_FAILED" if self.storage_failure else self.manager.availability.reason
        self.reason = reason
        return {
            "apiVersion": 1,
            "available": reason is None and self.manager.availability.available,
            "reason": reason,
            "pythonVersion": PYTHON_VERSION,
            "limits": self.config.limits.copy(),
        }

    async def start(
        self, code: str, run_id: str, subscriber: Subscriber
    ) -> tuple[SlotLease, PublicationHandle]:
        async with self.gate:
            if self.manager.availability.busy:
                raise RunRejected("RUN_BUSY")
            health = await self.refresh()
            if not health["available"] or self.image is None:
                raise RunRejected(cast(str, health["reason"]) or "DOCKER_UNAVAILABLE")
            try:
                recorder = TraceRecorder(
                    run_id, self.config.limits, temp_root=self.config.temp_root
                )
            except OSError:
                raise RunRejected("TRACE_RECORDER_FAILURE") from None
            try:
                self.service.register(recorder, code)
            except BaseException:
                await recorder.dispose()
                raise
            try:
                lease = await self.manager.start_run(
                    run_id=run_id,
                    code=code,
                    image=self.image,
                    sink=recorder,
                    subscriber=subscriber,
                    limits=self.config.limits,
                )
            except BaseException:
                await self.service.abandon(run_id)
                raise
            # No await between accepting the lease and binding its publication owner.
            return lease, self.service.bind(lease)

    async def shutdown(self) -> None:
        try:
            await self.manager.shutdown()
        finally:
            await self.service.shutdown()
