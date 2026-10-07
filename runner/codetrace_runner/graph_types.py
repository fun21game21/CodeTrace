"""Internal SERIALIZER inputs and pre-version output; these are not new wire contracts."""

from dataclasses import dataclass
from typing import Literal, TypedDict

from codetrace_contracts.types import Binding, Frame, FrameId, ObjectRecord, SourceLocation, Value


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class FrameInput:
    id: FrameId
    parentId: FrameId | None
    kind: Literal["module", "function", "class"]
    name: str
    qualifiedName: str
    definitionLine: int | None
    callSite: SourceLocation | None
    parameterNames: list[str] | tuple[str, ...]
    localsSource: Literal["own", "globals"]
    live_locals: dict[str, object]


class GraphSnapshot(TypedDict):
    globals: list[Binding]
    frames: list[Frame]
    stack: list[FrameId]
    objects: list[ObjectRecord]
    eventRoots: list[Value]
