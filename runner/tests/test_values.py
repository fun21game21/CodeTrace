import json
import math
import sys

import pytest
from codetrace_contracts import default_limits, validate_contract
from codetrace_contracts.validators import CONTRACTS_DIRECTORY
from codetrace_runner import (
    ObjectRegistry,
    RegistryClosedError,
    ScalarEncoder,
    ValueEncoder,
    values,
)
from codetrace_runner.errors import ValueLimitError


@pytest.mark.parametrize(
    "value,kind,encoded",
    [
        (None, "none", None),
        (True, "bool", True),
        (False, "bool", False),
        (0, "int", "0"),
        (1, "int", "1"),
        (-123, "int", "-123"),
        (9007199254740993, "int", "9007199254740993"),
        (-9007199254740993, "int", "-9007199254740993"),
        (0.0, "float", "0.0"),
        (-0.0, "float", "-0.0"),
        (1.25, "float", "1.25"),
        (-1.25, "float", "-1.25"),
        (1e20, "float", "1e+20"),
        (1e-7, "float", "1e-07"),
        (5e-324, "float", "5e-324"),
        (sys.float_info.max, "float", "1.7976931348623157e+308"),
        (math.nan, "float", "nan"),
        (-math.nan, "float", "nan"),
        (math.inf, "float", "inf"),
        (-math.inf, "float", "-inf"),
        ("", "str", ""),
        ("Привет 🌳\n\x00", "str", "Привет 🌳\n\x00"),
        ("\ud800", "str", "\ud800"),
    ],
)
def test_exact_scalar_contract(value, kind, encoded):
    result = ScalarEncoder().encode(value)
    assert result == {"kind": "scalar", "type": kind, "value": encoded}
    validate_contract(result, "trace", "Value")


def test_bool_and_int_are_distinct_and_do_not_enter_registry():
    registry = ObjectRegistry()
    encoder = ValueEncoder(registry)
    assert encoder.encode(True) != encoder.encode(1)
    assert encoder.encode(False) != encoder.encode(0)
    assert registry.count == 0


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("magnitude", [10**999, 10**1000 - 1])
def test_integer_at_default_digit_limit(sign, magnitude):
    result = ScalarEncoder().encode(sign * magnitude)
    assert result["type"] == "int"
    assert int(result["value"]) == sign * magnitude
    assert len(result["value"].lstrip("-")) == 1000
    validate_contract(result, "trace", "Value")


@pytest.mark.parametrize(
    "value", [10**1000, -(10**1000), 1 << 100000], ids=["positive", "negative", "huge"]
)
def test_integer_over_default_limit(value):
    with pytest.raises(ValueLimitError) as caught:
        ScalarEncoder().encode(value)
    assert caught.value.reason == "INT_DIGITS_LIMIT"
    validate_contract(caught.value.diagnostic, "trace", "Diagnostic")


def test_large_integer_rejected_before_decimal_conversion(monkeypatch):
    def forbidden(value):
        pytest.fail("Oversized integer reached decimal conversion")

    monkeypatch.setattr(values, "_decimal_text", forbidden)
    with pytest.raises(ValueLimitError):
        ScalarEncoder().encode(1 << 100000)


@pytest.mark.parametrize("digits", [1, 2, 3, 10, 100, 1000])
def test_integer_guard_has_no_false_rejection_at_decimal_boundary(digits):
    limits = default_limits()
    limits["integer_decimal_digits"] = digits
    encoder = ScalarEncoder(limits)
    assert encoder.encode(10**digits - 1)["value"] == "9" * digits
    assert encoder.encode(-(10**digits - 1))["value"] == "-" + "9" * digits
    with pytest.raises(ValueLimitError):
        encoder.encode(10**digits)


@pytest.mark.parametrize("digits,sign", [(1000, 1), (1000, -1), (5000, 1), (5000, -1)])
def test_native_integer_conversion_guard_does_not_change_effective_limit(digits, sign):
    limits = default_limits()
    limits["integer_decimal_digits"] = digits
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        result = ScalarEncoder(limits).encode(sign * (10**digits - 1))
        assert result["value"] == ("-" if sign < 0 else "") + "9" * digits
        assert sys.get_int_max_str_digits() == 640
        validate_contract(result, "trace", "Value")
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize(
    "text,size",
    [
        ("a" * 16384, 16384),
        ("я" * 8192, 16384),
        ("😀" * 4096, 16384),
        ("\ud800", 3),
        ("\ud800\udc00", 6),
        ("é", 2),
        ("e\u0301", 3),
    ],
)
def test_string_exact_byte_boundary_and_one_byte_over(text, size):
    limits = default_limits()
    limits["string_bytes"] = size
    result = ScalarEncoder(limits).encode(text)
    assert result["value"] is text
    validate_contract(result, "trace", "Value")
    with pytest.raises(ValueLimitError) as caught:
        ScalarEncoder(limits).encode(text + "a")
    assert caught.value.reason == "STRING_LIMIT"
    validate_contract(caught.value.diagnostic, "trace", "Diagnostic")


def test_large_string_rejected_without_truncation():
    with pytest.raises(ValueLimitError):
        ScalarEncoder().encode("a" * 100000)


SCALAR_FIXTURES = sorted((CONTRACTS_DIRECTORY / "fixtures/valid").glob("value-*.json"))
SCALAR_FIXTURES = [p for p in SCALAR_FIXTURES if json.loads(p.read_text()).get("kind") == "scalar"]


@pytest.mark.parametrize("path", SCALAR_FIXTURES, ids=lambda p: p.name)
def test_encoding_agrees_with_existing_contracts_scalar_fixtures(path):
    fixture = json.loads(path.read_text())
    value = fixture["value"]
    if fixture["type"] == "int":
        value = int(value)
    elif fixture["type"] == "float":
        value = float(value)
    assert ScalarEncoder().encode(value) == fixture


@pytest.mark.parametrize("value", [[], (), {}, set(), b"abc", object()])
def test_scalar_encoder_rejects_non_scalars(value):
    with pytest.raises(TypeError):
        ScalarEncoder().encode(value)


def test_value_encoder_emits_ref_only_and_follows_registry_lifecycle():
    registry = ObjectRegistry()
    encoder = ValueEncoder(registry)
    obj = []
    assert encoder.encode(obj) == {"$ref": "o1"}
    assert encoder.encode(obj) == {"$ref": "o1"}
    assert encoder.encode(None) == {"kind": "scalar", "type": "none", "value": None}
    registry.close()
    for value in (obj, None, 1):
        with pytest.raises(RegistryClosedError):
            encoder.encode(value)


def test_scalar_limit_configuration_is_copied_for_run():
    limits = default_limits()
    limits["string_bytes"] = 3
    limits["integer_decimal_digits"] = 2
    encoder = ScalarEncoder(limits)
    limits["string_bytes"] = 100
    limits["integer_decimal_digits"] = 100
    with pytest.raises(ValueLimitError):
        encoder.encode("abcd")
    with pytest.raises(ValueLimitError):
        encoder.encode(100)


@pytest.mark.parametrize("key", ["string_bytes", "integer_decimal_digits"])
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10", None])
def test_invalid_scalar_limit_rejected(key, invalid):
    limits = default_limits()
    limits[key] = invalid
    with pytest.raises(ValueError):
        ScalarEncoder(limits)
