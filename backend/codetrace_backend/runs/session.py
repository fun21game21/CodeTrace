"""Single-event-loop session: state changes never suspend; I/O runs in owned tasks."""

import asyncio
from collections.abc import Callable
from typing import cast

from codetrace_contracts import validate_contract
from codetrace_contracts.types import (
    Diagnostic,
    ReasonCode,
    ResourceLimits,
    RunnerMessage,
    RunStatus,
)

from ..docker_adapter import AttachedProcess, ContainerHandle, ContainerInspection, DockerAdapter
from ..docker_adapter.errors import DockerCommandTimeout, DockerOperationError
from .arbiter import TerminalArbiter
from .protocol import Prefix, ProtocolViolation, Reader, encode, require, text_bytes
from .timing import TERMINAL, Clock, Timing
from .types import (
    InputSubmission,
    LifecycleEvent,
    LifecycleKind,
    RecordSink,
    Subscriber,
    TerminalResult,
)


class RunSession:
    """Loop-confined. Atomic transitions/arbiter decisions contain no await.

    Sink commits can suspend; they own records until completion and are cancellable.
    Watchdog and native stderr drain never acquire the sink or stdin writer lock.
    """

    def __init__(
        self,
        run_id: str,
        code: str,
        limits: ResourceLimits,
        adapter: DockerAdapter,
        sink: RecordSink,
        subscriber: Subscriber,
        clock: Clock,
        on_result: Callable[[TerminalResult], None],
    ) -> None:
        self.runId, self.limits, self.adapter = run_id, limits, adapter
        self._code, self.sink, self._subscriber = code, sink, subscriber
        self.clock, self.timing = clock, Timing(clock, limits)
        self.acceptedAt = clock.utc()
        self.arbiter = TerminalArbiter()
        self.prefix = Prefix()
        self.container: ContainerHandle | None = None
        self.attachment: AttachedProcess | None = None
        self.inspection: ContainerInspection | None = None
        self.pendingInput: str | None = None
        self._request_count = 0
        self._accepted_inputs: set[str] = set()
        self._echo_pending: set[str] = set()
        self._control_seen = False
        self._ready_seen = False
        self._create_attempted = False
        self._ready = asyncio.Event()
        self._wake = asyncio.Event()
        self._writer_lock = asyncio.Lock()
        self._out_seq = 0
        self._exit_code: int | None = None
        self._native_bytes = 0
        self._sink_failed = False
        self._on_result = on_result
        self._result: asyncio.Future[TerminalResult] = asyncio.get_running_loop().create_future()
        self._startup: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._exit: asyncio.Task[None] | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._finalization: asyncio.Task[None] | None = None
        self._kill: asyncio.Task[None] | None = None

    @property
    def status(self) -> RunStatus:
        return self.timing.status

    def launch(self) -> None:
        self._startup = asyncio.create_task(self._start())
        self._watchdog = asyncio.create_task(self._watch())
        self._emit("accepted", {"status": "STARTING", "acceptedAt": self.acceptedAt})

    def _emit(self, kind: LifecycleKind, payload: object) -> None:
        try:
            self._subscriber(LifecycleEvent(kind, self.runId, payload))
        except Exception:
            # A failed observer cannot strand a container or prevent publication.
            self._subscriber = lambda event: None
            self.terminate("CONNECTION_LOST")

    def _transition(self, status: RunStatus) -> None:
        self.timing.transition(status)
        self._wake.set()
        self._emit("status", status)

    def terminate(self, reason: ReasonCode, *, late: bool = False) -> bool:
        if self.status in TERMINAL or (self.status == "FINALIZING" and not late):
            return False
        accepted = self.arbiter.accept(reason)
        self._begin_finalization()
        if self.container is not None and accepted:
            self._ensure_kill()
        return accepted

    def _begin_finalization(self) -> None:
        if self._finalization is not None:
            return
        self.pendingInput = None
        # Reserve the task before notifying observers (which may reenter).
        self._finalization = asyncio.create_task(self._finalize())
        self._transition("FINALIZING")

    async def await_result(self) -> TerminalResult:
        return await asyncio.shield(self._result)

    async def _start(self) -> None:
        try:
            await self.adapter.check_available()
            self._create_attempted = True
            self.container = await self.adapter.create(self.runId)
            self.attachment = await self.adapter.start_attach(self.container)
            self.timing.started = self.clock.now()
            self._wake.set()
            self._readers = [
                asyncio.create_task(self._stdout()),
                asyncio.create_task(self._stderr()),
            ]
            self._exit = asyncio.create_task(self._observe_exit())
            await self._ready.wait()
            if self.status != "STARTING":
                return
            self._transition("RUNNING")
            await self._send(
                "start",
                {
                    "code": self._code,
                    "sourceFileName": "<codetrace-user>",
                    "limits": self.limits,
                    "checkpointInterval": self.limits["checkpoint_interval"],
                },
            )
        except DockerOperationError as error:
            self.terminate(cast(ReasonCode, error.reason))
        except Exception:
            self.terminate("RUNNER_FAILURE")
        finally:
            self._code = ""

    async def _send(self, kind: str, payload: object) -> None:
        async with self._writer_lock:
            assert self.attachment is not None
            self._out_seq += 1
            message = {
                "protocolVersion": 1,
                "runId": self.runId,
                "seq": self._out_seq,
                "type": kind,
                "payload": payload,
            }
            validate_contract(message, "runner")
            raw = encode(message)
            require(len(raw) <= self.limits["runner_message_bytes"])
            self.attachment.stdin.write(raw + b"\n")
            await self.attachment.stdin.drain()

    async def submit_input(self, run_id: str, request_id: str, text: str) -> InputSubmission:
        if type(run_id) is not str or type(request_id) is not str or run_id != self.runId:
            return InputSubmission(False, "INVALID_INPUT")
        if request_id in self._accepted_inputs:
            return InputSubmission(False, "INPUT_ALREADY_ACCEPTED")
        if (
            self.status != "WAITING_INPUT"
            or request_id != self.pendingInput
            or type(text) is not str
            or "\r" in text
            or "\n" in text
            or text_bytes(text) > self.limits["input_value_bytes"]
        ):
            return InputSubmission(False, "INVALID_INPUT")
        # Enforce an already reached deadline even if the watchdog hasn't run yet.
        if self.check_deadline():
            return InputSubmission(False, "INVALID_INPUT")
        self._accepted_inputs.add(request_id)
        self._echo_pending.add(request_id)
        self.pendingInput = None
        self._transition("RUNNING")
        try:
            async with asyncio.timeout(self.limits["cleanup_command_timeout_ms"] / 1000):
                await self._send("input_response", {"requestId": request_id, "text": text})
        except (Exception, asyncio.CancelledError):
            self.terminate("RUNNER_FAILURE")
            # Acceptance is final even if the caller cancels while writing.
            raise
        if self.status not in TERMINAL:
            self._emit("input_accepted", request_id)
        return InputSubmission(True)

    def check_deadline(self) -> bool:
        deadline = self.timing.deadline()
        if deadline is not None and self.clock.now() >= deadline[0]:
            return self.terminate(deadline[1])
        return False

    async def _watch(self) -> None:
        while self.status not in TERMINAL and self.status != "FINALIZING":
            self._wake.clear()
            if self.check_deadline():
                return
            deadline = self.timing.deadline()
            if deadline is None:
                return
            await self.clock.wait(self._wake, deadline[0] - self.clock.now())

    async def _stdout(self) -> None:
        assert self.attachment is not None
        reader = Reader(self.attachment.stdout, self.runId, self.limits["runner_message_bytes"])
        try:
            while (message := await reader.next()) is not None:
                await self._message(message)
                await asyncio.sleep(0)  # A full pipe cannot starve the watchdog.
        except (ProtocolViolation, ValueError, RecursionError):
            self.terminate("RUNNER_PROTOCOL_ERROR", late=True)
            # Discard subsequent bytes; no parse or commits after corrupt prefix.
            while await self.attachment.stdout.read(4096):
                pass
        except Exception:
            self.terminate("RUNNER_FAILURE", late=True)
        finally:
            if self.status not in TERMINAL:
                self._begin_finalization()

    async def _message(self, message: RunnerMessage) -> None:
        kind = message["type"]
        require(not self._control_seen)
        if message["type"] == "ready":
            require(not self._ready_seen and self.status == "STARTING")
            self._ready_seen = True
            self._ready.set()
            return
        if message["type"] == "fatal":
            self._control_seen = True
            self._diagnostic = message["payload"]["diagnostic"]
            self.terminate("RUNNER_FAILURE", late=True)
            return
        require(self._ready_seen)
        if kind in {"trace_chunk", "output"}:
            require(self.status in {"RUNNING", "FINALIZING"})
        if message["type"] == "trace_chunk":
            records = message["payload"]["records"]
            candidate = self.prefix.chunk(records, self.limits)
            try:
                await self.sink.commit_chunk(records)
            except Exception:
                self._sink_failed = True
                self._control_seen = True
                self.terminate("TRACE_RECORDER_FAILURE", late=True)
                return
            self.prefix = candidate
        elif message["type"] == "output":
            entry = message["payload"]["entry"]
            candidate = self.prefix.output(entry, self.limits)
            if entry["kind"] == "input":
                require(entry["requestId"] in self._echo_pending)
            try:
                await self.sink.commit_console(entry)
            except Exception:
                self._sink_failed = True
                self._control_seen = True
                self.terminate("TRACE_RECORDER_FAILURE", late=True)
                return
            self.prefix = candidate
            if entry["kind"] == "input":
                self._echo_pending.remove(cast(str, entry["requestId"]))
            self._emit("console", entry)
        elif message["type"] == "input_request":
            if self.status == "FINALIZING":
                return
            payload = message["payload"]
            require(self.status == "RUNNING" and self.pendingInput is None)
            require(payload["requestId"] == f"q{self._request_count + 1}")
            require(self._request_count < self.limits["input_requests"])
            require(payload["consoleOffset"] <= self.prefix.console)
            if self.check_deadline():
                return
            self._request_count += 1
            self.pendingInput = payload["requestId"]
            self._transition("WAITING_INPUT")
            self._emit("input_requested", dict(payload))
        elif message["type"] == "finished":
            require(self.status in {"RUNNING", "FINALIZING"})
            require(message["payload"]["finalStateVersion"] == self.prefix.version >= 0)
            require(self.prefix.pendingStateBytes <= self.limits["terminal_reserve_bytes"])
            self._control_seen = True
            if self.arbiter.primaryCause is None:
                self.arbiter.proposal = message["payload"]
            self._begin_finalization()
        elif message["type"] == "abort_request":
            require(self.status in {"RUNNING", "FINALIZING"})
            self._control_seen = True
            self._diagnostic = message["payload"]["diagnostic"]
            self.terminate(message["payload"]["reason"], late=True)
        else:
            raise ProtocolViolation("Unexpected Runner direction")

    async def _stderr(self) -> None:
        assert self.attachment is not None
        while chunk := await self.attachment.stderr.read(4096):
            self._native_bytes = min(
                self.limits["native_stderr_bytes"] + 1, self._native_bytes + len(chunk)
            )
            if self._native_bytes > self.limits["native_stderr_bytes"]:
                self.terminate("RUNNER_FAILURE", late=True)
            await asyncio.sleep(0)

    async def _observe_exit(self) -> None:
        assert self.container is not None
        try:
            while True:
                try:
                    self._exit_code = await self.adapter.wait(self.container)
                    self._begin_finalization()
                    return
                except DockerCommandTimeout:
                    # Bounded Docker wait timeout is not an execution timeout.
                    await asyncio.sleep(0)
        except DockerOperationError as error:
            self.terminate(cast(ReasonCode, error.reason), late=True)

    def _ensure_kill(self) -> None:
        if self._kill is None and self.container is not None:
            self._kill = asyncio.create_task(self._kill_once())

    async def _kill_once(self) -> None:
        assert self.container is not None
        try:
            await self.adapter.kill(self.container)
        except DockerOperationError:
            pass  # Cleanup still inspects/retries; a CLI error isn't proof of death.

    async def _stop_tasks(self, tasks: list[asyncio.Task[None]]) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _finish_io(self) -> None:
        tasks = self._readers + [
            t for t in (self._startup, self._exit, self._watchdog, self._kill) if t is not None
        ]
        await self._stop_tasks(tasks)
        if self.attachment is not None:
            self.attachment.stdin.close()
            await self.attachment.close()

    async def _finalize(self) -> None:
        cleanup = False
        budget = self.limits["finalization_timeout_ms"] / 1000
        deadline = asyncio.get_running_loop().time() + budget
        # Reserve local task/CLI disposal time inside the same total deadline.
        disposal = min(self.limits["cleanup_command_timeout_ms"] / 1000, budget / 10)
        try:
            async with asyncio.timeout_at(deadline - disposal):
                if self._startup is not None:
                    await self._stop_tasks([self._startup])
                if self.container is None and self._create_attempted:
                    # Includes cancellation/unknown create. Successful enumeration
                    # proves absence; daemon errors never do. Touch only this RunId.
                    candidates = await self.adapter.list_managed_containers()
                    owned = [h for h in candidates if h.runId == self.runId]
                    require(len(owned) <= 1)
                    if owned:
                        self.container = owned[0]
                if self.arbiter.primaryCause is not None:
                    self._ensure_kill()
                if self._kill is not None:
                    await self._kill
                try:
                    try:
                        async with asyncio.timeout(
                            self.limits["cleanup_command_timeout_ms"] / 1000
                        ):
                            await asyncio.gather(*self._readers)
                    except TimeoutError:
                        self.arbiter.accept("RUNNER_FAILURE")
                        self._ensure_kill()
                        if self._kill is not None:
                            await self._kill
                    if self.container is not None:
                        self.inspection = await self.adapter.inspect(self.container)
                        if (
                            self.inspection is not None
                            and self.inspection.running
                            and self.arbiter.proposal is None
                        ):
                            self.arbiter.accept("RUNNER_FAILURE")
                            self._ensure_kill()
                            if self._kill is not None:
                                await self._kill
                        if self._exit is not None:
                            await self._exit
                        elif self.inspection is not None and self.inspection.status != "created":
                            self._exit_code = await self.adapter.wait(self.container)
                        self.inspection = await self.adapter.inspect(self.container)
                        if self.inspection is not None:
                            self._exit_code = self.inspection.exitCode
                except Exception:
                    self.arbiter.accept("RUNNER_FAILURE")
                if self.container is not None:
                    cleanup = (await self.adapter.cleanup(self.container)).cleanupConfirmed
                else:
                    cleanup = True
                await self._finish_io()
        except TimeoutError:
            self.arbiter.finalizationExpired = True
        except Exception:
            self.arbiter.accept("RUNNER_FAILURE")
        finally:
            # Local disposal uses only the remaining total finalization budget;
            # it never turns an unconfirmed container into confirmed cleanup.
            try:
                async with asyncio.timeout_at(deadline):
                    await self._finish_io()
            except TimeoutError:
                self.arbiter.finalizationExpired = True
            except (Exception, asyncio.CancelledError):
                self.arbiter.accept("RUNNER_FAILURE")
            self.pendingInput = None
            self._code = ""
            oom = self.inspection.oomKilled if self.inspection is not None else False
            decision = self.arbiter.decide(exit_code=self._exit_code, oom=oom, cleanup=cleanup)
            self._transition(decision.status)
            proposal = self.arbiter.proposal
            diagnostic: Diagnostic | None = (
                proposal["primaryDiagnostic"] if proposal else getattr(self, "_diagnostic", None)
            )
            active, waiting = self.timing.elapsed()
            result = TerminalResult(
                self.runId,
                decision.status,
                decision.outcome,
                decision.reason,
                self.arbiter.primaryCause,
                diagnostic,
                self.prefix.version if self.prefix.version >= 0 else None,
                self._exit_code,
                oom,
                cleanup,
                active,
                waiting,
                self.acceptedAt,
                self.clock.utc(),
                cleanup
                and not self.arbiter.finalizationExpired
                and not self._sink_failed
                and self.prefix.version >= 0,
                self.prefix.events,
                self.prefix.console,
            )
            self._on_result(result)
            self._result.set_result(result)
            self._emit("result", result)
