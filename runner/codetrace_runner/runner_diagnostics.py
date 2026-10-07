"""Bounded terminal DTO diagnostics without exception formatting hooks."""

from types import TracebackType
from typing import cast

from codetrace_contracts.config import DIAGNOSTIC_BYTES, SOURCE_FILE_NAME
from codetrace_contracts.types import Diagnostic, DiagnosticFrame, ReasonCode, SourceLocation

from .canonical import canonical_size
from .trace_diagnostics import observed_exception


def location(line: int | None) -> SourceLocation:
    return {"fileName": "<codetrace-user>", "lineNumber": line}


def neutral(code: ReasonCode = "RUNNER_FAILURE") -> Diagnostic:
    return {
        "category": "infrastructure",
        "code": code,
        "typeName": None,
        "message": "Runner execution or control channel failed.",
        "source": None,
        "columnNumber": None,
        "traceback": [],
        "omittedTracebackFrames": 0,
    }


def fit(diagnostic: Diagnostic) -> Diagnostic:
    while diagnostic["traceback"] and canonical_size(diagnostic) > DIAGNOSTIC_BYTES:
        diagnostic["traceback"].pop(0)
        diagnostic["omittedTracebackFrames"] += 1
    if canonical_size(diagnostic) > DIAGNOSTIC_BYTES:
        message = diagnostic["message"]
        low, high = 0, min(len(message), DIAGNOSTIC_BYTES)
        while low < high:
            mid = (low + high + 1) // 2
            diagnostic["message"] = message[:mid] + "... [truncated]"
            if canonical_size(diagnostic) <= DIAGNOSTIC_BYTES:
                low = mid
            else:
                high = mid - 1
        diagnostic["message"] = message[:low] + "... [truncated]"
    return diagnostic


def uncaught(exception: BaseException, tb: TracebackType | None) -> Diagnostic:
    diagnostic = observed_exception(exception, location(None))
    diagnostic["code"] = "UNCAUGHT_EXCEPTION"
    frames: list[DiagnosticFrame] = []
    omitted = 0
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename == SOURCE_FILE_NAME:
            name = code.co_name
            if len(name) > 512:
                name = name[:512] + "... [truncated]"
            frames.append({"name": name, "source": location(tb.tb_lineno)})
            if len(frames) > 32:
                frames.pop(0)
                omitted += 1
        tb = tb.tb_next
    diagnostic["traceback"] = frames
    diagnostic["omittedTracebackFrames"] = omitted
    diagnostic["source"] = frames[-1]["source"].copy() if frames else None
    return fit(diagnostic)


def syntax(exception: SyntaxError, source: str) -> Diagnostic:
    # Only compiler-created exact SyntaxError reaches this path (before user exec).
    line = exception.lineno
    diagnostic = observed_exception(exception, location(line))
    diagnostic["category"] = "syntax"
    diagnostic["code"] = "SYNTAX_ERROR"
    diagnostic["message"] = exception.msg
    offset = exception.offset
    # CPython SyntaxError.offset is already one-based Unicode code points.
    diagnostic["columnNumber"] = offset if offset is not None and offset > 0 else None
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if line is None or not 1 <= line <= len(lines):
        diagnostic["source"] = location(None)
    return fit(diagnostic)


class UnsupportedFeature(Exception):
    def __init__(self, line: int | None = None) -> None:
        super().__init__("This Python feature is unsupported in CodeTrace v1.")
        self.diagnostic = cast(
            Diagnostic,
            {
                "category": "unsupported",
                "code": "UNSUPPORTED_FEATURE",
                "typeName": None,
                "message": "This Python feature is unsupported in CodeTrace v1.",
                "source": location(line),
                "columnNumber": None,
                "traceback": [],
                "omittedTracebackFrames": 0,
            },
        )
