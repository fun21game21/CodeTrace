"""Strong-reference Object Registry scoped to one Run, with stable local IDs."""

from dataclasses import dataclass

from codetrace_contracts.config import default_limits
from codetrace_contracts.types import ObjectId, Ref, ResourceLimits

from .classification import is_scalar
from .errors import RegistryClosedError, ValueLimitError, positive_limit


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _Entry:
    value: object
    object_id: ObjectId


class ObjectRegistry:
    """Retain every non-scalar until close; clear is also terminal, never a reset."""

    def __init__(self, limits: ResourceLimits | None = None) -> None:
        effective = default_limits() if limits is None else limits
        self._capacity = positive_limit(effective["registry_objects"], "registry_objects")
        self._by_identity: dict[int, _Entry] = {}
        self._by_object_id: dict[ObjectId, _Entry] = {}
        self._next_id = 1
        self._closed = False

    @property
    def count(self) -> int:
        return len(self._by_object_id)

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise RegistryClosedError("Object Registry is closed")

    def get_or_register(self, value: object) -> Ref:
        self._require_open()
        if is_scalar(value):
            raise TypeError("Exact scalar values do not belong in Object Registry")
        identity = id(value)
        existing = self._by_identity.get(identity)
        if existing is not None:
            if existing.value is not value:
                raise RuntimeError("Object Registry identity invariant violated")
            return {"$ref": existing.object_id}
        if self.count >= self._capacity:
            raise ValueLimitError("REGISTRY_LIMIT")
        object_id = f"o{self._next_id}"
        entry = _Entry(value=value, object_id=object_id)
        self._by_identity[identity] = entry
        self._by_object_id[object_id] = entry
        self._next_id += 1
        return {"$ref": object_id}

    def register(self, value: object) -> Ref:
        return self.get_or_register(value)

    def lookup(self, object_id: ObjectId) -> object:
        self._require_open()
        if type(object_id) is not str:
            raise TypeError("ObjectId lookup requires an exact string")
        entry = self._by_object_id.get(object_id)
        if entry is None:
            raise KeyError("ObjectId is not registered in this Run")
        return entry.value

    def close(self) -> None:
        self._closed = True
        self._by_identity.clear()
        self._by_object_id.clear()

    def clear(self) -> None:
        self.close()
