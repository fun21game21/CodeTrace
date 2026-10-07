"""ENCODER internal event cores and encoded records, before RUNNER_IO Console offsets."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from codetrace_contracts.types import (
    EmptyPayload,
    ExceptionPayload,
    ReturnKnown,
    ReturnUnknown,
    SourceLocation,
    StateRecord,
)


class EncodedEventData(TypedDict):
    index: int
    kind: Literal["CALL", "LINE", "RETURN", "EXCEPTION"]
    stateVersion: int
    frameId: str
    source: SourceLocation | None
    payload: EmptyPayload | ReturnKnown | ReturnUnknown | ExceptionPayload


@dataclass(frozen=True, slots=True)
class EncodedEventCore:
    index: int
    kind: Literal["CALL", "LINE", "RETURN", "EXCEPTION"]
    stateVersion: int
    frameId: str
    source: SourceLocation | None
    payload: EmptyPayload | ReturnKnown | ReturnUnknown | ExceptionPayload

    def as_dict(self) -> EncodedEventData:
        """Read-only DTO view; no Console placeholder or production wire envelope."""
        return {
            "index": self.index,
            "kind": self.kind,
            "stateVersion": self.stateVersion,
            "frameId": self.frameId,
            "source": self.source,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class EncoderAccounting:
    stateRecordBytes: int
    eventCoreBytes: int
    ordinaryBudgetBytes: int

    @property
    def recordBytes(self) -> int:
        return self.stateRecordBytes + self.eventCoreBytes


@dataclass(frozen=True, slots=True)
class EncodedReferenceTrace:
    events: tuple[EncodedEventCore, ...]
    states: tuple[StateRecord, ...]
    checkpointInterval: int
    accounting: EncoderAccounting
