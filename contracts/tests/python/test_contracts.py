import copy
import json
from typing import get_args, get_type_hints

import pytest
from codetrace_contracts import (
    ContractValidationError,
    default_config,
    default_limits,
    types,
    validate_contract,
    validate_runner,
    validate_trace,
    validate_ws,
)
from codetrace_contracts.validators import CONTRACTS_DIRECTORY, load_schemas
from jsonschema import Draft202012Validator

FIXTURES = CONTRACTS_DIRECTORY / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


@pytest.mark.parametrize("case", MANIFEST, ids=lambda c: c["file"])
def test_shared_fixture(case):
    value = json.loads((FIXTURES / case["file"]).read_text())
    original = copy.deepcopy(value)
    if case["valid"]:
        validate_contract(value, case["contract"], case["definition"])
    else:
        with pytest.raises(ContractValidationError):
            validate_contract(value, case["contract"], case["definition"])
    assert value == original


@pytest.mark.parametrize("contract", ["trace", "ws", "runner"])
def test_schema_is_draft_2020_12(contract):
    Draft202012Validator.check_schema(load_schemas()[contract])


def test_manifest_has_every_fixture_exactly_once():
    declared = [case["file"] for case in MANIFEST]
    actual = [
        str(path.relative_to(FIXTURES))
        for group in ("valid", "invalid")
        for path in (FIXTURES / group).glob("*.json")
    ]
    assert sorted(declared) == sorted(actual)
    assert len(declared) == len(set(declared))
    assert all(case["valid"] == case["file"].startswith("valid/") for case in MANIFEST)


@pytest.mark.parametrize(
    "name",
    [
        "ResourceLimits",
        "SourceLocation",
        "NoneValue",
        "BoolValue",
        "IntValue",
        "FloatValue",
        "StrValue",
        "Ref",
        "Binding",
        "DictEntry",
        "ListObject",
        "TupleObject",
        "SetObject",
        "DictObject",
        "DequeObject",
        "InstanceObject",
        "OpaqueObject",
        "Frame",
        "NormalizedState",
        "StateDelta",
        "Checkpoint",
        "Delta",
        "ConsoleEntry",
        "DiagnosticFrame",
        "Diagnostic",
        "Completion",
        "TraceDocument",
    ],
)
def test_manual_python_dto_fields_match_schema(name):
    dto = getattr(types, name)
    assert set(get_type_hints(dto)) == set(load_schemas()["trace"]["$defs"][name]["required"])
    assert dto.__total__


@pytest.mark.parametrize(
    "name", ["ReasonCode", "LimitReasonCode", "ApiValidationCode", "RunStatus"]
)
def test_manual_python_enums_match_schema(name):
    assert set(get_args(getattr(types, name))) == set(
        load_schemas()["trace"]["$defs"][name]["enum"]
    )


@pytest.mark.parametrize(
    "type_name,value,accepted",
    [
        ("bool", True, True),
        ("bool", False, True),
        ("bool", 1, False),
        ("bool", "true", False),
        ("int", "9007199254740993", True),
        ("int", "-12345678901234567890", True),
        ("int", True, False),
        ("int", 1, False),
        ("int", "01", False),
        ("int", "+1", False),
        ("float", "nan", True),
        ("float", "inf", True),
        ("float", "-inf", True),
        ("float", "-0.0", True),
        ("float", "1e+20", True),
        ("float", "Infinity", False),
        ("float", "NaN", False),
        ("float", 1.0, False),
        ("float", "1e+309", False),
        ("none", None, True),
        ("none", "null", False),
    ],
)
def test_scalar_contract_without_coercion(type_name, value, accepted):
    scalar = {"kind": "scalar", "type": type_name, "value": value}
    if accepted:
        validate_contract(scalar, "trace", "Value")
    else:
        with pytest.raises(ContractValidationError):
            validate_contract(scalar, "trace", "Value")


@pytest.mark.parametrize("value", [None, True, 1, "1", {"$ref": "o1", "value": None}])
def test_none_and_ref_require_exact_encoded_variant(value):
    with pytest.raises(ContractValidationError):
        validate_contract(value, "trace", "Value")


@pytest.mark.parametrize("definition,prefix", [("ObjectId", "o"), ("FrameId", "f")])
def test_identity_patterns(definition, prefix):
    for value in (prefix + "1", prefix + "2", prefix + "12345"):
        validate_contract(value, "trace", definition)
    for value in (prefix + "0", prefix + "01", prefix + "-1", prefix + "1\n"):
        with pytest.raises(ContractValidationError):
            validate_contract(value, "trace", definition)


def test_uuid_is_not_an_object_id():
    validate_contract("8ba106dd-35b6-4945-a5db-e1d3120c743d", "trace", "RunId")
    with pytest.raises(ContractValidationError):
        validate_contract("o1", "trace", "RunId")


def test_defaults_equal_normative_table():
    expected = {
        "active_runs": 1,
        "retained_results": 1,
        "code_bytes": 65536,
        "max_events": 100000,
        "trace_bytes": 52428800,
        "terminal_reserve_bytes": 2097152,
        "registry_objects": 10000,
        "state_objects": 2000,
        "state_bytes": 1048576,
        "collection_items": 1000,
        "instance_attributes": 1000,
        "string_bytes": 16384,
        "integer_decimal_digits": 1000,
        "active_frames": 128,
        "console_bytes": 65536,
        "output_chunk_bytes": 4096,
        "input_value_bytes": 8192,
        "input_requests": 32,
        "checkpoint_interval": 128,
        "active_timeout_ms": 10000,
        "input_idle_timeout_ms": 120000,
        "container_lifetime_ms": 300000,
        "startup_timeout_ms": 15000,
        "result_ttl_ms": 300000,
        "cleanup_command_timeout_ms": 3000,
        "cleanup_attempts": 2,
        "finalization_timeout_ms": 120000,
        "watchdog_tick_ms": 50,
        "memory_bytes": 536870912,
        "memory_plus_swap_bytes": 536870912,
        "cpus": 1.0,
        "pids_limit": 16,
        "tmpfs_bytes": 16777216,
        "runner_message_bytes": 2097152,
        "ws_message_bytes": 524288,
        "native_stderr_bytes": 16384,
        "history_entries": 100,
        "max_visual_nodes": 200,
        "visible_array_items": 200,
    }
    assert default_limits() == expected
    config = default_config()
    assert (config["host"], config["port"], config["python_version"], config["image_ref"]) == (
        "127.0.0.1",
        8000,
        "3.13.16",
        "codetrace-runner:1.0",
    )
    schema = load_schemas()["trace"]["$defs"]["ResourceLimits"]
    assert {key: data["default"] for key, data in schema["properties"].items()} == expected
    config["limits"]["max_events"] = 1
    assert default_limits()["max_events"] == 100000


@pytest.mark.parametrize(
    "filename,validate",
    [
        ("trace-minimal-success.json", validate_trace),
        ("ws-start.json", validate_ws),
        ("runner-start.json", validate_runner),
    ],
)
def test_typed_entry_points_return_original_dto(filename, validate):
    value = json.loads((FIXTURES / "valid" / filename).read_text())
    assert validate(value) is value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), (1, 2), {1: 2}])
def test_reject_non_json_python_values(value):
    with pytest.raises(ContractValidationError):
        validate_contract(value, "trace", "Value")


def test_live_reference_cycles_are_not_wire_cycles():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ContractValidationError):
        validate_contract(cycle, "trace", "Value")


def test_definition_names_are_not_silently_ignored():
    with pytest.raises(ValueError, match="Unknown contract definition"):
        validate_contract({}, "trace", "InventedContract")
