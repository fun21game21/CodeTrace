import gc
import weakref
from collections import deque

import pytest
from codetrace_contracts import default_limits, validate_contract
from codetrace_runner import ObjectRegistry, RegistryClosedError, ValueLimitError
from codetrace_runner import registry as registry_module


class Plain:
    pass


@pytest.mark.parametrize("factory", [list, lambda: tuple([1, 2]), dict, set, deque, Plain])
def test_repeated_object_stable_ref_and_lookup(factory):
    obj = factory()
    registry = ObjectRegistry()
    first = registry.register(obj)
    second = registry.get_or_register(obj)
    assert first == second == {"$ref": "o1"}
    assert first is not second
    assert registry.lookup(first["$ref"]) is obj
    assert registry.count == 1
    validate_contract(first, "trace", "Value")


@pytest.mark.parametrize(
    "factory",
    [lambda: [1, 2], lambda: tuple([1, 2]), lambda: {1: 2}, lambda: {1, 2}, lambda: deque([1, 2])],
)
def test_equal_independent_objects_have_different_ids(factory):
    left, right = factory(), factory()
    assert left is not right
    assert left == right
    registry = ObjectRegistry()
    assert registry.register(left) == {"$ref": "o1"}
    assert registry.register(right) == {"$ref": "o2"}
    assert registry.count == 2


def test_mixed_types_have_monotonic_ids_and_return_unmodified_objects():
    registry = ObjectRegistry()
    objects = [[], tuple([1]), {}, set(), deque(), Plain(), b"abc", Plain]
    for index, obj in enumerate(objects, 1):
        ref = registry.register(obj)
        assert ref == {"$ref": f"o{index}"}
        assert registry.lookup(ref["$ref"]) is obj
    assert registry.count == len(objects)


def test_mutation_does_not_change_identity_or_touch_old_refs():
    registry = ObjectRegistry()
    obj = [1]
    ref = registry.register(obj)
    obj[:] = [2, obj]
    assert registry.register(obj) == ref
    assert registry.lookup(ref["$ref"]) is obj
    ref["$ref"] = "o999"
    assert registry.register(obj) == {"$ref": "o1"}


def test_retention_prevents_id_reuse_after_local_reference_disappears():
    registry = ObjectRegistry()
    obj = Plain()
    ref = registry.register(obj)
    observed = weakref.ref(obj)
    del obj
    gc.collect()
    assert observed() is not None
    assert registry.lookup(ref["$ref"]) is observed()
    assert registry.register(Plain()) == {"$ref": "o2"}


def test_independent_runs_restart_at_one_and_do_not_share_objects():
    first, second = ObjectRegistry(), ObjectRegistry()
    left, right = Plain(), Plain()
    assert first.register(left) == second.register(right) == {"$ref": "o1"}
    assert first.lookup("o1") is left
    assert second.lookup("o1") is right
    first.close()
    assert second.lookup("o1") is right


def test_registry_limit_allows_existing_object_but_not_new_identity():
    registry = ObjectRegistry()
    assert default_limits()["registry_objects"] == 10000
    first = []
    registry.register(first)
    for index in range(2, 10001):
        assert registry.register([]) == {"$ref": f"o{index}"}
    assert registry.count == 10000
    with pytest.raises(ValueLimitError) as caught:
        registry.register([])
    assert caught.value.reason == "REGISTRY_LIMIT"
    diagnostic = caught.value.diagnostic
    assert diagnostic["category"] == "limit"
    assert diagnostic["code"] == "REGISTRY_LIMIT"
    assert diagnostic["source"] is None
    validate_contract(diagnostic, "trace", "Diagnostic")
    assert registry.count == 10000
    assert registry.lookup("o1") is first
    assert registry.register(first) == {"$ref": "o1"}


@pytest.mark.parametrize("operation", ["close", "clear"])
def test_terminal_cleanup_releases_strong_references(operation):
    registry = ObjectRegistry()
    obj = Plain()
    observed = weakref.ref(obj)
    registry.register(obj)
    del obj
    assert observed() is not None
    getattr(registry, operation)()
    gc.collect()
    assert observed() is None
    assert registry.count == 0
    assert registry.closed is True
    registry.close()
    registry.clear()
    with pytest.raises(RegistryClosedError):
        registry.register([])
    with pytest.raises(RegistryClosedError):
        registry.get_or_register([])
    with pytest.raises(RegistryClosedError):
        registry.lookup("o1")


@pytest.mark.parametrize("value", [None, True, False, 0, 1.0, ""])
def test_registry_rejects_exact_scalars_without_consuming_id(value):
    registry = ObjectRegistry()
    with pytest.raises(TypeError):
        registry.register(value)
    assert registry.count == 0
    assert registry.register([]) == {"$ref": "o1"}


def test_unknown_id_does_not_register_or_change_counter():
    registry = ObjectRegistry()
    for object_id in ("o1", "o0", "o01", "unknown"):
        with pytest.raises(KeyError):
            registry.lookup(object_id)
    assert registry.count == 0
    assert registry.register([]) == {"$ref": "o1"}


def test_identity_hit_is_confirmed_with_is(monkeypatch):
    registry = ObjectRegistry()
    left, right = [], []
    monkeypatch.setattr(registry_module, "id", lambda obj: 123, raising=False)
    assert registry.register(left) == {"$ref": "o1"}
    with pytest.raises(RuntimeError, match="identity invariant"):
        registry.register(right)
    assert registry.count == 1
    assert registry.lookup("o1") is left


def test_lookup_does_not_hash_user_string_subclass():
    class HostileId(str):
        def __hash__(self):
            pytest.fail("User ObjectId hash called")

    registry = ObjectRegistry()
    registry.register([])
    with pytest.raises(TypeError):
        registry.lookup(HostileId("o1"))


def test_registry_limit_configuration_is_copied():
    limits = default_limits()
    limits["registry_objects"] = 1
    registry = ObjectRegistry(limits)
    limits["registry_objects"] = 10
    registry.register([])
    with pytest.raises(ValueLimitError):
        registry.register([])


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "10", None])
def test_invalid_registry_limit_rejected(invalid):
    limits = default_limits()
    limits["registry_objects"] = invalid
    with pytest.raises(ValueError):
        ObjectRegistry(limits)
