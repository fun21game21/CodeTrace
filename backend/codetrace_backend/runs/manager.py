"""Single slot and explicit terminal ownership handoff, without release/TTL transport."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from codetrace_contracts import default_limits, validate_contract
from codetrace_contracts.types import ResourceLimits

from ..docker_adapter import DockerAdapter, ImageIdentity
from ..docker_adapter.types import run_identity
from .protocol import encode, text_bytes
from .session import RunSession
from .timing import TERMINAL, Clock, SystemClock
from .types import InputSubmission, RecordSink, RunRejected, Subscriber, TerminalResult

AdapterFactory = Callable[[ImageIdentity, ResourceLimits], DockerAdapter]


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    busy: bool
    reason: str | None


class SlotLease:
    """Internal capability for RECORDER's result owner, not a wire release endpoint.

    The owner calls finish_ownership only after disposing its terminal result.
    No timeout, automatic expiry or active-run release is provided.
    """

    def __init__(self, manager: "RunManager", session: RunSession) -> None:
        self._manager, self.session = manager, session

    def finish_ownership(self) -> None:
        if self.session.status not in TERMINAL:
            raise RunRejected("RUN_BUSY")
        if self._manager._slot is self:
            self._manager._slot = None


class RunManager:
    def __init__(
        self, *, clock: Clock | None = None, adapter_factory: AdapterFactory = DockerAdapter
    ) -> None:
        self.clock = SystemClock() if clock is None else clock
        self._factory = adapter_factory
        self._slot: SlotLease | None = None
        self._blocked: str | None = None
        self._maintenance = False
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current:
            raise RuntimeError("RunManager belongs to one event loop")
        self._loop = current

    @property
    def availability(self) -> Availability:
        reason = "BACKEND_SHUTDOWN" if self._closed else self._blocked
        return Availability(
            not self._closed and reason is None and not self._maintenance,
            self._slot is not None,
            reason,
        )

    async def start_run(
        self,
        *,
        run_id: str,
        code: str,
        image: ImageIdentity,
        sink: RecordSink,
        subscriber: Subscriber,
        limits: ResourceLimits | None = None,
        checkpoint_interval: int | None = None,
    ) -> SlotLease:
        self._bind_loop()
        if not self.availability.available:
            raise RunRejected(self.availability.reason or "DOCKER_UNAVAILABLE")
        if self._slot is not None:
            raise RunRejected("RUN_BUSY")
        # No await before reservation/validation: simultaneous callers serialize
        # on this loop before any Docker call can start.
        effective = default_limits() if limits is None else limits.copy()
        try:
            run_identity(run_id)
            validate_contract(effective, "trace", "ResourceLimits")
            if type(code) is not str or (
                checkpoint_interval is not None
                and checkpoint_interval != effective["checkpoint_interval"]
            ):
                raise ValueError
            if text_bytes(code) > effective["code_bytes"]:
                raise RunRejected("CODE_TOO_LARGE")
            start = {
                "protocolVersion": 1,
                "runId": run_id,
                "seq": 1,
                "type": "start",
                "payload": {
                    "code": code,
                    "sourceFileName": "<codetrace-user>",
                    "limits": effective,
                    "checkpointInterval": effective["checkpoint_interval"],
                },
            }
            if len(encode(start)) > effective["runner_message_bytes"]:
                raise ValueError
        except (ValueError, TypeError):
            raise RunRejected("INVALID_START") from None
        session = RunSession(
            run_id,
            code,
            effective,
            self._factory(image, effective),
            sink,
            subscriber,
            self.clock,
            self._completed,
        )
        lease = self._slot = SlotLease(self, session)
        session.launch()
        return lease

    def _completed(self, result: TerminalResult) -> None:
        if not result.cleanupConfirmed:
            self._blocked = "CLEANUP_FAILED"

    async def submit_input(self, run_id: str, request_id: str, text: str) -> InputSubmission:
        self._bind_loop()
        if self._slot is None:
            return InputSubmission(False, "INVALID_INPUT")
        return await self._slot.session.submit_input(run_id, request_id, text)

    def connection_lost(self, run_id: str) -> None:
        self._bind_loop()
        if self._slot is not None and self._slot.session.runId == run_id:
            self._slot.session.terminate("CONNECTION_LOST")

    def client_protocol_error(self, run_id: str) -> None:
        """Terminate only this accepted Run for a malformed browser command."""
        self._bind_loop()
        if self._slot is not None and self._slot.session.runId == run_id:
            self._slot.session.terminate("CLIENT_PROTOCOL_ERROR")

    async def shutdown(self) -> TerminalResult | None:
        self._bind_loop()
        self._closed = True
        if self._slot is None:
            return None
        session = self._slot.session
        session.terminate("BACKEND_SHUTDOWN")
        return await session.await_result()

    async def startup_cleanup(self, image: ImageIdentity) -> Availability:
        self._bind_loop()
        if self._maintenance or (
            self._slot is not None and self._slot.session.status not in TERMINAL
        ):
            raise RunRejected("RUN_BUSY")
        self._maintenance = True
        self._blocked = "CLEANUP_FAILED"
        adapter = self._factory(image, default_limits())
        try:
            handles = await adapter.list_managed_containers()
            confirmed = True
            for handle in handles:
                try:
                    result = await adapter.cleanup_managed_container(handle)
                    confirmed = result.cleanupConfirmed and confirmed
                except Exception:
                    confirmed = False
            if confirmed and not await adapter.list_managed_containers():
                self._blocked = None
        except Exception:
            pass  # Enumeration or daemon failure never proves absence.
        finally:
            self._maintenance = False
        return self.availability

    async def retry_cleanup(self, image: ImageIdentity) -> Availability:
        return await self.startup_cleanup(image)
