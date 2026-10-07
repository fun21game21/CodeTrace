"""Small internal immutable Docker DTOs and strict identities."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

from .errors import CleanupError

IMAGE_REF = "codetrace-runner:1.0"
_ID = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def run_identity(value: str) -> str:
    if type(value) is not str:
        raise ValueError("RunId must be a UUID string")
    try:
        identifier = str(UUID(value))
    except ValueError:
        raise ValueError("Invalid RunId") from None
    if identifier != value.lower() or len(value) != 36:
        raise ValueError("RunId must use UUID representation")
    return value


def container_id(value: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("Expected a complete Docker container ID")
    return value


@dataclass(frozen=True, slots=True)
class ImageIdentity:
    imageId: str
    imageRef: str = IMAGE_REF

    def __post_init__(self) -> None:
        if self.imageRef != IMAGE_REF or _IMAGE_ID.fullmatch(self.imageId) is None:
            raise ValueError("Invalid local Runner image identity")

    @classmethod
    def load(cls, path: Path) -> ImageIdentity:
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict or set(value) != {"imageRef", "imageId"}:
            raise ValueError("Invalid local image configuration")
        return cls(value["imageId"], value["imageRef"])


@dataclass(frozen=True, slots=True)
class ContainerHandle:
    id: str
    runId: str
    name: str

    def __post_init__(self) -> None:
        container_id(self.id)
        run_identity(self.runId)
        if self.name != f"codetrace-{self.runId}":
            raise ValueError("Unexpected managed container name")


@dataclass(frozen=True, slots=True)
class ContainerInspection:
    id: str
    name: str
    imageId: str
    status: str
    running: bool
    exitCode: int
    oomKilled: bool
    startedAt: str | None
    finishedAt: str | None
    labels: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))


@dataclass(frozen=True, slots=True)
class CleanupResult:
    cleanupConfirmed: bool
    attempts: int
    error: CleanupError | None = None
