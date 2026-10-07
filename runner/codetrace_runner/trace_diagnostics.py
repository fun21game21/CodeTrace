"""Observe exception metadata without dispatching user str/repr/attribute hooks."""

from typing import cast

from codetrace_contracts.config import DIAGNOSTIC_BYTES
from codetrace_contracts.types import Diagnostic, SourceLocation

from .canonical import canonical_size

_ARGS = BaseException.__dict__["args"]
_TYPE_NAME = vars(type)["__name__"]
_TRUNCATED = "... [truncated]"


def observed_exception(exception: BaseException, source: SourceLocation) -> Diagnostic:
    # Intrinsic descriptors bypass even an overridden args property/metaclass name.
    raw_name = _TYPE_NAME.__get__(type(exception))
    name = raw_name if type(raw_name) is str else "Exception"
    if len(name) > 512:
        name = name[:512] + _TRUNCATED
    args = cast(tuple[object, ...], _ARGS.__get__(exception))
    message = "Exception observed (message unavailable without user hooks)."
    if args and type(args[0]) is str:
        message = args[0]
    diagnostic: Diagnostic = {
        "category": "runtime",
        "code": "EXCEPTION_OBSERVED",
        "typeName": name,
        "message": "",
        "source": source.copy(),
        "columnNumber": None,
        "traceback": [],
        "omittedTracebackFrames": 0,
    }
    # Pre-bound allocations, then fit the WHOLE ASCII-escaped diagnostic exactly.
    candidate = message[:DIAGNOSTIC_BYTES]
    diagnostic["message"] = candidate
    if len(candidate) == len(message) and canonical_size(diagnostic) <= DIAGNOSTIC_BYTES:
        return diagnostic
    low, high = 0, len(candidate)
    while low < high:
        middle = (low + high + 1) // 2
        diagnostic["message"] = candidate[:middle] + _TRUNCATED
        if canonical_size(diagnostic) <= DIAGNOSTIC_BYTES:
            low = middle
        else:
            high = middle - 1
    diagnostic["message"] = candidate[:low] + _TRUNCATED
    return diagnostic
