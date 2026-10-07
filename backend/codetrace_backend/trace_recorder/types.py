"""RECORDER ownership DTOs. No parsed trace, transport or executable state."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from codetrace_contracts import validate_contract
from codetrace_contracts.config import PYTHON_VERSION, SOURCE_FILE_NAME
from codetrace_contracts.types import Completion, ResourceLimits

from ..runs import SlotLease
from ..runs.protocol import text_bytes


class RecorderError(RuntimeError):
    def __init__(self, reason: str = "TRACE_RECORDER_FAILURE") -> None:
        super().__init__(reason)
        self.reason = reason


class ResultAccessError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        self.code = "RESULT_NOT_AVAILABLE"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class RunMetadata:
    runId: str
    sourceCode: str
    acceptedAt: str
    limits: ResourceLimits
    sourceFileName: str = SOURCE_FILE_NAME
    pythonVersion: str = PYTHON_VERSION

    def __post_init__(self) -> None:
        effective = self.limits.copy()
        validate_contract(effective, "trace", "ResourceLimits")
        validate_contract(self.runId, "trace", "RunId")
        validate_contract(self.acceptedAt, "trace", "Timestamp")
        if (
            type(self.sourceCode) is not str
            or text_bytes(self.sourceCode) > effective["code_bytes"]
            or self.sourceFileName != SOURCE_FILE_NAME
            or self.pythonVersion != PYTHON_VERSION
            or effective["terminal_reserve_bytes"] >= effective["trace_bytes"]
        ):
            raise RecorderError()
        object.__setattr__(self, "limits", MappingProxyType(effective))

    def header(self) -> dict[str, object]:
        return {
            "schemaVersion": "1.0",
            "runId": self.runId,
            "sourceCode": self.sourceCode,
            "sourceFileName": self.sourceFileName,
            "pythonVersion": self.pythonVersion,
            "checkpointInterval": self.limits["checkpoint_interval"],
            "limits": dict(self.limits),
        }

    def effective_limits(self) -> ResourceLimits:
        return cast(ResourceLimits, dict(self.limits))


@dataclass(frozen=True, slots=True)
class ResultSummary:
    completion: Completion
    traceAvailable: bool
    traceUrl: str | None
    traceBytes: int | None
    eventCount: int | None

    def as_dict(self) -> dict[str, object]:
        # Caller receives detached metadata, never mutable service-owned Completion.
        import copy

        return {
            "completion": copy.deepcopy(self.completion),
            "traceAvailable": self.traceAvailable,
            "traceUrl": self.traceUrl,
            "traceBytes": self.traceBytes,
            "eventCount": self.eventCount,
        }


@dataclass(frozen=True, slots=True)
class RetainedTraceResult:
    runId: str
    path: Path
    traceBytes: int
    eventCount: int
    sealedAt: str
    expiresAt: float
    completion: Completion
    lease: SlotLease
