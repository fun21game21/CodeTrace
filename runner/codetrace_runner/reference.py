"""Thin TRACING harness for trusted, finite reference tests. NOT the isolated Runner."""

import builtins
import sys

from codetrace_contracts.config import SOURCE_FILE_NAME
from codetrace_contracts.types import ResourceLimits

from .registry import ObjectRegistry
from .serializer import GraphSerializer
from .trace_engine import TraceEngine, TraceEngineError
from .trace_types import ReferenceExecution


def trace_source(source: str, *, limits: ResourceLimits | None = None) -> ReferenceExecution:
    """Compile once, observe once, restore tracing and release run-owned references.

    Syntax errors propagate before tracing starts. Runtime exceptions escaping exec
    are observed by this harness only as a boolean; TRACING never creates Completion.
    No timeout/import/input/security policy is implemented in this test harness.
    """
    if type(source) is not str:
        raise TypeError("Reference source must be an exact string")
    code = compile(source, SOURCE_FILE_NAME, "exec", dont_inherit=True, optimize=0)
    # Python recognizes CR/LF/CRLF, not str.splitlines' other Unicode separators.
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    line_count = len(normalized.split("\n")) if source else 0
    user_globals: dict[str, object] = {
        "__name__": "__main__",
        "__file__": SOURCE_FILE_NAME,
        "__builtins__": vars(builtins).copy(),
    }
    registry = ObjectRegistry(limits)
    engine = TraceEngine(code, line_count, user_globals, GraphSerializer(registry, limits))
    callback = engine.callback
    previous = sys.gettrace()
    escaped = False
    try:
        sys.settrace(callback)
        try:
            exec(code, user_globals)
        except BaseException:
            escaped = True
        # First inspect the sticky failure; then detect unsupported tracing loss.
        trace = engine.result()
        if sys.gettrace() is not callback:
            raise TraceEngineError("Reference trace callback was replaced during execution")
        return ReferenceExecution(trace, escaped)
    finally:
        sys.settrace(previous)
        engine.close()
        registry.close()
        user_globals.clear()
