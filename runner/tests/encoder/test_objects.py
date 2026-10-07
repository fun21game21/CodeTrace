import pytest
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.graph_types import FrameInput
from codetrace_runner.registry import ObjectRegistry
from codetrace_runner.serializer import GraphSerializer

from .helpers import assert_differential, scalar, sequence, snapshot


def bound_objects(records, *, name="x", ref="o1"):
    return snapshot(globals_=[{"name": name, "value": {"$ref": ref}}], objects=records)


@pytest.mark.parametrize(
    "kind,field,before,after",
    [
        ("list", "items", [scalar(1), scalar(2)], [scalar(1), scalar(3)]),
        ("tuple", "items", [scalar(1)], [scalar(2)]),
        ("set", "items", [scalar(1)], [scalar(1), scalar(2)]),
        ("dict", "entries", [{"key": scalar("k"), "value": scalar(1)}], []),
        ("deque", "items", [scalar(1)], [scalar(2)]),
        ("instance", "attrs", [{"name": "a", "value": scalar(1)}], []),
    ],
)
def test_changed_object_is_full_record_and_binding_can_stay_unchanged(kind, field, before, after):
    extra = (
        {"maxlen": None} if kind == "deque" else {"className": "Node"} if kind == "instance" else {}
    )
    old = {"id": "o1", "kind": kind, field: before, **extra}
    new = {"id": "o1", "kind": kind, field: after, **extra}
    reference = sequence(bound_objects([old]), bound_objects([new]))
    encoded = TraceEncoder().encode(reference)
    changes = encoded.states[2]["changes"]
    assert changes["globals"] is None
    assert changes["objectsUpsert"] == [new]
    assert changes["objectsRemove"] == []
    assert set(changes) == {
        "globals",
        "stack",
        "eventRoots",
        "framesUpsert",
        "framesRemove",
        "objectsUpsert",
        "objectsRemove",
    }
    assert_differential(encoded, reference)


def test_nested_mutation_upserts_only_changed_child():
    outer = {"id": "o1", "kind": "list", "items": [{"$ref": "o2"}]}
    old = {"id": "o2", "kind": "list", "items": [scalar(1)]}
    new = {"id": "o2", "kind": "list", "items": [scalar(1), scalar(2)]}
    reference = sequence(bound_objects([outer, old]), bound_objects([outer, new]))
    encoded = TraceEncoder().encode(reference)
    assert encoded.states[2]["changes"]["objectsUpsert"] == [new]
    assert encoded.states[2]["changes"]["globals"] is None
    assert_differential(encoded, reference)


@pytest.mark.parametrize("retain_alias", [False, True])
def test_rebind_upsert_new_remove_old_only_if_unreachable(retain_alias):
    old = {"id": "o1", "kind": "list", "items": []}
    new = {"id": "o2", "kind": "list", "items": [scalar(2)]}
    before = bound_objects([old])
    after = bound_objects(([old, new] if retain_alias else [new]), ref="o2")
    if retain_alias:
        before["globals"].append({"name": "y", "value": {"$ref": "o1"}})
        after["globals"].append({"name": "y", "value": {"$ref": "o1"}})
    reference = sequence(before, after)
    encoded = TraceEncoder().encode(reference)
    changes = encoded.states[2]["changes"]
    assert changes["objectsUpsert"] == [new]
    assert changes["objectsRemove"] == ([] if retain_alias else ["o1"])
    assert_differential(encoded, reference)  # globals refer to o2 before its upsert is applied.


def test_registry_retention_disappear_and_same_id_reappear():
    registry = ObjectRegistry()
    serializer = GraphSerializer(registry)
    live = [1]
    module = FrameInput("f1", None, "module", "module", "module", 1, None, (), "globals", {})
    try:
        before = serializer.serialize({"x": live}, [module], ["f1"])
        missing = serializer.serialize({}, [module], ["f1"])
        assert registry.count == 1
        live.append(2)
        again = serializer.serialize({"x": live}, [module], ["f1"])
        reference = sequence(before, missing, again)
        encoded = TraceEncoder().encode(reference)
        assert encoded.states[2]["changes"]["objectsRemove"] == ["o1"]
        assert encoded.states[3]["changes"]["objectsUpsert"] == [
            {"id": "o1", "kind": "list", "items": [scalar(1), scalar(2)]}
        ]
        assert_differential(encoded, reference)
    finally:
        registry.close()


@pytest.mark.parametrize("kind", ["list", "dict", "instance"])
def test_self_cycle_and_shared_roots_keep_one_identity(kind):
    ref = {"$ref": "o1"}
    fields = {
        "list": {"items": [ref]},
        "dict": {"entries": [{"key": scalar("self"), "value": ref}]},
        "instance": {"className": "Node", "attrs": [{"name": "next", "value": ref}]},
    }[kind]
    obj = {"id": "o1", "kind": kind, **fields}
    state = bound_objects([obj])
    state["globals"].append({"name": "y", "value": ref})
    reference = sequence(snapshot(), state, state)
    encoded = TraceEncoder().encode(reference)
    assert len(encoded.states[2]["changes"]["objectsUpsert"]) == 1
    assert [e.stateVersion for e in encoded.events] == [1, 2, 2]
    assert_differential(encoded, reference)


def test_mutual_cycle_changed_child_preserves_reference_ids():
    a = {"id": "o1", "kind": "list", "items": [{"$ref": "o2"}]}
    b = {"id": "o2", "kind": "list", "items": [{"$ref": "o1"}]}
    changed_b = {"id": "o2", "kind": "list", "items": [{"$ref": "o1"}, scalar(2)]}
    reference = sequence(bound_objects([a, b]), bound_objects([a, changed_b]))
    encoded = TraceEncoder().encode(reference)
    assert encoded.states[2]["changes"]["objectsUpsert"] == [changed_b]
    assert_differential(encoded, reference)


def test_return_only_object_then_root_removal_removes_unreachable_record():
    obj = {"id": "o1", "kind": "list", "items": []}
    ref = {"$ref": "o1"}
    reference = sequence(snapshot(roots=[ref], objects=[obj]), snapshot())
    encoded = TraceEncoder().encode(reference)
    changes = encoded.states[2]["changes"]
    assert changes["eventRoots"] == [] and changes["objectsRemove"] == ["o1"]
    assert changes["globals"] is None
    assert_differential(encoded, reference)
