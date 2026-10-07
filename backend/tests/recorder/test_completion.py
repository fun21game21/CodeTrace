import pytest
from codetrace_backend.trace_recorder import RecorderError, build_completion
from codetrace_contracts import validate_contract
from codetrace_contracts.config import DIAGNOSTIC_BYTES

from .helpers import DOCUMENT, terminal

CASES = (
    [
        ("SUCCESS", "NONE", True),
        ("USER_ERROR", "SYNTAX_ERROR", True),
        ("USER_ERROR", "UNCAUGHT_EXCEPTION", True),
        ("USER_ERROR", "UNSUPPORTED_FEATURE", True),
    ]
    + [
        ("LIMIT_EXCEEDED", reason, False)
        for reason in [
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
        ]
    ]
    + [
        ("SYSTEM_ERROR", reason, False)
        for reason in [
            "DOCKER_UNAVAILABLE",
            "IMAGE_UNAVAILABLE",
            "CLIENT_PROTOCOL_ERROR",
            "RUNNER_PROTOCOL_ERROR",
            "RUNNER_FAILURE",
            "TRACE_RECORDER_FAILURE",
            "FINALIZATION_TIMEOUT",
            "CLEANUP_FAILED",
        ]
    ]
    + [("INTERRUPTED", reason, False) for reason in ["CONNECTION_LOST", "BACKEND_SHUTDOWN"]]
)


@pytest.mark.parametrize("outcome,reason,complete", CASES)
@pytest.mark.parametrize("observations", [0, 2])
def test_completion_policy_table(outcome, reason, complete, observations):
    t = terminal(outcome=outcome, reason=reason, oomKilled=reason == "MEMORY_LIMIT")
    if outcome == "SUCCESS" and observations == 0:
        with pytest.raises(RecorderError):
            build_completion(t, observations)
        return
    result = build_completion(t, observations)
    validate_contract(result, "trace", "Completion")
    assert result["outcome"] == outcome and result["reason"] == reason
    assert result["traceComplete"] == complete
    assert result["stateAccuracy"] == (
        "unavailable" if observations == 0 else "final" if complete else "last_observed"
    )
    for key in ["acceptedAt", "finishedAt", "activeElapsedMs", "waitingElapsedMs", "exitCode"]:
        assert result[key] == getattr(t, key)


@pytest.mark.parametrize("variant", ["valid", "oversized", "mismatch"])
def test_diagnostic_bounded_detached(variant):
    from codetrace_backend.runs.protocol import encode

    diagnostic = {
        "category": "runtime",
        "code": "UNCAUGHT_EXCEPTION",
        "typeName": "ValueError",
        "message": "Failure",
        "source": None,
        "columnNumber": None,
        "traceback": [],
        "omittedTracebackFrames": 0,
    }
    if variant == "oversized":
        diagnostic["message"] = "😀" * DIAGNOSTIC_BYTES
    if variant == "mismatch":
        diagnostic["code"] = "EXCEPTION_OBSERVED"
    result = build_completion(
        terminal(outcome="USER_ERROR", reason="UNCAUGHT_EXCEPTION", runnerDiagnostic=diagnostic), 2
    )
    d = result["primaryDiagnostic"]
    assert len(encode(d)) <= DIAGNOSTIC_BYTES
    assert d["code"] == "UNCAUGHT_EXCEPTION"
    assert d is not diagnostic
    if variant == "valid":
        assert d == diagnostic
    else:
        assert d["message"] == "Execution ended: UNCAUGHT_EXCEPTION"


def test_success_exact_completion():
    assert build_completion(terminal(), 2) == DOCUMENT["completion"]
