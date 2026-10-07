import json
from collections import deque
from dataclasses import replace

import pytest
from codetrace_contracts import default_limits, validate_contract
from codetrace_runner import ObjectRegistry, ValueLimitError
from codetrace_runner.canonical import canonical_size
from codetrace_runner.graph_errors import GraphLimitError
from codetrace_runner.serializer import GraphSerializer

from .test_graph_serializer import Node, frame, snapshot


def assert_limit(caught, code):
    assert caught.value.reason == code
    assert caught.value.diagnostic["category"] == "limit"
    assert caught.value.diagnostic["code"] == code
    validate_contract(caught.value.diagnostic, "trace", "Diagnostic")


@pytest.mark.parametrize("factory", [list, tuple, lambda x: dict.fromkeys(x, None), set, deque])
def test_default_collection_limit_exact_and_plus_one(factory):
    assert default_limits()["collection_items"] == 1000
    good = factory(range(1000))
    record = snapshot({"a": good})["objects"][0]
    assert len(record.get("items", record.get("entries"))) == 1000
    with pytest.raises(GraphLimitError) as caught:
        snapshot({"a": factory(range(1001))})
    assert_limit(caught, "COLLECTION_LIMIT")


def test_default_instance_attribute_limit_exact_and_plus_one():
    obj = Node()
    obj.__dict__.update({f"attr{i}": i for i in range(1000)})
    record = snapshot({"a": obj})["objects"][0]
    assert len(record["attrs"]) == 1000
    obj.extra = 0
    with pytest.raises(GraphLimitError) as caught:
        snapshot({"a": obj})
    assert_limit(caught, "COLLECTION_LIMIT")
    assert "attribute" in caught.value.diagnostic["message"]


def test_default_state_object_limit_exact_and_plus_one():
    leaves = [[] for _ in range(1995)]
    root = [leaves[i : i + 500] for i in range(0, len(leaves), 500)]
    registry = ObjectRegistry()
    assert len(snapshot({"a": root}, registry=registry)["objects"]) == 2000
    root[-1].append([])
    with pytest.raises(GraphLimitError) as caught:
        snapshot({"a": root}, registry=registry)
    assert_limit(caught, "COLLECTION_LIMIT")
    assert "reachable state object" in caught.value.diagnostic["message"]


@pytest.mark.parametrize("roots_location", ["globals", "locals", "event"])
def test_state_object_limit_counts_all_roots_and_counts_alias_once(roots_location):
    limits = default_limits()
    limits["state_objects"] = 1
    obj = []
    kwargs = {"limits": limits}
    if roots_location == "globals":
        kwargs["globals_mapping"] = {"a": obj, "b": obj}
    elif roots_location == "locals":
        kwargs.update(frames=[frame(locals_mapping={"a": obj, "b": obj})], stack=["f1"])
    else:
        kwargs["roots"] = [obj, obj]
    assert len(snapshot(**kwargs)["objects"]) == 1
    obj.append([])
    with pytest.raises(GraphLimitError) as caught:
        snapshot(**kwargs)
    assert_limit(caught, "COLLECTION_LIMIT")


def test_state_bytes_exact_default_boundary_and_one_byte_over():
    limits = default_limits()
    assert limits["state_bytes"] == 1048576
    payload = {f"s{i}": "a" * 16384 for i in range(63)}
    payload["tail"] = ""
    generous = limits.copy()
    generous["state_bytes"] = 2 * limits["state_bytes"]
    base = snapshot(payload, limits=generous)
    base_size = len(
        json.dumps(base, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    padding = limits["state_bytes"] - base_size
    assert 0 < padding < limits["string_bytes"]
    payload["tail"] = "a" * padding
    state = snapshot(payload)
    assert canonical_size(state) == limits["state_bytes"]
    payload["tail"] += "a"
    with pytest.raises(GraphLimitError) as caught:
        snapshot(payload)
    assert_limit(caught, "STATE_BYTES_LIMIT")


@pytest.mark.parametrize("text", ["я", "😀", "\ud800", "\n", '"', "\\", "\x00"])
def test_state_bytes_counts_json_escaping_not_raw_string_bytes(text):
    value = {"a": text * 10}
    base = snapshot(value)
    measured = len(
        json.dumps(base, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    limits = default_limits()
    limits["state_bytes"] = measured
    assert snapshot(value, limits=limits) == base
    limits["state_bytes"] -= 1
    with pytest.raises(GraphLimitError) as caught:
        snapshot(value, limits=limits)
    assert_limit(caught, "STATE_BYTES_LIMIT")


def test_empty_snapshot_size_boundary():
    size = canonical_size(snapshot())
    limits = default_limits()
    limits["state_bytes"] = size
    assert snapshot(limits=limits)["objects"] == []
    limits["state_bytes"] -= 1
    with pytest.raises(GraphLimitError) as caught:
        snapshot(limits=limits)
    assert_limit(caught, "STATE_BYTES_LIMIT")


def test_size_limit_aborts_before_serializing_huge_remaining_graph():
    limits = default_limits()
    limits["state_bytes"] = 200
    registry = ObjectRegistry(limits)
    root = ["a" * 500, *([] for _ in range(999))]
    with pytest.raises(GraphLimitError) as caught:
        snapshot({"a": root}, limits=limits, registry=registry)
    assert_limit(caught, "STATE_BYTES_LIMIT")
    assert registry.count == 1


def test_registry_limit_remains_per_run_independently_of_state_reachability():
    registry = ObjectRegistry()
    objects = [[] for _ in range(10000)]
    for obj in objects:
        registry.register(obj)
    assert len(snapshot({"a": objects[0]}, registry=registry)["objects"]) == 1
    with pytest.raises(ValueLimitError) as caught:
        snapshot({"new": []}, registry=registry)
    assert_limit(caught, "REGISTRY_LIMIT")
    assert registry.count == 10000


@pytest.mark.parametrize(
    "value,code", [("a" * 16385, "STRING_LIMIT"), (10**1000, "INT_DIGITS_LIMIT")]
)
@pytest.mark.parametrize(
    "location", ["global", "list", "dict_key", "dict_value", "attr", "event", "local"]
)
def test_values_value_limits_apply_everywhere(value, code, location):
    obj = Node()
    obj.value = value
    args = {"globals_mapping": {"a": value}}
    if location == "list":
        args["globals_mapping"] = {"a": [value]}
    elif location == "dict_key":
        args["globals_mapping"] = {"a": {value: None}}
    elif location == "dict_value":
        args["globals_mapping"] = {"a": {1: value}}
    elif location == "attr":
        args["globals_mapping"] = {"a": obj}
    elif location == "event":
        args = {"roots": [value]}
    elif location == "local":
        args = {"frames": [frame(locals_mapping={"a": value})], "stack": ["f1"]}
    with pytest.raises(ValueLimitError) as caught:
        snapshot(**args)
    assert_limit(caught, code)


@pytest.mark.parametrize(
    "location", ["binding", "attribute", "class_name", "frame_name", "parameter"]
)
def test_string_limit_also_bounds_names_and_metadata(location):
    limits = default_limits()
    limits["string_bytes"] = 8
    args = {"limits": limits}
    if location == "binding":
        args["globals_mapping"] = {"a" * 9: 1}
    elif location == "attribute":
        obj = Node()
        setattr(obj, "a" * 9, 1)
        args["globals_mapping"] = {"a": obj}
    elif location == "class_name":
        args["globals_mapping"] = {"a": type("a" * 9, (), {})()}
    else:
        source = replace(frame(), name="short", qualifiedName="short", parameterNames=[])
        source = replace(
            source,
            **({"name": "a" * 9} if location == "frame_name" else {"parameterNames": ["a" * 9]}),
        )
        args.update(frames=[source], stack=["f1"])
    with pytest.raises(ValueLimitError) as caught:
        snapshot(**args)
    assert_limit(caught, "STRING_LIMIT")


def test_frame_limit_exact_and_plus_one():
    limits = default_limits()
    limits["active_frames"] = 2
    frames = [frame("f1"), frame("f2", "f1")]
    assert len(snapshot(frames=frames, stack=["f1", "f2"], limits=limits)["frames"]) == 2
    frames.append(frame("f3", "f2"))
    with pytest.raises(GraphLimitError) as caught:
        snapshot(frames=frames, stack=["f1", "f2", "f3"], limits=limits)
    assert_limit(caught, "FRAME_LIMIT")


@pytest.mark.parametrize(
    "key",
    ["state_objects", "state_bytes", "collection_items", "instance_attributes", "active_frames"],
)
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10", None])
def test_invalid_graph_limit_rejected(key, invalid):
    limits = default_limits()
    limits[key] = invalid
    with pytest.raises(ValueError):
        GraphSerializer(ObjectRegistry(), limits)


@pytest.mark.parametrize(
    "key",
    ["state_objects", "state_bytes", "collection_items", "instance_attributes", "active_frames"],
)
def test_effective_limits_are_copied(key):
    limits = default_limits()
    limits[key] = 1
    serializer = GraphSerializer(ObjectRegistry(), limits)
    limits[key] = 1000000
    obj = Node()
    obj.a, obj.b = 1, 2
    roots = {
        "state_objects": {"a": [[], []]},
        "state_bytes": {},
        "collection_items": {"a": [1, 2]},
        "instance_attributes": {"a": obj},
    }
    kwargs = {"user_globals": roots.get(key, {})}
    if key == "active_frames":
        kwargs.update(frames=[frame("f1"), frame("f2", "f1")], stack=["f1", "f2"])
    with pytest.raises(GraphLimitError):
        serializer.serialize(**kwargs)


def test_failed_snapshot_does_not_mutate_prior_snapshot_or_reset_registry():
    limits = default_limits()
    limits["collection_items"] = 1
    registry = ObjectRegistry()
    serializer = GraphSerializer(registry, limits)
    obj = [1]
    first = serializer.serialize({"a": obj})
    obj.append(2)
    with pytest.raises(GraphLimitError):
        serializer.serialize({"a": obj})
    assert first["objects"][0]["items"] == [{"kind": "scalar", "type": "int", "value": "1"}]
    assert registry.register(obj) == {"$ref": "o1"}
