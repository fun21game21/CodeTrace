import types
from collections import deque

import pytest
from codetrace_runner import ObjectRegistry, ScalarEncoder, ValueEncoder, classify_value, is_scalar


class Plain:
    pass


@pytest.mark.parametrize(
    "value,kind",
    [
        (None, "none"),
        (True, "bool"),
        (1, "int"),
        (1.0, "float"),
        ("a", "str"),
        ([], "list"),
        ((), "tuple"),
        ({}, "dict"),
        (set(), "set"),
        (deque(), "deque"),
        (Plain(), "instance"),
        (object(), "opaque"),
        (b"x", "opaque"),
        (1j, "opaque"),
        (frozenset(), "opaque"),
        (Plain, "opaque"),
        (lambda: None, "opaque"),
        (types, "opaque"),
        (types.SimpleNamespace(), "opaque"),
    ],
)
def test_categories(value, kind):
    assert classify_value(value) == kind
    assert is_scalar(value) is (kind in ("none", "bool", "int", "float", "str"))


@pytest.mark.parametrize("base", [int, float, str, list, tuple, dict, set, deque])
def test_builtin_subclasses_opaque_and_never_encoded_as_exact_scalar(base):
    class Child(base):
        def __repr__(self):
            pytest.fail("User repr called")

        def __str__(self):
            pytest.fail("User str called")

        def __iter__(self):
            pytest.fail("User iterator called")

    obj = Child()
    assert classify_value(obj) == "opaque"
    assert not is_scalar(obj)
    with pytest.raises(TypeError):
        ScalarEncoder().encode(obj)
    assert ValueEncoder(ObjectRegistry()).encode(obj) == {"$ref": "o1"}


def test_dangerous_methods_are_not_called_even_for_unhashable_instances():
    class Dangerous:
        def __eq__(self, other):
            pytest.fail("User equality called")

        def __repr__(self):
            pytest.fail("User repr called")

        def __str__(self):
            pytest.fail("User str called")

        def __iter__(self):
            pytest.fail("User iterator called")

        def __hash__(self):
            pytest.fail("User hash called")

    left, right = Dangerous(), Dangerous()
    registry = ObjectRegistry()
    assert classify_value(left) == "instance"
    assert registry.register(left) == {"$ref": "o1"}
    assert registry.register(left) == {"$ref": "o1"}
    assert registry.register(right) == {"$ref": "o2"}
    assert registry.lookup("o1") is left


def test_unhashable_object_with_raising_equality_is_registered_by_identity():
    class Unhashable:
        def __eq__(self, other):
            pytest.fail("Unhashable object equality called")

    left, right = Unhashable(), Unhashable()
    registry = ObjectRegistry()
    assert registry.register(left) == {"$ref": "o1"}
    assert registry.register(right) == {"$ref": "o2"}
    assert registry.register(left) == {"$ref": "o1"}


def test_property_descriptor_and_inherited_property_are_opaque_without_access():
    calls = []

    class WithProperty:
        @property
        def value(self):
            calls.append("property")
            raise RuntimeError

    class Inherited(WithProperty):
        pass

    class Descriptor:
        def __get__(self, instance, owner):
            calls.append("descriptor")
            raise RuntimeError

    class WithDescriptor:
        field = Descriptor()

    for obj in (WithProperty(), Inherited(), WithDescriptor()):
        assert classify_value(obj) == "opaque"
        assert ObjectRegistry().register(obj) == {"$ref": "o1"}
    assert calls == []


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__", "__setattr__", "__delattr__"])
def test_custom_object_access_is_opaque_without_invoking_hook(hook):
    def dangerous(*args):
        pytest.fail("Custom object access called")

    cls = type("Custom", (), {hook: dangerous})
    obj = object.__new__(cls)
    assert classify_value(obj) == "opaque"
    assert ObjectRegistry().register(obj) == {"$ref": "o1"}


@pytest.mark.parametrize("slots", [(), ("value",), ("__dict__",)])
def test_slots_instances_are_opaque(slots):
    cls = type("Slotted", (), {"__slots__": slots})
    assert classify_value(cls()) == "opaque"


def test_custom_metaclass_hooks_are_never_called():
    class Meta(type):
        def __getattribute__(self, name):
            pytest.fail("Metaclass attribute access called")

        def __hash__(self):
            pytest.fail("Metaclass hash called")

        def __eq__(self, other):
            pytest.fail("Metaclass equality called")

        def __instancecheck__(self, obj):
            pytest.fail("Metaclass instance check called")

    class Custom(metaclass=Meta):
        pass

    obj = Custom()
    assert classify_value(obj) == "opaque"
    assert not is_scalar(obj)
    assert ValueEncoder(ObjectRegistry()).encode(obj) == {"$ref": "o1"}


def test_plain_inheritance_and_ordinary_static_class_methods_are_supported():
    class Parent:
        @staticmethod
        def static():
            pytest.fail("Static method called")

        @classmethod
        def method(cls):
            pytest.fail("Class method called")

    class Child(Parent):
        __getattribute__ = object.__getattribute__
        __setattr__ = object.__setattr__
        __delattr__ = object.__delattr__

    assert classify_value(Child()) == "instance"


def test_overridden_instance_dict_never_evaluated():
    class Custom:
        @property
        def __dict__(self):
            pytest.fail("Instance dict property called")

    assert classify_value(Custom()) == "opaque"


def test_class_changes_are_not_hidden_by_classification_cache():
    class Custom:
        pass

    obj = Custom()
    assert classify_value(obj) == "instance"
    Custom.field = property(lambda self: pytest.fail("Property called"))
    assert classify_value(obj) == "opaque"


def test_instance_attributes_and_container_children_are_not_traversed():
    class Child:
        def __getattribute__(self, name):
            pytest.fail("Child was accessed")

        def __repr__(self):
            pytest.fail("Child repr called")

    child = Child()
    obj = Plain()
    obj.value = child
    recursive = [child]
    recursive.append(recursive)
    registry = ObjectRegistry()
    encoder = ValueEncoder(registry)
    assert classify_value(obj) == "instance"
    assert classify_value(recursive) == "list"
    assert encoder.encode(obj) == {"$ref": "o1"}
    assert encoder.encode(recursive) == {"$ref": "o2"}
    assert registry.count == 2


def test_hostile_class_namespace_key_never_compared():
    class Key(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            pytest.fail("User namespace key equality called")

    # Non-special key lets class creation finish without triggering its comparison.
    cls = type("Custom", (), {Key("unusual"): None})
    assert classify_value(cls()) == "opaque"
