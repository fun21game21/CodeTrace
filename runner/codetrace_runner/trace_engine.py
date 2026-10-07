"""Reference sys.settrace observer for supported CPython 3.13.16 source scopes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import CO_NEWLOCALS, CO_OPTIMIZED, CO_VARARGS, CO_VARKEYWORDS
from types import CodeType, FrameType
from typing import TYPE_CHECKING, Literal, cast

from codetrace_contracts.config import SOURCE_FILE_NAME
from codetrace_contracts.types import (
    Diagnostic,
    EmptyPayload,
    ExceptionPayload,
    ReturnKnown,
    ReturnUnknown,
    SourceLocation,
)

from .errors import ValueLimitError
from .graph_errors import GraphLimitError
from .graph_types import FrameInput
from .serializer import GraphSerializer
from .trace_diagnostics import observed_exception
from .trace_types import ReferenceTrace, ReferenceTracePoint

if TYPE_CHECKING:
    from _typeshed import TraceFunction


class TraceEngineError(RuntimeError):
    """Observation failed; no reference trace may be published."""


class ReferenceLimitError(RuntimeError):
    """VALUES/SERIALIZER resource limit, kept separate from an infrastructure defect."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic["message"])
        self.diagnostic = diagnostic
        self.reason = diagnostic["code"]


class _TraceAbort(BaseException):
    """Leave the callback; the harness's sticky failure check is authoritative."""


def parameter_names(code: CodeType) -> tuple[str, ...]:
    positional = code.co_varnames[: code.co_argcount]
    end = code.co_argcount + code.co_kwonlyargcount
    keywords = code.co_varnames[code.co_argcount : end]
    varargs: tuple[str, ...] = ()
    kwargs: tuple[str, ...] = ()
    if code.co_flags & CO_VARARGS:
        varargs = (code.co_varnames[end],)
        end += 1
    if code.co_flags & CO_VARKEYWORDS:
        kwargs = (code.co_varnames[end],)
    return positional + varargs + keywords + kwargs


@dataclass(slots=True, eq=False, repr=False)
class _ActiveFrame:
    live: FrameType
    id: str
    parent_id: str | None
    kind: Literal["module", "function", "class"]
    call_site: SourceLocation | None


class TraceEngine:
    """One compiled module, one run. Only active entries retain live frames.

    The same observer feeds reference collection or a synchronous production sink.
    It is not a sandbox or a recorder.
    The caller owns Registry lifetime and must call close in its finally block.
    """

    def __init__(
        self,
        code: CodeType,
        line_count: int,
        user_globals: dict[str, object],
        serializer: GraphSerializer,
        *,
        point_sink: Callable[[ReferenceTracePoint], None] | None = None,
        collect_points: bool = True,
        failure_observer: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._point_sink = point_sink
        self._collect_points = collect_points
        self._failure_observer = failure_observer
        self._code = code
        self._line_count = line_count
        self._globals = user_globals
        self._serializer = serializer
        self._active: dict[int, _ActiveFrame] = {}
        self._stack: list[_ActiveFrame] = []
        self._next_frame = 1
        self._points: list[ReferenceTracePoint] = []
        self._failed = False
        self._limit: Diagnostic | None = None
        self._closed = False

    def _line(self, line: int) -> int | None:
        return line if 1 <= line <= self._line_count else None

    def _source(self, frame: FrameType) -> SourceLocation:
        return {
            "fileName": cast(Literal["<codetrace-user>"], SOURCE_FILE_NAME),
            "lineNumber": self._line(frame.f_lineno),
        }

    def _enter(self, frame: FrameType) -> _ActiveFrame:
        if id(frame) in self._active:
            raise TraceEngineError("Repeated entry into an active frame is unsupported")
        caller = frame.f_back
        parent: _ActiveFrame | None = None
        while caller is not None:
            candidate = self._active.get(id(caller))
            if candidate is not None and candidate.live is caller:
                parent = candidate
                break
            caller = caller.f_back
        if parent is not (self._stack[-1] if self._stack else None):
            raise TraceEngineError("User frame ancestry does not match the active stack")
        code = frame.f_code
        kind: Literal["module", "function", "class"]
        if code is self._code:
            kind = "module"
        elif code.co_flags & (CO_NEWLOCALS | CO_OPTIMIZED) == (CO_NEWLOCALS | CO_OPTIMIZED):
            kind = "function"
        elif not code.co_flags & (CO_NEWLOCALS | CO_OPTIMIZED):
            # CPython compile.c compute_code_flags: within the supported source
            # scopes, only module and class bodies lack the function flags.
            kind = "class"
        else:
            raise TraceEngineError("Unsupported code scope flags")
        entry = _ActiveFrame(
            frame,
            f"f{self._next_frame}",
            None if parent is None else parent.id,
            kind,
            None if parent is None else self._source(parent.live),
        )
        self._next_frame += 1
        self._active[id(frame)] = entry
        self._stack.append(entry)
        return entry

    def _frames(self) -> list[FrameInput]:
        result: list[FrameInput] = []
        for entry in self._stack:
            code = entry.live.f_code
            live_locals: dict[str, object] = {}
            if entry.kind != "module":
                namespace = entry.live.f_locals
                if code.co_flags & CO_OPTIMIZED:
                    # Guaranteed intrinsic frame-locals proxy on normative CPython.
                    live_locals = dict(namespace)
                elif type(namespace) is dict:
                    live_locals = dict.copy(namespace)
                else:
                    raise TraceEngineError("Custom class namespace mappings are unsupported")
            result.append(
                FrameInput(
                    id=entry.id,
                    parentId=entry.parent_id,
                    kind=entry.kind,
                    name=code.co_name,
                    qualifiedName=code.co_qualname,
                    definitionLine=self._line(code.co_firstlineno),
                    callSite=entry.call_site,
                    parameterNames=parameter_names(code) if entry.kind == "function" else (),
                    localsSource="globals" if entry.kind == "module" else "own",
                    # CPython 3.13 optimized locals are a builtin frame-locals
                    # proxy; copy them now. SERIALIZER accepts only detached exact dicts.
                    live_locals=live_locals,
                )
            )
        return result

    def _observe(self, frame: FrameType, event: str, arg: object) -> None:
        source = self._source(frame)
        if event == "line" and source["lineNumber"] is None:
            return
        if event == "call":
            entry = self._enter(frame)
        else:
            entry = self._active[id(frame)]
            if entry.live is not frame or not self._stack or self._stack[-1] is not entry:
                raise TraceEngineError("Live frame identity/stack invariant violated")
        roots = (arg,) if event == "return" and arg is not None else ()
        snapshot = self._serializer.serialize(
            self._globals, self._frames(), [item.id for item in self._stack], roots
        )
        payload: EmptyPayload | ReturnKnown | ReturnUnknown | ExceptionPayload = {}
        if event == "return":
            if arg is None:
                payload = {"returnValueKnown": False, "returnValue": None}
            else:
                # SERIALIZER encoded the very same root, including its stable ObjectId.
                payload = cast(
                    ReturnKnown,
                    {"returnValueKnown": True, "returnValue": snapshot["eventRoots"][0]},
                )
        elif event == "exception":
            _, exception, _ = cast(tuple[type[BaseException], BaseException, object], arg)
            payload = {"diagnostic": observed_exception(exception, source)}
        point = ReferenceTracePoint(
            cast(Literal["CALL", "LINE", "RETURN", "EXCEPTION"], event.upper()),
            entry.id,
            source,
            payload,
            snapshot,
        )
        if self._point_sink is not None:
            self._point_sink(point)
        if self._collect_points:
            self._points.append(point)
        if event == "return":
            self._stack.pop()
            del self._active[id(frame)]

    def callback(self, frame: FrameType, event: str, arg: object) -> TraceFunction | None:
        if frame.f_code.co_filename != SOURCE_FILE_NAME:
            return None
        if self._closed or self._failed:
            raise _TraceAbort()
        if event not in ("call", "line", "return", "exception"):
            return self.callback
        try:
            self._observe(frame, event, arg)
        except BaseException as error:
            # CPython disables tracing when a callback raises. A user can catch
            # even BaseException: retain a failure marker independent of unwinding.
            self._failed = True
            if type(error) is ValueLimitError or type(error) is GraphLimitError:
                self._limit = error.diagnostic
            if self._failure_observer is not None:
                self._failure_observer(error)
            raise _TraceAbort() from None
        return self.callback

    def result(self) -> ReferenceTrace:
        if self._limit is not None:
            raise ReferenceLimitError(self._limit)
        if self._failed or self._active or self._closed:
            raise TraceEngineError("Reference observation failed; trace is unavailable")
        return ReferenceTrace(tuple(self._points))

    def close(self) -> None:
        self._closed = True
        self._active.clear()
        self._stack.clear()
        self._globals = {}
        self._points.clear()
