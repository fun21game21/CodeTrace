"""Compact ASCII-escaped JSON for detached DTOs, with fixed lexical field order."""

import json
import math
from typing import cast


def _check_dto(value: object) -> None:
    # Inspect only exact JSON types before json's encoder can observe user subclasses.
    pending: list[tuple[object, bool]] = [(value, False)]
    ancestors: set[int] = set()
    while pending:
        current, leaving = pending.pop()
        if leaving:
            ancestors.remove(id(current))
            continue
        cls = type(current)
        if current is None or cls is str or cls is bool or cls is int:
            continue
        if cls is float:
            if not math.isfinite(cast(float, current)):
                raise ValueError("DTO JSON numbers must be finite")
            continue
        if cls is not list and cls is not dict:
            raise TypeError("Canonical encoding requires exact JSON types")
        identity = id(current)
        if identity in ancestors:
            raise ValueError("DTO must not contain live cycles; use Ref")
        ancestors.add(identity)
        pending.append((current, True))
        if cls is dict:
            mapping = cast(dict[object, object], current)
            for key, child in dict.items(mapping):
                if type(key) is not str:
                    raise TypeError("Canonical encoding requires exact string keys")
                pending.append((child, False))
        else:
            pending.extend((child, False) for child in list.__iter__(cast(list[object], current)))


def canonical_json(value: object) -> str:
    """No formatting spaces, ASCII escaping, sorted fields, no NaN JSON numbers."""
    _check_dto(value)
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def canonical_size(value: object) -> int:
    # ASCII output means characters == UTF-8 bytes, including escaped lone surrogates.
    return len(canonical_json(value))
