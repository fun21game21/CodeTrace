"""ENCODER failures use existing reasons and Diagnostic, not a new wire contract."""

from typing import Literal

from codetrace_contracts.types import Diagnostic

EncoderLimitCode = Literal["EVENT_LIMIT", "STATE_BYTES_LIMIT", "TRACE_BYTES_LIMIT"]


class TraceEncodingError(RuntimeError):
    """Invalid internal reference data or Encoder invariant; never a user error."""


class EncoderLimitError(TraceEncodingError):
    def __init__(self, reason: EncoderLimitCode) -> None:
        messages = {
            "EVENT_LIMIT": "Maximum Python event count exceeded (RUN_END slot reserved).",
            "STATE_BYTES_LIMIT": "Maximum canonical NormalizedState size exceeded.",
            "TRACE_BYTES_LIMIT": "Encoder records exceed the ordinary trace byte budget.",
        }
        super().__init__(messages[reason])
        self.reason = reason
        self.diagnostic: Diagnostic = {
            "category": "limit",
            "code": reason,
            "typeName": None,
            "message": messages[reason],
            "source": None,
            "columnNumber": None,
            "traceback": [],
            "omittedTracebackFrames": 0,
        }
