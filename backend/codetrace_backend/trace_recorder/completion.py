"""One completion policy; execution outcome is never recomputed from Docker."""

import json
from dataclasses import replace
from typing import Literal, cast

from codetrace_contracts import validate_contract
from codetrace_contracts.config import DIAGNOSTIC_BYTES
from codetrace_contracts.types import Completion, Diagnostic, ReasonCode

from ..runs import TerminalResult
from ..runs.protocol import encode
from .types import RecorderError

NORMAL = frozenset({"NONE", "SYNTAX_ERROR", "UNCAUGHT_EXCEPTION", "UNSUPPORTED_FEATURE"})
LIMITS = frozenset(
    {
        "ACTIVE_TIMEOUT",
        "INPUT_IDLE_TIMEOUT",
        "CONTAINER_LIFETIME",
        "STARTUP_TIMEOUT",
        "MEMORY_LIMIT",
        "EVENT_LIMIT",
        "TRACE_BYTES_LIMIT",
        "REGISTRY_LIMIT",
        "STATE_BYTES_LIMIT",
        "COLLECTION_LIMIT",
        "STRING_LIMIT",
        "INT_DIGITS_LIMIT",
        "CONSOLE_LIMIT",
        "FRAME_LIMIT",
        "INPUT_REQUEST_LIMIT",
    }
)
CATEGORIES = {
    "SYNTAX_ERROR": "syntax",
    "UNCAUGHT_EXCEPTION": "runtime",
    "UNSUPPORTED_FEATURE": "unsupported",
}


def primary_diagnostic(terminal: TerminalResult) -> Diagnostic | None:
    if terminal.outcome == "SUCCESS":
        return None
    candidate = terminal.runnerDiagnostic
    if candidate is not None and candidate["code"] == terminal.reason:
        validate_contract(candidate, "trace", "Diagnostic")
        if len(encode(candidate)) <= DIAGNOSTIC_BYTES:
            return cast(Diagnostic, json.loads(encode(candidate)))
    # No unbounded reformatting/copy of a Runner buffer or exception.
    result = cast(
        Diagnostic,
        {
            "category": CATEGORIES.get(
                terminal.reason, "limit" if terminal.reason in LIMITS else "infrastructure"
            ),
            "code": terminal.reason,
            "typeName": None,
            "message": "Execution ended: " + terminal.reason,
            "source": None,
            "columnNumber": None,
            "traceback": [],
            "omittedTracebackFrames": 0,
        },
    )
    validate_contract(result, "trace", "Diagnostic")
    return result


def build_completion(terminal: TerminalResult, observations: int) -> Completion:
    normal = terminal.reason in NORMAL and terminal.outcome in {"SUCCESS", "USER_ERROR"}
    accuracy: Literal["final", "last_observed", "unavailable"] = (
        "final" if normal and observations else "last_observed" if observations else "unavailable"
    )
    if terminal.outcome == "SUCCESS" and not observations:
        raise RecorderError()  # Cannot invent an observation for SUCCESS.
    result: Completion = {
        "outcome": terminal.outcome,
        "reason": terminal.reason,
        "traceComplete": normal and terminal.cleanupConfirmed,
        "stateAccuracy": accuracy,
        "primaryDiagnostic": primary_diagnostic(terminal),
        "acceptedAt": terminal.acceptedAt,
        "finishedAt": terminal.finishedAt,
        "activeElapsedMs": terminal.activeElapsedMs,
        "waitingElapsedMs": terminal.waitingElapsedMs,
        "exitCode": terminal.exitCode,
        "cleanupConfirmed": terminal.cleanupConfirmed,
    }
    validate_contract(result, "trace", "Completion")
    return result


def publication_failure(terminal: TerminalResult, reason: str, finished_at: str) -> Completion:
    # A separate publication layer. The frozen WATCHDOG result remains unchanged.
    updated = replace(
        terminal,
        outcome="SYSTEM_ERROR",
        reason=cast(ReasonCode, reason),
        runnerDiagnostic=None,
        finishedAt=finished_at,
    )
    return build_completion(updated, 0)
