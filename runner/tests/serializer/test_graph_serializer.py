import json
import math
import random
from collections import deque
from dataclasses import replace

import pytest
from codetrace_contracts import validate_contract
from codetrace_contracts.validators import CONTRACTS_DIRECTORY
from codetrace_runner import ObjectRegistry, RegistryClosedError
from codetrace_runner.canonical import canonical_json
from codetrace_runner.graph_types import FrameInput
from codetrace_runner.serializer import GraphSerializer


class Node:
    class_default = 123


def scalar(value):
    if value is None:
        kind, text = "none", None
    elif type(value) is bool:
        kind, text = "bool", value
    elif type(value) is int:
        kind, text = "int", str(value)
    elif type(value) is float:
        kind, text = "float", repr(value)
    else:
        kind, text = "str", value
    return {"kind": "scalar", "type": kind, "value": text}


def frame(frame_id="f1", parent=None, kind="function", locals_mapping=None):
    module = kind == "module"
    return FrameInput(
        id=frame_id,
        parentId=parent,
        kind=kind,
        name="<module>" if module else "example",
        qualifiedName="<module>" if module else "outer.example",
        definitionLine=None if module else 2,
        callSite=None if parent is None else {"fileName": "<codetrace-user>", "lineNumber": 3},
        parameterNames=["a", "b"] if kind == "function" else [],
        localsSource="globals" if module else "own",
        live_locals={} if locals_mapping is None else locals_mapping,
    )


def refs(dto):
    if type(dto) is list:
        return set().union(*(refs(v) for v in dto)) if dto else set()
    if type(dto) is dict:
        if "$ref" in dto:
            return {dto["$ref"]}
        return set().union(*(refs(v) for v in dto.values())) if dto else set()
    return set()


def assert_invariants(snapshot):
    assert set(snapshot) == {"globals", "frames", "stack", "objects", "eventRoots"}
    # The test adapter supplies a version only to validate against the existing CONTRACTS schema.
    validate_contract({"version": 1, **snapshot}, "trace", "NormalizedState")
    records = {record["id"]: record for record in snapshot["objects"]}
    assert len(records) == len(snapshot["objects"])
    assert refs(snapshot) <= set(records)
    assert [record["id"] for record in snapshot["objects"]] == sorted(
        records, key=lambda s: int(s[1:])
    )
    frame_ids = [record["id"] for record in snapshot["frames"]]
    assert len(frame_ids) == len(set(frame_ids))
    assert set(frame_ids) == set(snapshot["stack"])
    assert all(
        record["locals"] == [] for record in snapshot["frames"] if record["kind"] == "module"
    )
    reached = set()
    pending = list(refs([snapshot["globals"], snapshot["frames"], snapshot["eventRoots"]]))
    while pending:
        object_id = pending.pop()
        if object_id not in reached:
            reached.add(object_id)
            pending.extend(refs(records[object_id]))
    assert reached == set(records)
    # Round trip rejects live objects and live cycles, including nested non-JSON objects.
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot
    return snapshot


def snapshot(globals_mapping=None, *, registry=None, limits=None, frames=(), stack=(), roots=()):
    registry = ObjectRegistry(limits) if registry is None else registry
    return assert_invariants(
        GraphSerializer(registry, limits).serialize(
            {} if globals_mapping is None else globals_mapping, frames, stack, roots
        )
    )


def object_by_name(state, name):
    object_id = next(b["value"]["$ref"] for b in state["globals"] if b["name"] == name)
    return next(record for record in state["objects"] if record["id"] == object_id)


def test_scalar_roots_locals_and_event_roots():
    values = {"i": 9007199254740993, "s": "Привет", "b": True, "n": None, "f": -0.0}
    state = snapshot(
        values, frames=[frame(locals_mapping=values)], stack=["f1"], roots=[math.nan, None]
    )
    expected = [{"name": name, "value": scalar(values[name])} for name in sorted(values)]
    assert state["globals"] == state["frames"][0]["locals"] == expected
    assert state["eventRoots"] == [scalar(math.nan), scalar(None)]
    assert state["objects"] == []


def test_service_globals_only_are_removed():
    service = (
        "__builtins__",
        "__name__",
        "__file__",
        "__package__",
        "__loader__",
        "__spec__",
        "__cached__",
        "__doc__",
    )
    values = {name: [10**1001] for name in service}
    values.update({"_private": 1, "__proto__": 2, "__custom__": 3, "__name__extra": 4})
    state = snapshot(values)
    assert [b["name"] for b in state["globals"]] == sorted(set(values) - set(service))
    assert state["objects"] == []


@pytest.mark.parametrize("factory,kind", [(list, "list"), (tuple, "tuple"), (deque, "deque")])
def test_ordered_collections_and_aliases(factory, kind):
    child = [7]
    obj = factory([None, True, 1, "a", child, child])
    state = snapshot({"a": obj, "b": obj})
    record = object_by_name(state, "a")
    assert record["kind"] == kind
    assert record["items"][:4] == [scalar(None), scalar(True), scalar(1), scalar("a")]
    assert record["items"][4] == record["items"][5]
    assert len(state["objects"]) == 2
    assert state["globals"][0]["value"] == state["globals"][1]["value"]


@pytest.mark.parametrize("factory", [list, deque])
def test_self_cycle(factory):
    obj = factory()
    obj.append(obj)
    state = snapshot({"a": obj})
    assert state["objects"][0]["items"] == [{"$ref": "o1"}]
    assert len(state["objects"]) == 1


@pytest.mark.parametrize("factories", [(list, list), (deque, list), (deque, deque)])
def test_mutual_collection_cycle(factories):
    left, right = factories[0](), factories[1]()
    left.append(right)
    right.append(left)
    state = snapshot({"a": left})
    assert [r["items"] for r in state["objects"]] == [[{"$ref": "o2"}], [{"$ref": "o1"}]]


def test_tuple_list_cycle_and_mutable_child():
    child = []
    parent = (child,)
    child.append(parent)
    state = snapshot({"a": parent})
    assert state["objects"] == [
        {"id": "o1", "kind": "tuple", "items": [{"$ref": "o2"}]},
        {"id": "o2", "kind": "list", "items": [{"$ref": "o1"}]},
    ]


def test_nested_aliases_and_equal_independent_lists():
    child = [1]
    left, right = [child], [child]
    state = snapshot({"x": left, "y": right, "z": [1]})
    assert object_by_name(state, "x")["items"] == object_by_name(state, "y")["items"]
    assert object_by_name(state, "z")["id"] != object_by_name(state, "x")["items"][0]["$ref"]
    assert len(state["objects"]) == 4


def test_dict_preserves_typed_keys_and_insertion_order():
    obj = {"z": 0, 1: "int", "1": "str", None: None, (2, "a"): "tuple", "a": 5}
    state = snapshot({"d": obj})
    entries = object_by_name(state, "d")["entries"]
    assert [entry["key"] for entry in entries] == [
        scalar("z"),
        scalar(1),
        scalar("1"),
        scalar(None),
        {"$ref": "o2"},
        scalar("a"),
    ]
    assert [entry["value"] for entry in entries] == [scalar(v) for v in obj.values()]
    assert state["objects"][1] == {"id": "o2", "kind": "tuple", "items": [scalar(2), scalar("a")]}


def test_dict_cycle_shared_children_and_object_key():
    child = []
    key = Node()
    obj = {key: child, "other": child}
    obj["self"] = obj
    state = snapshot({"d": obj})
    entries = object_by_name(state, "d")["entries"]
    assert entries[0]["value"] == entries[1]["value"]
    assert entries[2]["value"] == {"$ref": "o1"}
    assert len(state["objects"]) == 3


def test_set_mixed_values_has_expected_canonical_order():
    obj = {None, False, 3, -1, "3", "я", 1.25, math.inf}
    state = snapshot({"s": obj})
    expected = [
        scalar(None),
        scalar(False),
        scalar(3),
        scalar(-1),
        scalar("3"),
        scalar("я"),
        scalar(1.25),
        scalar(math.inf),
    ]
    expected.sort(
        key=lambda v: json.dumps(v, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
    assert object_by_name(state, "s")["items"] == expected


def test_set_members_are_not_compared_or_rehashed_by_serializer():
    armed = False

    class Dangerous:
        def __hash__(self):
            if armed:
                pytest.fail("Serializer hashed a user object")
            return id(self)

        def __eq__(self, other):
            pytest.fail("Serializer compared user objects")

        def __lt__(self, other):
            pytest.fail("Serializer sorted user objects")

    members = {Dangerous(), Dangerous()}
    armed = True
    registry = ObjectRegistry()
    first = snapshot({"s": members}, registry=registry)
    second = snapshot({"s": members}, registry=registry)
    assert first == second
    assert object_by_name(first, "s")["items"] == [{"$ref": "o2"}, {"$ref": "o3"}]


@pytest.mark.parametrize("maxlen", [None, 0, 3, 100])
def test_deque_maxlen(maxlen):
    obj = deque([1, 2], maxlen=maxlen)
    state = snapshot({"d": obj})
    assert object_by_name(state, "d")["maxlen"] == maxlen
    assert object_by_name(state, "d")["items"] == ([] if maxlen == 0 else [scalar(1), scalar(2)])


def test_instance_own_attrs_unicode_order_and_class_defaults_excluded():
    class Parent:
        inherited = 20

    class Child(Parent):
        own_class = 30

    obj = Child()
    for name in ("😀", "я", "z", "_hidden", "\ue000", "a", "__proto__"):
        setattr(obj, name, name)
    record = object_by_name(snapshot({"node": obj}), "node")
    assert record["className"] == "Child"
    assert [b["name"] for b in record["attrs"]] == sorted(obj.__dict__)
    assert {b["name"] for b in record["attrs"]}.isdisjoint({"inherited", "own_class"})


def test_instance_cycles_and_shared_attrs():
    left, right, child = Node(), Node(), []
    left.self = left
    left.next = right
    right.next = left
    left.a = left.b = child
    state = snapshot({"node": left})
    attrs = {b["name"]: b["value"] for b in object_by_name(state, "node")["attrs"]}
    assert attrs["self"] == {"$ref": "o1"}
    assert attrs["a"] == attrs["b"]
    other = next(r for r in state["objects"] if r["id"] == attrs["next"]["$ref"])
    assert other["attrs"] == [{"name": "next", "value": {"$ref": "o1"}}]
    assert len(state["objects"]) == 3


def test_reachability_is_snapshot_local_and_registry_keeps_identity():
    registry = ObjectRegistry()
    obj = [1]
    first = snapshot({"x": obj}, registry=registry)
    assert first["objects"][0]["id"] == "o1"
    assert snapshot(registry=registry)["objects"] == []
    assert registry.count == 1
    assert snapshot(roots=[obj], registry=registry)["eventRoots"] == [{"$ref": "o1"}]
    assert snapshot({"x": obj}, registry=registry) == first


def test_event_roots_preserve_order_alias_and_only_reachable_children():
    obj = [Node()]
    state = snapshot(roots=[obj, obj, 7])
    assert state["eventRoots"] == [{"$ref": "o1"}, {"$ref": "o1"}, scalar(7)]
    assert len(state["objects"]) == 2


def test_all_frames_are_roots_and_module_globals_are_not_duplicated():
    shared, outer_only, inner_only = [], [1], [2]
    module = frame("f1", kind="module", locals_mapping={"ignored": [10**1001]})
    outer = frame("f2", "f1", locals_mapping={"shared": shared, "outer": outer_only})
    inner = frame("f10", "f2", locals_mapping={"shared": shared, "inner": inner_only})
    state = snapshot({"global": shared}, frames=[inner, module, outer], stack=["f1", "f2", "f10"])
    assert [f["id"] for f in state["frames"]] == ["f1", "f2", "f10"]
    assert state["frames"][0]["locals"] == []
    assert len(state["objects"]) == 3
    for f in state["frames"][1:]:
        binding = next(b for b in f["locals"] if b["name"] == "shared")
        assert binding["value"] == state["globals"][0]["value"]


def test_class_frame_has_own_namespace_including_underscore_names():
    state = snapshot(
        frames=[frame(kind="class", locals_mapping={"_x": [1], "__module__": "__main__"})],
        stack=["f1"],
    )
    assert [b["name"] for b in state["frames"][0]["locals"]] == ["__module__", "_x"]


@pytest.mark.parametrize(
    "mutation", ["list", "nested", "instance", "dict", "shared", "deque", "set"]
)
def test_historical_snapshot_has_no_live_mutable_references(mutation):
    child = [1]
    node = Node()
    node.child = child
    obj = {"nested": [child], "node": node, "shared": child, "queue": deque([child]), "set": {1, 2}}
    registry = ObjectRegistry()
    first = snapshot({"x": obj, "alias": obj}, registry=registry)
    saved = json.loads(json.dumps(first))
    if mutation == "list":
        obj["nested"].append(2)
    elif mutation in ("nested", "shared"):
        child.append(3)
    elif mutation == "instance":
        node.child = []
        node.new = "new"
    elif mutation == "dict":
        obj["new"] = 4
        del obj["shared"]
    elif mutation == "deque":
        obj["queue"].append(7)
    else:
        obj["set"].add(3)
    assert first == saved
    assert snapshot({"x": obj, "alias": obj}, registry=registry) != first


def test_input_frame_metadata_is_detached():
    parameters = ["a", "b"]
    site = {"fileName": "<codetrace-user>", "lineNumber": 3}
    caller = frame("f1", kind="module")
    callee = replace(frame("f2", "f1"), parameterNames=parameters, callSite=site)
    stack = ["f1", "f2"]
    state = snapshot(frames=[caller, callee], stack=stack)
    parameters.append("c")
    site["lineNumber"] = 10
    stack.clear()
    assert state["frames"][1]["parameterNames"] == ["a", "b"]
    assert state["frames"][1]["callSite"]["lineNumber"] == 3
    assert state["stack"] == ["f1", "f2"]


def test_determinism_for_bindings_objects_nan_and_same_registry():
    registry = ObjectRegistry()
    nodes = [[], {}, (), Node(), deque(), {"x", 1}]
    globals_mapping = {str(i): value for i, value in reversed(list(enumerate(nodes)))}
    globals_mapping.update({"nan": math.nan, "😀": 1, "\ue000": 2})
    first = snapshot(globals_mapping, registry=registry)
    globals_mapping = dict(reversed(list(globals_mapping.items())))
    second = snapshot(globals_mapping, registry=registry)
    assert first == second
    assert canonical_json(first) == canonical_json(second)


def test_object_table_sorted_numerically_with_existing_registry_mapping():
    registry = ObjectRegistry()
    objects = [[] for _ in range(12)]
    for obj in objects:
        registry.register(obj)
    state = snapshot({"a": objects[-1], "b": objects[1], "c": objects[0]}, registry=registry)
    assert [r["id"] for r in state["objects"]] == ["o1", "o2", "o12"]


def test_deep_graph_does_not_use_recursive_python_traversal():
    obj = []
    for _ in range(1500):
        obj = [obj]
    state = snapshot({"a": obj})
    assert len(state["objects"]) == 1501


@pytest.mark.parametrize("seed", range(30))
def test_seeded_graph_against_independent_index_model(seed):
    rng = random.Random(seed)
    topology = [[rng.randrange(20) for _ in range(rng.randrange(4))] for _ in range(20)]
    nodes = [[] for _ in topology]
    for index, edges in enumerate(topology):
        nodes[index].extend([index, *(nodes[child] for child in edges)])
    registry = ObjectRegistry()
    order = list(range(20))
    rng.shuffle(order)
    identity = {index: registry.register(nodes[index])["$ref"] for index in order}
    roots = [rng.randrange(20) for _ in range(3)]
    reached = set(roots)
    frontier = list(roots)
    while frontier:
        for child in topology[frontier.pop()]:
            if child not in reached:
                reached.add(child)
                frontier.append(child)
    expected = [
        {
            "id": identity[i],
            "kind": "list",
            "items": [scalar(i), *({"$ref": identity[child]} for child in topology[i])],
        }
        for i in reached
    ]
    expected.sort(key=lambda r: int(r["id"][1:]))
    state = snapshot(roots=[nodes[index] for index in roots], registry=registry)
    assert state["objects"] == expected
    assert state["eventRoots"] == [{"$ref": identity[index]} for index in roots]


@pytest.mark.parametrize("kind", ["list", "dict", "set", "deque", "instance", "opaque"])
def test_existing_contracts_object_fixtures_are_emitted_without_contract_changes(kind):
    if kind == "list":
        obj = []
        obj.append(obj)
        filename = "object-list-cycle.json"
    elif kind == "dict":
        obj, filename = {9007199254740993: "\ud800😀 __proto__"}, "object-dict.json"
    elif kind == "set":
        obj, filename = {9007199254740993}, "object-set.json"
    elif kind == "deque":
        obj, filename = deque([9007199254740993]), "object-deque.json"
    elif kind == "instance":
        obj, filename = Node(), "object-instance.json"
        obj.left = obj
        obj.value = 9007199254740993
    else:
        obj, filename = b"data", "object-opaque.json"
    fixture = json.loads((CONTRACTS_DIRECTORY / "fixtures/valid" / filename).read_text())
    assert snapshot({"a": obj})["objects"][0] == fixture


def test_closed_registry_rejected_even_for_empty_snapshot():
    registry = ObjectRegistry()
    serializer = GraphSerializer(registry)
    registry.close()
    with pytest.raises(RegistryClosedError):
        serializer.serialize({})
