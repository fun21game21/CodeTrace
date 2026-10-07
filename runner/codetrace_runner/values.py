"""Encode exact scalars or register a non-scalar, without following children."""

from types import NoneType
from typing import cast

from codetrace_contracts.config import default_limits
from codetrace_contracts.types import (
    BoolValue,
    FloatValue,
    IntValue,
    NoneValue,
    ResourceLimits,
    StrValue,
    Value,
)

from .classification import is_scalar
from .errors import RegistryClosedError, ValueLimitError, positive_limit
from .registry import ObjectRegistry

ScalarValue = NoneValue | BoolValue | IntValue | FloatValue | StrValue


def _decimal_text(value: int) -> str:
    try:
        return int.__repr__(value)
    except ValueError:
        # A lower process-wide int conversion guard must not invalidate effective limits.
        # Each chunk is at most nine decimal digits; never change sys's global setting.
        magnitude = -value if value < 0 else value
        chunks: list[str] = []
        while magnitude >= 1_000_000_000:
            magnitude, remainder = divmod(magnitude, 1_000_000_000)
            chunks.append(f"{remainder:09d}")
        text = str(magnitude) + "".join(reversed(chunks))
        return "-" + text if value < 0 else text


class ScalarEncoder:
    def __init__(self, limits: ResourceLimits | None = None) -> None:
        effective = default_limits() if limits is None else limits
        self._string_bytes = positive_limit(effective["string_bytes"], "string_bytes")
        self._integer_digits = positive_limit(
            effective["integer_decimal_digits"], "integer_decimal_digits"
        )
        # log2(10) < 3.322. This integer-only ceiling never rejects an allowed integer.
        self._integer_bits = (self._integer_digits * 3322 + 999) // 1000

    def encode(self, value: object) -> ScalarValue:
        cls = type(value)
        if cls is NoneType:
            return {"kind": "scalar", "type": "none", "value": None}
        if cls is bool:
            return {"kind": "scalar", "type": "bool", "value": cast(bool, value)}
        if cls is int:
            integer = cast(int, value)
            if int.bit_length(integer) > self._integer_bits:
                raise ValueLimitError("INT_DIGITS_LIMIT")
            text = _decimal_text(integer)
            digits = len(text) - (1 if integer < 0 else 0)
            if digits > self._integer_digits:
                raise ValueLimitError("INT_DIGITS_LIMIT")
            return {"kind": "scalar", "type": "int", "value": text}
        if cls is float:
            return {"kind": "scalar", "type": "float", "value": float.__repr__(value)}
        if cls is str:
            text = cast(str, value)
            # Every character occupies at least one UTF-8 byte, including surrogatepass.
            # Bound the temporary byte allocation before encoding a potentially huge string.
            if len(text) > self._string_bytes:
                raise ValueLimitError("STRING_LIMIT")
            if len(str.encode(text, "utf-8", "surrogatepass")) > self._string_bytes:
                raise ValueLimitError("STRING_LIMIT")
            return {"kind": "scalar", "type": "str", "value": text}
        raise TypeError("ScalarEncoder requires an exact None/bool/int/float/str value")


class ValueEncoder:
    def __init__(self, registry: ObjectRegistry, limits: ResourceLimits | None = None) -> None:
        self._scalars = ScalarEncoder(limits)
        self._registry = registry

    def encode(self, value: object) -> Value:
        if self._registry.closed:
            raise RegistryClosedError("Object Registry is closed")
        if is_scalar(value):
            return self._scalars.encode(value)
        return self._registry.get_or_register(value)
