"""Classify values without traversing objects or invoking user access hooks."""

from collections import deque
from types import FunctionType, GetSetDescriptorType, MappingProxyType, NoneType
from typing import Any, Literal, cast

ValueKind = Literal[
    "none",
    "bool",
    "int",
    "float",
    "str",
    "list",
    "tuple",
    "dict",
    "set",
    "deque",
    "instance",
    "opaque",
]

# CPython Py_TPFLAGS_HEAPTYPE; Runner is pinned to CPython by the specification.
_HEAPTYPE = 1 << 9


def is_scalar(value: object) -> bool:
    cls = type(value)
    # Do not use class equality, hashing, isinstance, or an object's __class__.
    return cls is NoneType or cls is bool or cls is int or cls is float or cls is str


def _namespace(cls: type) -> MappingProxyType[str, Any]:
    return cast(MappingProxyType[str, Any], type.__getattribute__(cls, "__dict__"))


def _mro(cls: type) -> tuple[type, ...]:
    return cast(tuple[type, ...], type.__getattribute__(cls, "__mro__"))


def _has_descriptor_hooks(value: object) -> bool:
    cls = type(value)
    if type(cls) is not type:
        return True
    for base in _mro(cls):
        # Iterate the raw namespace; lookup can compare a hostile str subclass key.
        for name in _namespace(base):
            if type(name) is not str:
                return True
            if name == "__get__" or name == "__set__" or name == "__delete__":
                return True
    return False


def _plain_instance_type(cls: type) -> bool:
    if type(cls) is not type:
        return False
    has_instance_dict = False
    for base in _mro(cls):
        if base is object:
            continue
        # Exact builtins were handled by classify_value; their subclasses are opaque.
        if base is deque or not (type.__getattribute__(base, "__flags__") & _HEAPTYPE):
            return False
        for name, attribute in _namespace(base).items():
            if type(name) is not str:
                return False
            if name == "__slots__" or name == "__getattr__":
                return False
            if name == "__getattribute__":
                if attribute is not object.__getattribute__:
                    return False
                continue
            if name == "__setattr__":
                if attribute is not object.__setattr__:
                    return False
                continue
            if name == "__delattr__":
                if attribute is not object.__delattr__:
                    return False
                continue
            attr_type = type(attribute)
            if name == "__dict__" or name == "__weakref__":
                if attr_type is not GetSetDescriptorType:
                    return False
                # Permit only the intrinsic descriptors generated for this class.
                if object.__getattribute__(attribute, "__objclass__") is not base:
                    return False
                if name == "__dict__":
                    has_instance_dict = True
                continue
            # Ordinary methods may have dangerous bodies; observing them never calls them.
            if attr_type is FunctionType or attr_type is staticmethod or attr_type is classmethod:
                continue
            if _has_descriptor_hooks(attribute):
                return False
    return has_instance_dict


def classify_value(value: object) -> ValueKind:
    """Inspect type metadata only; never read instance attributes or container items."""
    cls = type(value)
    if cls is NoneType:
        return "none"
    if cls is bool:
        return "bool"
    if cls is int:
        return "int"
    if cls is float:
        return "float"
    if cls is str:
        return "str"
    if cls is list:
        return "list"
    if cls is tuple:
        return "tuple"
    if cls is dict:
        return "dict"
    if cls is set:
        return "set"
    if cls is deque:
        return "deque"
    return "instance" if _plain_instance_type(cls) else "opaque"
