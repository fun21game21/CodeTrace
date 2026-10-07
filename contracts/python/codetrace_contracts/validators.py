"""Strict local JSON Schema validation plus record-level contract invariants.

This module validates DTOs, never live Python state. It does not reconstruct deltas,
track a connection, run Python, or resolve schemas over the network.
"""

import json
import math
import re
from collections.abc import Iterable
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .types import RunnerMessage, TraceDocument, WsMessage

Contract = Literal["trace", "ws", "runner"]
_PACKAGED_RESOURCES = Path(__file__).resolve().parent / "resources"
CONTRACTS_DIRECTORY = (
    _PACKAGED_RESOURCES if _PACKAGED_RESOURCES.is_dir() else Path(__file__).resolve().parents[2]
)


class ContractValidationError(ValueError):
    """The input is not a valid CodeTrace v1 DTO."""


def valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(
        r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):"
        r"([0-9]{2})(?:\.[0-9]+)?Z",
        value,
    )
    if match is None:
        return False
    year, month, day, hour, minute, second = map(int, match.groups())
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return (
        1 <= month <= 12
        and 1 <= day <= days[month - 1]
        and hour <= 23
        and minute <= 59
        and second <= 59
    )


@lru_cache(maxsize=1)
def load_schemas() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads((CONTRACTS_DIRECTORY / f"{name}-v1.schema.json").read_text())
        for name in ("trace", "ws", "runner")
    }


@cache
def _validator(contract: Contract, definition: str | None) -> Draft202012Validator:
    schemas = load_schemas()
    schema = schemas[contract]
    if definition is not None and definition not in schema["$defs"]:
        raise ValueError(f"Unknown contract definition: {contract}/{definition}")
    registry: Registry[Any] = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    checker = FormatChecker(["uuid"])
    checker.checks("date-time")(valid_timestamp)
    target = schema if definition is None else {"$ref": schema["$id"] + "#/$defs/" + definition}
    return Draft202012Validator(target, registry=registry, format_checker=checker)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractValidationError(message)


def _check_json(value: object, ancestors: set[int] | None = None) -> None:
    """Reject non-JSON Python inputs and NaN/Infinity, without scalar coercion."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value), "JSON numbers must be finite")
        return
    if type(value) not in (dict, list):
        raise ContractValidationError("Input must contain only JSON types")
    ancestors = set() if ancestors is None else ancestors
    _require(id(value) not in ancestors, "JSON must not contain in-memory cycles; use $ref")
    ancestors.add(id(value))
    children: Iterable[object]
    if isinstance(value, dict):
        _require(all(type(key) is str for key in value), "JSON object keys must be strings")
        children = value.values()
    else:
        children = cast(list[object], value)
    for child in children:
        _check_json(child, ancestors)
    ancestors.remove(id(value))


def _unique(items: list[Any], label: str) -> None:
    _require(len(items) == len(set(items)), f"Duplicate {label}")


def _bindings(bindings: list[dict[str, Any]]) -> None:
    names = [b["name"] for b in bindings]
    _unique(names, "binding names")
    _require(names == sorted(names), "Bindings must be ordered by Unicode code point")


def _refs(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(_refs(item) for item in value)) if value else set()
    if isinstance(value, dict):
        if "$ref" in value:
            return {value["$ref"]}
        return set().union(*(_refs(item) for item in value.values())) if value else set()
    return set()


def _state(state: dict[str, Any]) -> None:
    objects = [o["id"] for o in state["objects"]]
    frames = [f["id"] for f in state["frames"]]
    _unique(objects, "object IDs")
    _unique(frames, "frame IDs")
    _unique(state["stack"], "stack IDs")
    _require(objects == sorted(objects, key=lambda x: (len(x), x)), "Object table ID order")
    _require(frames == sorted(frames, key=lambda x: (len(x), x)), "Frame table ID order")
    _require(set(frames) == set(state["stack"]), "Active frames must match stack")
    _require(_refs(state) <= set(objects), "Unresolved object reference")
    frame_by_id = {f["id"]: f for f in state["frames"]}
    for index, frame_id in enumerate(state["stack"]):
        parent = None if index == 0 else state["stack"][index - 1]
        _require(frame_by_id[frame_id]["parentId"] == parent, "Invalid active frame parent")


def _trace(document: dict[str, Any]) -> None:
    states, events, console = document["states"], document["events"], document["console"]
    interval = document["checkpointInterval"]
    _require(interval == document["limits"]["checkpoint_interval"], "Checkpoint interval mismatch")
    for index, record in enumerate(states):
        _require(record["version"] == index, "State versions must be contiguous from zero")
        expected = "checkpoint" if index % interval == 0 else "delta"
        _require(record["kind"] == expected, "Unexpected checkpoint position")
    _require([e["index"] for e in events] == list(range(len(events))), "Event indices")
    _require([e["index"] for e in console] == list(range(len(console))), "Console indices")
    _require([e["kind"] for e in events].count("RUN_END") == 1, "Exactly one RUN_END required")
    _require(events[-1]["kind"] == "RUN_END", "RUN_END must be last")
    _require(events[-1]["stateVersion"] == len(states) - 1, "RUN_END must use latest state")
    _require(events[-1]["consoleOffset"] == len(console), "RUN_END must include all Console")
    versions = [e["stateVersion"] for e in events]
    offsets = [e["consoleOffset"] for e in events]
    _require(versions == sorted(versions) and max(versions) < len(states), "Event stateVersion")
    _require(offsets == sorted(offsets) and max(offsets) <= len(console), "Event consoleOffset")
    _require(len(events) <= document["limits"]["max_events"], "Event limit")
    if len(events) == 1:
        _require(document["completion"]["stateAccuracy"] == "unavailable", "No observation")
    if document["completion"]["stateAccuracy"] == "unavailable":
        _require(len(events) == 1, "Unavailable requires no accepted observations")


def _semantic(value: Any) -> None:
    if isinstance(value, list):
        for child in value:
            _semantic(child)
        return
    if not isinstance(value, dict):
        return
    if "schemaVersion" in value:
        _trace(value)
    if value.get("kind") == "checkpoint":
        _require(value["version"] == value["state"]["version"], "Checkpoint version mismatch")
    if value.get("kind") == "delta":
        _require(value["baseVersion"] == value["version"] - 1, "Delta baseVersion mismatch")
    if "framesUpsert" in value:
        for prefix in ("frames", "objects"):
            upsert = [r["id"] for r in value[prefix + "Upsert"]]
            remove = value[prefix + "Remove"]
            _unique(upsert, prefix + " upsert IDs")
            _unique(remove, prefix + " remove IDs")
            _require(not set(upsert) & set(remove), "ID appears in upsert and remove")
        _require(
            any(value[k] is not None for k in ("globals", "stack", "eventRoots"))
            or any(
                value[k] for k in ("framesUpsert", "framesRemove", "objectsUpsert", "objectsRemove")
            ),
            "An empty change does not create a Delta",
        )
    if "objects" in value and "eventRoots" in value and "version" in value:
        _state(value)
    for key in ("globals", "locals", "attrs"):
        if isinstance(value.get(key), list):
            _bindings(value[key])
    if value.get("type") == "float" and value.get("kind") == "scalar":
        text = value["value"]
        _require(text in ("nan", "inf", "-inf") or math.isfinite(float(text)), "Float overflow")
    for child in value.values():
        _semantic(child)


def validate_contract(value: object, contract: Contract, definition: str | None = None) -> None:
    """Raise ContractValidationError; leave the input completely unchanged.

    A definition selects an existing schema $defs entry (for unit fixtures).
    Stateful seq/runId/order verification across messages belongs to RUNNER_IO/WATCHDOG/RECORDER.
    """
    _check_json(value)
    validator = _validator(contract, definition)
    error = next(validator.iter_errors(value), None)
    if error is not None:
        path = "/" + "/".join(map(str, error.absolute_path))
        raise ContractValidationError(f"{contract}{path}: {error.message}")
    _semantic(value)


def validate_trace(value: object) -> TraceDocument:
    validate_contract(value, "trace")
    return cast(TraceDocument, value)


def validate_ws(value: object) -> WsMessage:
    validate_contract(value, "ws")
    return cast(WsMessage, value)


def validate_runner(value: object) -> RunnerMessage:
    validate_contract(value, "runner")
    return cast(RunnerMessage, value)
