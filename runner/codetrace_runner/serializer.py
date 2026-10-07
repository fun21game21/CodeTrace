"""Build a detached reachable graph without execution, tracing or version assignment."""

from collections import deque
from types import BuiltinFunctionType
from typing import Literal, cast

from codetrace_contracts import default_limits, validate_contract
from codetrace_contracts.types import (
    Binding,
    Frame,
    FrameId,
    ObjectId,
    ObjectRecord,
    OpaqueObject,
    Ref,
    ResourceLimits,
    SourceLocation,
    Value,
)

from .canonical import canonical_json, canonical_size
from .classification import classify_value
from .errors import RegistryClosedError, positive_limit
from .graph_errors import GraphLimitError
from .graph_types import FrameInput, GraphSnapshot
from .registry import ObjectRegistry
from .values import ScalarEncoder, ValueEncoder

_SERVICE_GLOBALS = frozenset(
    (
        "__builtins__",
        "__name__",
        "__file__",
        "__package__",
        "__loader__",
        "__spec__",
        "__cached__",
        "__doc__",
    )
)
_HEAPTYPE = 1 << 9
LiteralOpaqueReason = Literal["unsupported_type", "unsupported_class", "builtin_symbol"]


def _exact_mapping(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError("Root mappings must be exact dicts supplied by the frame adapter")
    mapping = cast(dict[str, object], value)
    if any(type(key) is not str for key in dict.__iter__(mapping)):
        raise TypeError("Root binding names must be exact strings")
    return mapping


def _exact_sequence(value: object) -> list[object] | tuple[object, ...]:
    if type(value) is not list and type(value) is not tuple:
        raise TypeError("Input sequences must be exact lists or tuples")
    return cast(list[object] | tuple[object, ...], value)


def _id_order(value: str) -> tuple[int, str]:
    # Numeric ordering for canonical oN/fN IDs without unbounded int conversion.
    return len(value), value


def _safe_type_name(cls: type) -> str:
    if type(cls) is not type:
        return "opaque"
    name = type.__getattribute__(cls, "__name__")
    return name if type(name) is str else "opaque"


def _opaque_reason(cls: type) -> LiteralOpaqueReason:
    if cls is type or cls is BuiltinFunctionType:
        return "builtin_symbol"
    if type(cls) is not type or type.__getattribute__(cls, "__flags__") & _HEAPTYPE:
        return "unsupported_class"
    return "unsupported_type"


class GraphSerializer:
    """One Run's Registry; every serialize call has its own reachability and DTOs."""

    def __init__(self, registry: ObjectRegistry, limits: ResourceLimits | None = None) -> None:
        effective = default_limits() if limits is None else limits.copy()
        self._registry = registry
        self._values = ValueEncoder(registry, effective)
        self._scalars = ScalarEncoder(effective)
        self._state_objects = positive_limit(effective["state_objects"], "state_objects")
        self._state_bytes = positive_limit(effective["state_bytes"], "state_bytes")
        self._collection_items = positive_limit(effective["collection_items"], "collection_items")
        self._instance_attributes = positive_limit(
            effective["instance_attributes"], "instance_attributes"
        )
        self._active_frames = positive_limit(effective["active_frames"], "active_frames")

    def _text(self, value: object) -> str:
        if type(value) is not str:
            raise TypeError("Metadata and binding names must be exact strings")
        self._scalars.encode(value)
        return value

    def _frame(self, value: object) -> tuple[Frame, dict[str, object]]:
        if type(value) is not FrameInput:
            raise TypeError("Frames must be exact FrameInput records")
        source = value
        frame_id = self._text(source.id)
        parent = None if source.parentId is None else self._text(source.parentId)
        kind = self._text(source.kind)
        locals_source = self._text(source.localsSource)
        parameters = [self._text(p) for p in _exact_sequence(source.parameterNames)]
        definition = source.definitionLine
        if definition is not None and (type(definition) is not int or definition < 1):
            raise ValueError("Frame definitionLine must be a positive integer or None")
        call_site: SourceLocation | None = None
        if source.callSite is not None:
            raw = _exact_mapping(source.callSite)
            if set(raw) != {"fileName", "lineNumber"}:
                raise ValueError("Invalid callSite fields")
            line = raw["lineNumber"]
            if line is not None and (type(line) is not int or line < 1):
                raise ValueError("callSite lineNumber must be a positive integer or None")
            location: SourceLocation = {
                "fileName": cast(Literal["<codetrace-user>"], self._text(raw["fileName"])),
                "lineNumber": line,
            }
            call_site = location
        result: Frame = {
            "id": frame_id,
            "parentId": parent,
            "kind": cast(Literal["module", "function", "class"], kind),
            "name": self._text(source.name),
            "qualifiedName": self._text(source.qualifiedName),
            "definitionLine": definition,
            "callSite": call_site,
            "parameterNames": parameters,
            "localsSource": cast(Literal["own", "globals"], locals_source),
            "locals": [],
        }
        validate_contract(result, "trace", "Frame")
        return result, _exact_mapping(source.live_locals)

    def serialize(
        self,
        user_globals: dict[str, object],
        frames: list[FrameInput] | tuple[FrameInput, ...] = (),
        stack: list[FrameId] | tuple[FrameId, ...] = (),
        event_roots: list[object] | tuple[object, ...] = (),
    ) -> GraphSnapshot:
        if self._registry.closed:
            raise RegistryClosedError("Object Registry is closed")
        globals_mapping = _exact_mapping(user_globals)
        raw_frames = _exact_sequence(frames)
        raw_stack = _exact_sequence(stack)
        raw_roots = _exact_sequence(event_roots)
        if len(raw_frames) > self._active_frames:
            raise GraphLimitError("FRAME_LIMIT", "Maximum active frame count exceeded.")
        prepared = [self._frame(frame) for frame in raw_frames]
        prepared.sort(key=lambda pair: _id_order(pair[0]["id"]))
        stack_ids = [self._text(frame_id) for frame_id in raw_stack]
        frame_by_id = {frame["id"]: frame for frame, _ in prepared}
        if len(frame_by_id) != len(prepared) or len(set(stack_ids)) != len(stack_ids):
            raise ValueError("Frame and stack IDs must be unique")
        if set(frame_by_id) != set(stack_ids):
            raise ValueError("Stack must contain exactly the active frame IDs")
        for index, frame_id in enumerate(stack_ids):
            parent = None if index == 0 else stack_ids[index - 1]
            if frame_by_id[frame_id]["parentId"] != parent:
                raise ValueError("Frame parent must match the active user stack")

        snapshot: GraphSnapshot = {
            "globals": [],
            "frames": [],
            "stack": stack_ids,
            "objects": [],
            "eventRoots": [],
        }
        work = _Traversal(self)
        snapshot["globals"] = work.bindings(globals_mapping, filter_globals=True)
        for frame, live_locals in prepared:
            work.spend(canonical_size(frame))
            if frame["localsSource"] == "own":
                frame["locals"] = work.bindings(live_locals)
            snapshot["frames"].append(frame)
        snapshot["eventRoots"] = [work.value(value) for value in raw_roots]
        snapshot["objects"] = work.finish()
        if canonical_size(snapshot) > self._state_bytes:
            raise GraphLimitError("STATE_BYTES_LIMIT", "Maximum canonical state size exceeded.")
        return snapshot


class _Traversal:
    """Visited markers are inserted before queueing children; no recursive graph walk."""

    def __init__(self, serializer: GraphSerializer) -> None:
        self._serializer = serializer
        self._seen: set[ObjectId] = set()
        self._pending: deque[ObjectId] = deque()
        self._records: dict[ObjectId, ObjectRecord] = {}
        self._lower_bound_bytes = 0

    def spend(self, size: int) -> None:
        # Count disjoint DTO fragments early; exact full DTO size is checked at publication.
        self._lower_bound_bytes += size
        if self._lower_bound_bytes > self._serializer._state_bytes:
            raise GraphLimitError("STATE_BYTES_LIMIT", "Maximum canonical state size exceeded.")

    def value(self, value: object) -> Value:
        encoded = self._serializer._values.encode(value)
        if "$ref" in encoded:
            object_id = cast(Ref, encoded)["$ref"]
            if object_id not in self._seen:
                if len(self._seen) >= self._serializer._state_objects:
                    raise GraphLimitError(
                        "COLLECTION_LIMIT", "Maximum reachable state object count exceeded."
                    )
                self._seen.add(object_id)
                self._pending.append(object_id)
        self.spend(canonical_size(encoded))
        return encoded

    def bindings(
        self, mapping: dict[str, object], *, filter_globals: bool = False
    ) -> list[Binding]:
        result: list[Binding] = []
        for name in sorted(mapping):
            if filter_globals and name in _SERVICE_GLOBALS:
                continue
            safe_name = self._serializer._text(name)
            self.spend(canonical_size(safe_name))
            result.append({"name": safe_name, "value": self.value(dict.__getitem__(mapping, name))})
        return result

    def _opaque(self, object_id: ObjectId, obj: object) -> OpaqueObject:
        cls = type(obj)
        return {
            "id": object_id,
            "kind": "opaque",
            "pythonType": self._serializer._text(_safe_type_name(cls)),
            "reason": _opaque_reason(cls),
        }

    def _record(self, object_id: ObjectId, obj: object) -> ObjectRecord:
        category = classify_value(obj)
        cap = self._serializer._collection_items
        if category == "list":
            sequence = cast(list[object], obj)
            if list.__len__(sequence) > cap:
                raise GraphLimitError("COLLECTION_LIMIT", "Maximum collection item count exceeded.")
            return {
                "id": object_id,
                "kind": "list",
                "items": [self.value(v) for v in list.__iter__(sequence)],
            }
        if category == "tuple":
            tup = cast(tuple[object, ...], obj)
            if tuple.__len__(tup) > cap:
                raise GraphLimitError("COLLECTION_LIMIT", "Maximum collection item count exceeded.")
            return {
                "id": object_id,
                "kind": "tuple",
                "items": [self.value(v) for v in tuple.__iter__(tup)],
            }
        if category == "dict":
            mapping = cast(dict[object, object], obj)
            if dict.__len__(mapping) > cap:
                raise GraphLimitError("COLLECTION_LIMIT", "Maximum collection item count exceeded.")
            return {
                "id": object_id,
                "kind": "dict",
                "entries": [
                    {"key": self.value(k), "value": self.value(v)} for k, v in dict.items(mapping)
                ],
            }
        if category == "set":
            members = cast(set[object], obj)
            if set.__len__(members) > cap:
                raise GraphLimitError("COLLECTION_LIMIT", "Maximum collection item count exceeded.")
            items = [self.value(v) for v in set.__iter__(members)]
            items.sort(key=canonical_json)
            return {"id": object_id, "kind": "set", "items": items}
        if category == "deque":
            queue = cast(deque[object], obj)
            if deque.__len__(queue) > cap:
                raise GraphLimitError("COLLECTION_LIMIT", "Maximum collection item count exceeded.")
            return {
                "id": object_id,
                "kind": "deque",
                "items": [self.value(v) for v in deque.__iter__(queue)],
                "maxlen": queue.maxlen,
            }
        if category == "instance":
            # VALUES has checked all class access hooks and the intrinsic dict descriptor.
            attributes = object.__getattribute__(obj, "__dict__")
            if type(attributes) is not dict:
                return self._opaque(object_id, obj)
            if dict.__len__(attributes) > self._serializer._instance_attributes:
                raise GraphLimitError(
                    "COLLECTION_LIMIT", "Maximum instance attribute count exceeded."
                )
            if any(type(key) is not str for key in dict.__iter__(attributes)):
                return self._opaque(object_id, obj)
            return {
                "id": object_id,
                "kind": "instance",
                "className": self._serializer._text(_safe_type_name(type(obj))),
                "attrs": self.bindings(attributes),
            }
        return self._opaque(object_id, obj)

    def finish(self) -> list[ObjectRecord]:
        while self._pending:
            object_id = self._pending.popleft()
            obj = self._serializer._registry.lookup(object_id)
            record = self._record(object_id, obj)
            # Values and binding names have already been charged. Only add disjoint header.
            header = dict(record)
            for field in ("items", "entries", "attrs"):
                if field in header:
                    header[field] = []
            self.spend(canonical_size(header))
            self._records[object_id] = record
        return [self._records[key] for key in sorted(self._records, key=_id_order)]
