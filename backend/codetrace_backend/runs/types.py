"""Internal orchestration contracts. No wire Completion or retained trace document."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from codetrace_contracts.types import (
    ConsoleEntry,
    Diagnostic,
    Outcome,
    ReasonCode,
    RunStatus,
    TransportTraceRecord,
)


class RunRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InvariantError(RuntimeError):
    pass


class RecordSink(Protocol):
    """Async, cancellable, atomic commits; DTOs are transferred read-only.

    Failure/cancellation must not expose a partial commit. RECORDER owns storage.
    Implementations must yield during slow I/O and must not block the event loop.
    """

    async def commit_chunk(self, records: list[TransportTraceRecord]) -> None: ...
    async def commit_console(self, entry: ConsoleEntry) -> None: ...


LifecycleKind = Literal[
    "accepted", "status", "input_requested", "input_accepted", "console", "result"
]


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    kind: LifecycleKind
    runId: str
    payload: object


# Nonblocking observer, e.g. put_nowait into a bounded transport-owned channel.
Subscriber = Callable[[LifecycleEvent], None]


@dataclass(frozen=True, slots=True)
class InputSubmission:
    accepted: bool
    code: Literal["INVALID_INPUT", "INPUT_ALREADY_ACCEPTED"] | None = None


@dataclass(frozen=True, slots=True)
class TerminalResult:
    runId: str
    terminalStatus: RunStatus
    outcome: Outcome
    reason: ReasonCode
    primaryCause: ReasonCode | None
    runnerDiagnostic: Diagnostic | None
    finalStateVersion: int | None
    exitCode: int | None
    oomKilled: bool
    cleanupConfirmed: bool
    activeElapsedMs: float
    waitingElapsedMs: float
    acceptedAt: str
    finishedAt: str
    traceUsable: bool
    eventCount: int
    consoleCount: int
