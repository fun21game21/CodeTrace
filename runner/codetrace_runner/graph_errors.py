"""Graph limits expose the existing CONTRACTS Diagnostic, without a new wire format."""

from typing import Literal

from codetrace_contracts.types import Diagnostic

GraphLimitCode = Literal["COLLECTION_LIMIT", "STATE_BYTES_LIMIT", "FRAME_LIMIT"]


class GraphLimitError(Exception):
    def __init__(self, code: GraphLimitCode, message: str) -> None:
        Exception.__init__(self, message)
        self.reason = code
        self.diagnostic: Diagnostic = {
            "category": "limit",
            "code": code,
            "typeName": None,
            "message": message,
            "source": None,
            "columnNumber": None,
            "traceback": [],
            "omittedTracebackFrames": 0,
        }
