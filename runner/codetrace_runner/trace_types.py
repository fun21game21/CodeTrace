"""TRACING reference records: observed boundaries, without ENCODER versions or wire events."""

from dataclasses import dataclass
from typing import Literal

from codetrace_contracts.types import (
    EmptyPayload,
    ExceptionPayload,
    ReturnKnown,
    ReturnUnknown,
    SourceLocation,
)

from .graph_types import GraphSnapshot


@dataclass(frozen=True, slots=True)
class ReferenceTracePoint:
    kind: Literal["CALL", "LINE", "RETURN", "EXCEPTION"]
    frameId: str
    source: SourceLocation
    payload: EmptyPayload | ReturnKnown | ReturnUnknown | ExceptionPayload
    snapshot: GraphSnapshot


@dataclass(frozen=True, slots=True)
class ReferenceTrace:
    # Nested SERIALIZER JSON DTOs are detached, read-only by ownership, not deep-frozen.
    points: tuple[ReferenceTracePoint, ...]


@dataclass(frozen=True, slots=True)
class ReferenceExecution:
    trace: ReferenceTrace
    # Fact observed by the test harness outside exec; not Completion or run status.
    exception_escaped: bool
