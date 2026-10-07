"""ENCODER: forward full-record deltas and event versions over detached TRACING DTOs."""

from __future__ import annotations

import json
from typing import cast

from codetrace_contracts import ContractValidationError, default_limits, validate_contract
from codetrace_contracts.config import SOURCE_FILE_NAME
from codetrace_contracts.types import (
    Diagnostic,
    Frame,
    NormalizedState,
    ObjectRecord,
    ResourceLimits,
    ReturnKnown,
    ReturnUnknown,
    StateDelta,
    StateRecord,
)

from .canonical import canonical_json, canonical_size
from .encoder_errors import EncoderLimitError, TraceEncodingError
from .encoder_types import (
    EncodedEventCore,
    EncodedEventData,
    EncodedReferenceTrace,
    EncoderAccounting,
)
from .errors import positive_limit
from .graph_types import GraphSnapshot
from .trace_types import ReferenceTrace, ReferenceTracePoint


def _empty_snapshot() -> GraphSnapshot:
    return {"globals": [], "frames": [], "stack": [], "objects": [], "eventRoots": []}


def _order(identifier: str) -> tuple[int, str]:
    return len(identifier), identifier


def _changes(old: NormalizedState, new: NormalizedState) -> StateDelta:
    old_frames = {frame["id"]: frame for frame in old["frames"]}
    new_frames = {frame["id"]: frame for frame in new["frames"]}
    old_objects = {obj["id"]: obj for obj in old["objects"]}
    new_objects = {obj["id"]: obj for obj in new["objects"]}
    # All comparisons are over already checked exact JSON DTOs, never live objects.
    frames: list[Frame] = [
        new_frames[fid]
        for fid in sorted(new_frames, key=_order)
        if old_frames.get(fid) != new_frames[fid]
    ]
    objects: list[ObjectRecord] = [
        new_objects[oid]
        for oid in sorted(new_objects, key=_order)
        if old_objects.get(oid) != new_objects[oid]
    ]
    return {
        "globals": None if old["globals"] == new["globals"] else new["globals"],
        "stack": None if old["stack"] == new["stack"] else new["stack"],
        "eventRoots": None if old["eventRoots"] == new["eventRoots"] else new["eventRoots"],
        "framesUpsert": frames,
        "framesRemove": sorted(old_frames.keys() - new_frames.keys(), key=_order),
        "objectsUpsert": objects,
        "objectsRemove": sorted(old_objects.keys() - new_objects.keys(), key=_order),
    }


def _event(point: ReferenceTracePoint, index: int, state: NormalizedState) -> EncodedEventCore:
    # Guard exact JSON types BEFORE copying, equality, hash lookups or validators.
    raw: EncodedEventData = {
        "index": index,
        "kind": point.kind,
        "stateVersion": state["version"],
        "frameId": point.frameId,
        "source": point.source,
        "payload": point.payload,
    }
    dto = cast(EncodedEventData, json.loads(canonical_json(raw)))
    kind, source, payload = dto["kind"], dto["source"], dto["payload"]
    if kind not in ("CALL", "LINE", "RETURN", "EXCEPTION"):
        raise TraceEncodingError("Reference event kind is not a Python trace event")
    if not state["stack"] or dto["frameId"] != state["stack"][-1]:
        raise TraceEncodingError("Reference event frame must be the current active frame")
    if source is not None:
        if type(source) is not dict or set(source) != {"fileName", "lineNumber"}:
            raise TraceEncodingError("Invalid reference event source")
        line = source["lineNumber"]
        if source["fileName"] != SOURCE_FILE_NAME or (
            line is not None and (type(line) is not int or line < 1)
        ):
            raise TraceEncodingError("Invalid reference event source")
    if kind == "LINE" and (source is None or source["lineNumber"] is None):
        raise TraceEncodingError("LINE requires a known source position")
    if type(cast(object, payload)) is not dict:
        raise TraceEncodingError("Reference payload must be a JSON object")
    if kind in ("LINE", "CALL"):
        if payload:
            raise TraceEncodingError("LINE/CALL payload must be empty")
    elif kind == "RETURN":
        if set(payload) != {"returnValueKnown", "returnValue"}:
            raise TraceEncodingError("Invalid RETURN payload fields")
        returned = cast(ReturnKnown | ReturnUnknown, payload)
        if type(returned["returnValueKnown"]) is not bool:
            raise TraceEncodingError("Invalid RETURN known flag")
        if returned["returnValueKnown"]:
            value = returned["returnValue"]
            validate_contract(value, "trace", "Value")
            if cast(dict[str, object], value).get("type") == "none":
                raise TraceEncodingError("Known RETURN cannot encode None")
            if value not in state["eventRoots"]:
                raise TraceEncodingError("Known RETURN must resolve through its event root")
        elif returned["returnValue"] is not None:
            raise TraceEncodingError("Unknown RETURN must have a null value")
    elif kind == "EXCEPTION":
        if set(payload) != {"diagnostic"}:
            raise TraceEncodingError("Invalid EXCEPTION payload fields")
        diagnostic = cast(Diagnostic, cast(dict[str, object], payload)["diagnostic"])
        validate_contract(diagnostic, "trace", "Diagnostic")
        if diagnostic["category"] != "runtime" or diagnostic["code"] != "EXCEPTION_OBSERVED":
            raise TraceEncodingError("Invalid observed exception diagnostic")
    return EncodedEventCore(**dto)


class _EncoderBudget:
    """Only canonical ENCODER record bytes; not final TraceDocument or a recorder."""

    def __init__(self, ordinary_bytes: int) -> None:
        self.ordinary_bytes = ordinary_bytes
        self.state_bytes = 0
        self.event_bytes = 0
        self.extra_bytes = 0

    def accept(
        self, record: StateRecord | None, event: EncodedEventCore | None, extra_bytes: int = 0
    ) -> None:
        state_size = 0 if record is None else canonical_size(record)
        event_size = 0 if event is None else canonical_size(event.as_dict())
        if (
            self.state_bytes
            + self.event_bytes
            + self.extra_bytes
            + state_size
            + event_size
            + extra_bytes
            > self.ordinary_bytes
        ):
            raise EncoderLimitError("TRACE_BYTES_LIMIT")
        self.state_bytes += state_size
        self.event_bytes += event_size
        self.extra_bytes += extra_bytes

    def result(self) -> EncoderAccounting:
        return EncoderAccounting(self.state_bytes, self.event_bytes, self.ordinary_bytes)


class TraceEncoder:
    """Reusable fixed configuration; each encode has entirely local working state.

    Output JSON DTOs are read-only by ownership, matching SERIALIZER/TRACING. Input fragments
    are copied via guarded JSON, so output does not share mutable input containers.
    No Registry, frame, Python execution, Console or final-document dependencies.
    """

    def __init__(
        self, limits: ResourceLimits | None = None, *, checkpoint_interval: int | None = None
    ) -> None:
        effective = default_limits() if limits is None else limits.copy()
        interval = (
            effective["checkpoint_interval"] if checkpoint_interval is None else checkpoint_interval
        )
        self._interval = positive_limit(interval, "checkpoint_interval")
        self._max_user_events = positive_limit(effective["max_events"], "max_events") - 1
        self._state_bytes = positive_limit(effective["state_bytes"], "state_bytes")
        trace_bytes = positive_limit(effective["trace_bytes"], "trace_bytes")
        reserve = positive_limit(effective["terminal_reserve_bytes"], "terminal_reserve_bytes")
        if reserve >= trace_bytes:
            raise ValueError("terminal_reserve_bytes must leave a positive ordinary byte budget")
        self._ordinary_bytes = trace_bytes - reserve
        self._reserve_bytes = reserve

    @property
    def checkpointInterval(self) -> int:
        return self._interval

    def _check_state(self, state: NormalizedState) -> None:
        if canonical_size(state) > self._state_bytes:
            raise EncoderLimitError("STATE_BYTES_LIMIT")
        validate_contract(state, "trace", "NormalizedState")

    def encode(self, reference_trace: ReferenceTrace) -> EncodedReferenceTrace:
        if type(reference_trace) is not ReferenceTrace or type(reference_trace.points) is not tuple:
            raise TraceEncodingError("Encoder requires an exact ReferenceTrace with tuple points")
        if len(reference_trace.points) > self._max_user_events:
            raise EncoderLimitError("EVENT_LIMIT")
        try:
            return self._encode(reference_trace)
        except EncoderLimitError:
            raise
        except (TypeError, ValueError, KeyError, RecursionError, ContractValidationError):
            # Never render rejected data or return a partially successful result.
            raise TraceEncodingError("Invalid detached reference DTO for encoding") from None

    def session(self) -> TraceEncoderSession:
        return TraceEncoderSession(self)

    def _encode(self, reference_trace: ReferenceTrace) -> EncodedReferenceTrace:
        session = self.session()
        records = [session.start()]
        events: list[EncodedEventCore] = []
        for point in reference_trace.points:
            record, event = session.consume(point)
            if record is not None:
                records.append(record)
            events.append(event)
        return EncodedReferenceTrace(
            tuple(events), tuple(records), self._interval, session.accounting
        )


class TraceEncoderSession:
    """One incremental run; only its current state and counters are retained.

    Returned DTOs are read-only by ownership, like the batch API. Finalization is
    state-only and charges the terminal reserve, never a synthetic Python event.
    """

    def __init__(self, encoder: TraceEncoder) -> None:
        self._encoder = encoder
        self._previous: NormalizedState = {"version": 0, **_empty_snapshot()}
        self._signature = canonical_json(_empty_snapshot())
        self._budget = _EncoderBudget(encoder._ordinary_bytes)
        self._index = 0
        self._started = False
        self._finished = False
        self.terminalStateBytes = 0

    @property
    def accounting(self) -> EncoderAccounting:
        return self._budget.result()

    @property
    def ordinaryBytes(self) -> int:
        return self.accounting.recordBytes + self._budget.extra_bytes

    @property
    def version(self) -> int:
        return self._previous["version"]

    def _ready(self) -> None:
        if not self._started or self._finished:
            raise TraceEncodingError("Encoder session is not accepting records")

    def start(self) -> StateRecord:
        if self._started:
            raise TraceEncodingError("Encoder session already started")
        self._encoder._check_state(self._previous)
        record: StateRecord = {"kind": "checkpoint", "version": 0, "state": self._previous}
        self._budget.accept(record, None)
        self._started = True
        return record

    def accept_ordinary_bytes(self, size: int) -> None:
        self._ready()
        if type(size) is not int or size < 0:
            raise TraceEncodingError("Invalid ordinary byte count")
        self._budget.accept(None, None, size)

    def _transition(
        self, snapshot: GraphSnapshot
    ) -> tuple[StateRecord | None, NormalizedState, str]:
        signature = canonical_json(snapshot)
        if signature == self._signature:
            return None, self._previous, signature
        detached = cast(GraphSnapshot, json.loads(signature))
        if set(detached) != set(_empty_snapshot()):
            raise TraceEncodingError("GraphSnapshot must contain exactly the SERIALIZER fields")
        current: NormalizedState = {"version": self.version + 1, **detached}
        self._encoder._check_state(current)
        version = current["version"]
        record: StateRecord
        if version % self._encoder._interval == 0:
            record = {"kind": "checkpoint", "version": version, "state": current}
        else:
            record = {
                "kind": "delta",
                "version": version,
                "baseVersion": version - 1,
                "changes": _changes(self._previous, current),
            }
        validate_contract(record, "trace", "StateRecord")
        return record, current, signature

    def consume(
        self, point: ReferenceTracePoint, *, console_offset: int | None = None
    ) -> tuple[StateRecord | None, EncodedEventCore]:
        self._ready()
        if type(point) is not ReferenceTracePoint:
            raise TraceEncodingError("Encoder points must be exact ReferenceTracePoint records")
        if self._index >= self._encoder._max_user_events:
            raise EncoderLimitError("EVENT_LIMIT")
        record, current, signature = self._transition(point.snapshot)
        event = _event(point, self._index, current)
        extra = 0
        if console_offset is not None:
            if type(console_offset) is not int or console_offset < 0:
                raise TraceEncodingError("Invalid console offset")
            # Event field and conservative array separators; no transport bytes.
            extra = len(',"consoleOffset":') + len(str(console_offset)) + 1
            if record is not None:
                extra += 1
        self._budget.accept(record, event, extra)
        self._previous, self._signature = current, signature
        self._index += 1
        return record, event

    def accept_final_snapshot(self, snapshot: GraphSnapshot) -> tuple[StateRecord | None, int]:
        self._ready()
        record, current, signature = self._transition(snapshot)
        size = 0 if record is None else canonical_size(record) + 1
        if size > self._encoder._reserve_bytes:
            raise EncoderLimitError("TRACE_BYTES_LIMIT")
        self.terminalStateBytes = size
        self._previous, self._signature = current, signature
        self._finished = True
        return record, self.version
