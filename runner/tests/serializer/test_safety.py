import types
from collections import deque
from dataclasses import replace

import pytest
from codetrace_runner import ObjectRegistry
from codetrace_runner.canonical import canonical_json, canonical_size
from codetrace_runner.serializer import GraphSerializer

from .test_graph_serializer import Node, frame, snapshot


@pytest.mark.parametrize("base", [list, tuple, dict, set, deque, int, float, str])
def test_builtin_subclasses_never_run_iter_len_format_or_compare(base):
    def forbidden(*args):
        pytest.fail("Serializer invoked a user container hook")

    cls = type(
        "Hostile",
        (base,),
        {
            name: forbidden
            for name in (
                "__iter__",
                "__len__",
                "__getitem__",
                "__repr__",
                "__str__",
                "__eq__",
                "__lt__",
            )
        },
    )
    obj = cls()
    state = snapshot({"a": obj, "b": obj})
    assert state["objects"] == [
        {"id": "o1", "kind": "opaque", "pythonType": "Hostile", "reason": "unsupported_class"}
    ]
    assert state["globals"][0]["value"] == state["globals"][1]["value"]


def test_simple_instance_raising_repr_str_eq_lt_iter_len_hash_are_never_called():
    def forbidden(*args):
        pytest.fail("Serializer invoked a user instance hook")

    cls = type(
        "Hostile",
        (),
        {
            name: forbidden
            for name in (
                "__repr__",
                "__str__",
                "__eq__",
                "__lt__",
                "__iter__",
                "__len__",
                "__hash__",
            )
        },
    )
    obj = cls()
    obj.value = 7
    state = snapshot({"a": obj})
    assert state["objects"][0]["kind"] == "instance"
    assert state["objects"][0]["attrs"] == [
        {"name": "value", "value": {"kind": "scalar", "type": "int", "value": "7"}}
    ]


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__", "__setattr__", "__delattr__"])
def test_custom_access_is_opaque_and_never_runs(hook):
    calls = []

    def forbidden(*args):
        calls.append(hook)
        raise RuntimeError("User hook called")

    cls = type("CustomAccess", (), {hook: forbidden})
    obj = object.__new__(cls)
    state = snapshot({"a": obj})
    assert state["objects"][0]["kind"] == "opaque"
    assert calls == []


@pytest.mark.parametrize("field", ["computed", "__dict__"])
def test_property_and_inherited_property_never_run(field):
    calls = []

    def forbidden(obj):
        calls.append(field)
        raise RuntimeError

    parent = type("Parent", (), {field: property(forbidden)})
    child = type("Child", (parent,), {})
    assert snapshot({"a": child()})["objects"][0]["kind"] == "opaque"
    assert calls == []


def test_descriptor_never_run():
    class Descriptor:
        def __get__(self, instance, owner):
            pytest.fail("Descriptor invoked")

    class Custom:
        value = Descriptor()

    assert snapshot({"a": Custom()})["objects"][0]["kind"] == "opaque"


@pytest.mark.parametrize("slots", [(), ("value",), ("__dict__",)])
def test_slots_are_opaque_without_reading_slots(slots):
    cls = type("Slotted", (), {"__slots__": slots})
    record = snapshot({"a": cls()})["objects"][0]
    assert record["kind"] == "opaque"
    assert record["reason"] == "unsupported_class"


def test_custom_metaclass_does_not_run_caption_or_classification_hooks():
    class Meta(type):
        def __getattribute__(self, name):
            pytest.fail("Metaclass access called")

        def __hash__(self):
            pytest.fail("Metaclass hash called")

        def __eq__(self, other):
            pytest.fail("Metaclass equality called")

    class Custom(metaclass=Meta):
        pass

    obj = Custom()
    state = snapshot({"a": obj, "class": Custom})
    assert state["objects"][0] == {
        "id": "o1",
        "kind": "opaque",
        "pythonType": "opaque",
        "reason": "unsupported_class",
    }
    assert len(state["objects"]) == 2


@pytest.mark.parametrize(
    "value,caption,reason",
    [
        (lambda: None, "function", "unsupported_type"),
        (types, "module", "unsupported_type"),
        (Node, "type", "builtin_symbol"),
        (len, "builtin_function_or_method", "builtin_symbol"),
        (b"a", "bytes", "unsupported_type"),
        (1j, "complex", "unsupported_type"),
        (object(), "object", "unsupported_type"),
        (frozenset([1]), "frozenset", "unsupported_type"),
    ],
)
def test_opaque_symbols_and_types(value, caption, reason):
    assert snapshot({"a": value})["objects"] == [
        {"id": "o1", "kind": "opaque", "pythonType": caption, "reason": reason}
    ]


def test_opaque_does_not_traverse_internal_state():
    class WithProperty:
        @property
        def computed(self):
            pytest.fail("Property invoked")

    obj = WithProperty()
    obj.hidden = [10**1001]
    registry = ObjectRegistry()
    state = snapshot({"a": obj}, registry=registry)
    assert len(state["objects"]) == registry.count == 1


def test_unsafe_instance_dict_subclass_is_opaque_without_hooks():
    class Unsafe(dict):
        def __iter__(self):
            pytest.fail("Instance mapping iterator invoked")

        def items(self):
            pytest.fail("Instance mapping items invoked")

        def __len__(self):
            pytest.fail("Instance mapping length invoked")

    obj = Node()
    obj.__dict__ = Unsafe(hidden=[10**1001])
    assert snapshot({"a": obj})["objects"][0]["kind"] == "opaque"


def test_non_string_and_hostile_instance_dict_keys_are_opaque_without_comparison():
    class HostileKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            pytest.fail("Hostile attribute key compared")

    for key in (1, HostileKey("value")):
        obj = Node()
        obj.__dict__ = {key: [10**1001]}
        assert snapshot({"a": obj})["objects"][0]["kind"] == "opaque"


def test_class_mutation_is_observed_without_changing_prior_snapshot():
    class Custom:
        pass

    obj = Custom()
    obj.value = []
    registry = ObjectRegistry()
    first = snapshot({"a": obj}, registry=registry)
    Custom.computed = property(lambda self: pytest.fail("Property invoked"))
    second = snapshot({"a": obj}, registry=registry)
    assert len(first["objects"]) == 2
    assert first["objects"][0]["kind"] == "instance"
    assert len(second["objects"]) == 1
    assert second["objects"][0]["id"] == first["objects"][0]["id"]
    assert second["objects"][0]["kind"] == "opaque"


@pytest.mark.parametrize("base", [dict, list, tuple])
def test_internal_input_subclasses_are_rejected_without_hooks(base):
    def forbidden(*args):
        pytest.fail("Internal input user hook called")

    cls = type(
        "HostileInput",
        (base,),
        {name: forbidden for name in ("__iter__", "__len__", "items", "__getitem__")},
    )
    serializer = GraphSerializer(ObjectRegistry())
    with pytest.raises(TypeError):
        if base is dict:
            serializer.serialize(cls())
        else:
            serializer.serialize({}, event_roots=cls())


@pytest.mark.parametrize("field", ["name", "id", "qualifiedName", "kind", "localsSource"])
def test_frame_metadata_string_subclasses_rejected_without_hooks(field):
    class Hostile(str):
        def __str__(self):
            pytest.fail("Frame string formatted")

        def __eq__(self, other):
            pytest.fail("Frame string compared")

    source = replace(frame(), **{field: Hostile("x")})
    with pytest.raises(TypeError):
        snapshot(frames=[source], stack=["f1"])


def test_real_frame_objects_are_not_accepted_as_frame_input():
    import sys

    with pytest.raises(TypeError):
        GraphSerializer(ObjectRegistry()).serialize({}, frames=[sys._getframe()])


@pytest.mark.parametrize(
    "variant",
    ["duplicate_frame", "duplicate_stack", "missing_frame", "orphan_frame", "wrong_parent"],
)
def test_frame_and_stack_invariants_are_enforced(variant):
    sources = [frame("f1"), frame("f2", "f1")]
    stack = ["f1", "f2"]
    if variant == "duplicate_frame":
        sources.append(sources[0])
    elif variant == "duplicate_stack":
        stack.append("f1")
    elif variant == "missing_frame":
        stack.append("f3")
    elif variant == "orphan_frame":
        stack.pop()
    else:
        sources[1] = replace(sources[1], parentId=None)
    with pytest.raises(ValueError):
        snapshot(frames=sources, stack=stack)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "f0"},
        {"parentId": "f01"},
        {"kind": "other"},
        {"definitionLine": 0},
        {"definitionLine": True},
        {"localsSource": "globals"},
        {"callSite": {"fileName": "other", "lineNumber": 1}},
        {"callSite": {"fileName": "<codetrace-user>", "lineNumber": False}},
        {"callSite": {"fileName": "<codetrace-user>", "lineNumber": 1, "extra": None}},
    ],
)
def test_invalid_frame_adapter_data_rejected(changes):
    with pytest.raises(ValueError):
        snapshot(frames=[replace(frame(), **changes)], stack=["f1"])


def test_root_names_are_exact_strings_before_filter_hash_or_sort():
    class Hostile(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            pytest.fail("Root name compared")

        def __lt__(self, other):
            pytest.fail("Root name sorted")

    with pytest.raises(TypeError):
        snapshot({Hostile("unusual"): 1})


@pytest.mark.parametrize("bad", [object(), b"a", (), {1: "a"}, float("nan")])
def test_canonical_helper_rejects_non_json_input(bad):
    with pytest.raises((TypeError, ValueError)):
        canonical_json(bad)


def test_canonical_helper_does_not_run_user_hooks_or_string_key_comparisons():
    def forbidden(*args):
        pytest.fail("Canonical encoding called user code")

    for base in (list, dict, int, float, str):
        cls = type(
            "Hostile",
            (base,),
            {name: forbidden for name in ("__iter__", "__len__", "__str__", "__repr__")},
        )
        with pytest.raises(TypeError):
            canonical_json(cls())

    class Key(str):
        __hash__ = str.__hash__
        __lt__ = forbidden
        __eq__ = forbidden

    with pytest.raises(TypeError):
        canonical_json({Key("unusual"): 1})


def test_canonical_encoding_is_ascii_compact_stable_and_does_not_mutate_dto():
    left = {"z": "\ud800😀", "a": [True, None, 3, 1.5]}
    right = {"a": [True, None, 3, 1.5], "z": "\ud800😀"}
    expected = '{"a":[true,null,3,1.5],"z":"\\ud800\\ud83d\\ude00"}'
    assert canonical_json(left) == canonical_json(right) == expected
    assert canonical_size(left) == len(expected.encode("ascii"))
    assert list(left) == ["z", "a"]


def test_canonical_helper_rejects_live_cycles_but_accepts_shared_dto_lists():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError):
        canonical_json(cycle)
    shared = [1]
    assert canonical_json([shared, shared]) == "[[1],[1]]"
