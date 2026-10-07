"""Single retained result, HTTP-domain access and explicit ownership disposal."""

import asyncio
import copy
import os
from dataclasses import dataclass, replace
from typing import BinaryIO

from ..runs import Clock, LifecycleEvent, RunManager, SlotLease, SystemClock
from .completion import build_completion, publication_failure
from .recorder import TraceRecorder, check
from .types import (
    RecorderError,
    ResultAccessError,
    ResultSummary,
    RetainedTraceResult,
    RunMetadata,
)


@dataclass(slots=True)
class _Entry:
    recorder: TraceRecorder
    sourceCode: str
    acceptedAt: str | None = None
    deadline: float | None = None
    lease: SlotLease | None = None
    result: RetainedTraceResult | None = None
    task: asyncio.Task[ResultSummary] | None = None
    ttl: asyncio.Task[None] | None = None
    disposal: asyncio.Task[None] | None = None
    retiring: bool = False
    disposed: bool = False
    readers: int = 0
    ownershipFinished: bool = False
    blockedCleanup: bool = False


class TraceAccess:
    """An opened immutable file capability, acquired before release/TTL unlink.

    The future HTTP adapter must close this in a response-finally. At most two
    concurrent readers are allowed; unfinished readers keep slot ownership after
    unlink so old open files cannot accumulate across successive runs.
    """

    contentType = "application/json"

    def __init__(self, service: "ResultService", entry: _Entry, file: BinaryIO) -> None:
        self._service, self._entry, self._file = service, entry, file
        assert entry.result is not None
        self.traceBytes = entry.result.traceBytes

    def read(self, size: int = 65536) -> bytes:
        if type(size) is not int or not 0 < size <= 65536:
            raise ValueError("Use bounded reads")
        return self._file.read(size)

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
            self._entry.readers -= 1
            self._service._finish_disposed(self._entry)

    def __enter__(self) -> "TraceAccess":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class PublicationHandle:
    """A caller may keep this small handle even when no trace can be retained."""

    def __init__(self, task: asyncio.Task[ResultSummary]) -> None:
        self._task = task

    async def await_result(self) -> ResultSummary:
        return copy.deepcopy(await asyncio.shield(self._task))


class ResultService:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self.clock = SystemClock() if clock is None else clock
        self._entry: _Entry | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> None:
        current = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current:
            raise RuntimeError("ResultService belongs to one event loop")
        self._loop = current

    def register(self, recorder: TraceRecorder, source_code: str) -> None:
        """Before WATCHDOG start: reserve storage; failed start must call abandon()."""
        self._bind_loop()
        check(self._entry is None)
        RunMetadata(recorder.runId, source_code, "1970-01-01T00:00:00Z", recorder.limits)
        self._entry = _Entry(recorder, source_code)

    def observe(self, event: LifecycleEvent) -> None:
        """Compose into WATCHDOG's nonblocking subscriber. Never retain Console/input."""
        entry = self._entry
        if entry is None or entry.recorder.runId != event.runId:
            return
        if event.kind == "accepted":
            if not isinstance(event.payload, dict) or not isinstance(
                event.payload.get("acceptedAt"), str
            ):
                raise RecorderError()
            entry.acceptedAt = event.payload["acceptedAt"]
        elif event.kind == "status" and event.payload == "FINALIZING" and entry.deadline is None:
            entry.deadline = (
                self.clock.now() + entry.recorder.limits["finalization_timeout_ms"] / 1000
            )

    def bind(self, lease: SlotLease) -> PublicationHandle:
        """Immediate handoff after start_run returns, before yielding to Runner."""
        self._bind_loop()
        entry = self._entry
        check(entry is not None)
        assert entry is not None
        check(entry.lease is None and lease.session.runId == entry.recorder.runId)
        check(entry.acceptedAt == lease.session.acceptedAt)
        check(entry.recorder.limits == lease.session.limits)
        check(entry.sourceCode == lease.session._code)
        entry.lease = lease
        entry.task = asyncio.create_task(self._publish(entry))
        return PublicationHandle(entry.task)

    async def await_result(self, run_id: str) -> ResultSummary:
        self._bind_loop()
        entry = self._entry
        if entry is None or entry.recorder.runId != run_id or entry.task is None:
            raise ResultAccessError(404)
        return copy.deepcopy(await asyncio.shield(entry.task))

    @property
    def retained(self) -> RetainedTraceResult | None:
        entry = self._entry
        if entry is None or entry.retiring or entry.result is None:
            return None
        return replace(entry.result, completion=copy.deepcopy(entry.result.completion))

    async def _publish(self, entry: _Entry) -> ResultSummary:
        assert entry.lease is not None
        terminal = await entry.lease.session.await_result()
        try:
            completion = build_completion(terminal, entry.recorder.counters["eventCount"])
            if not terminal.traceUsable or not terminal.cleanupConfirmed:
                entry.blockedCleanup = not terminal.cleanupConfirmed
                await self._release_entry(entry)
                return ResultSummary(completion, False, None, None, None)
            check(entry.acceptedAt is not None and entry.deadline is not None)
            assert entry.acceptedAt is not None and entry.deadline is not None
            metadata = RunMetadata(
                terminal.runId, entry.sourceCode, entry.acceptedAt, entry.recorder.limits
            )
            sealed = await entry.recorder.seal(
                metadata, terminal, deadline=entry.deadline, clock=self.clock
            )
            entry.result = RetainedTraceResult(
                terminal.runId,
                sealed.path,
                sealed.traceBytes,
                sealed.eventCount,
                self.clock.utc(),
                self.clock.now() + metadata.limits["result_ttl_ms"] / 1000,
                completion,
                entry.lease,
            )
            entry.ttl = asyncio.create_task(self._expire(entry))
            return ResultSummary(
                completion,
                True,
                f"/api/runs/{terminal.runId}/trace",
                sealed.traceBytes,
                sealed.eventCount,
            )
        except Exception as error:
            reason = (
                "FINALIZATION_TIMEOUT"
                if isinstance(error, RecorderError) and error.reason == "FINALIZATION_TIMEOUT"
                else "TRACE_RECORDER_FAILURE"
            )
            if not terminal.cleanupConfirmed:
                reason = "CLEANUP_FAILED"
                entry.blockedCleanup = True
            completion = publication_failure(terminal, reason, self.clock.utc())
            await self._release_entry(entry)
            return ResultSummary(completion, False, None, None, None)

    async def _expire(self, entry: _Entry) -> None:
        assert entry.result is not None
        wake = asyncio.Event()
        while not entry.retiring and self.clock.now() < entry.result.expiresAt:
            wake.clear()
            await self.clock.wait(wake, entry.result.expiresAt - self.clock.now())
        if not entry.retiring:
            await self._release_entry(entry)

    def get_trace(self, run_id: str) -> TraceAccess:
        self._bind_loop()
        entry = self._entry
        if entry is None or entry.recorder.runId != run_id or entry.retiring:
            raise ResultAccessError(404)
        if entry.result is None:
            raise ResultAccessError(409)
        if self.clock.now() >= entry.result.expiresAt:
            # Scheduling delays cannot extend logical TTL. No await before retiring.
            self._start_disposal(entry)
            raise ResultAccessError(404)
        if entry.readers >= 2:
            raise ResultAccessError(409)
        # Synchronous open and reference acquisition are one loop-confined operation.
        descriptor = os.open(entry.result.path, os.O_RDONLY | os.O_NOFOLLOW)
        file = os.fdopen(descriptor, "rb", buffering=0)
        entry.readers += 1
        return TraceAccess(self, entry, file)

    async def release(self, run_id: str) -> int:
        self._bind_loop()
        entry = self._entry
        if entry is None or entry.recorder.runId != run_id:
            return 204
        if not entry.retiring and (entry.task is None or not entry.task.done()):
            raise ResultAccessError(409)
        await self._release_entry(entry)
        return 204

    def _start_disposal(self, entry: _Entry) -> asyncio.Task[None]:
        entry.retiring = True
        if entry.disposal is None or (entry.disposal.done() and not entry.disposed):
            entry.disposal = asyncio.create_task(self._dispose(entry))
        return entry.disposal

    async def _release_entry(self, entry: _Entry) -> None:
        await asyncio.shield(self._start_disposal(entry))

    async def _dispose(self, entry: _Entry) -> None:
        # Cancelling expiry while it awaited shielded disposal cannot cancel us.
        if entry.ttl is not None and entry.ttl is not asyncio.current_task():
            entry.ttl.cancel()
            await asyncio.gather(entry.ttl, return_exceptions=True)
        await entry.recorder.dispose()
        entry.disposed = True
        entry.sourceCode = ""
        self._finish_disposed(entry)

    def _finish_disposed(self, entry: _Entry) -> None:
        if (
            entry.disposed
            and not entry.readers
            and not entry.ownershipFinished
            and not entry.blockedCleanup
        ):
            if entry.lease is not None:
                entry.lease.finish_ownership()
            entry.ownershipFinished = True
            if self._entry is entry:
                self._entry = None

    def recover_failed_ownership(self, manager: RunManager) -> None:
        """After this manager's successful explicit retry_cleanup, never before."""
        self._bind_loop()
        entry = self._entry
        if entry is not None and entry.blockedCleanup:
            # Manager identity is checked through its exact retained session capability.
            check(entry.lease is not None and entry.lease._manager is manager)
            check(manager.availability.available)
            entry.blockedCleanup = False
            self._finish_disposed(entry)

    async def abandon(self, run_id: str) -> None:
        """Dispose storage after a start rejected before obtaining a SlotLease."""
        entry = self._entry
        if entry is not None and entry.recorder.runId == run_id:
            check(entry.lease is None)
            await self._release_entry(entry)

    async def shutdown(self) -> None:
        """Caller first awaits RunManager.shutdown(); RECORDER never stops Python."""
        entry = self._entry
        if entry is not None:
            if entry.task is not None:
                await asyncio.shield(entry.task)
            await self._release_entry(entry)
