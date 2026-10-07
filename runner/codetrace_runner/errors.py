"""Domain limits expose the existing CONTRACTS Diagnostic, with no new wire format."""

from typing import Literal

from codetrace_contracts.types import Diagnostic

ValueLimitCode = Literal["REGISTRY_LIMIT", "STRING_LIMIT", "INT_DIGITS_LIMIT"]


class ValueLimitError(Exception):
    """A caller can forward diagnostic to the future Runner abort_request."""

    def __init__(self, code: ValueLimitCode) -> None:
        messages = {
            "REGISTRY_LIMIT": "Maximum registered object count exceeded.",
            "STRING_LIMIT": "Maximum UTF-8 string size exceeded.",
            "INT_DIGITS_LIMIT": "Maximum integer decimal digit count exceeded.",
        }
        message = messages[code]
        super().__init__(message)
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
        self.reason: ValueLimitCode = code


class RegistryClosedError(RuntimeError):
    """Internal API misuse; this exception does not define a wire message."""


def positive_limit(value: int, name: str) -> int:
    """Limits are trusted configuration, but reject bool and invalid numbers."""
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
