"""Explicit Docker primitives; importing this package performs no Docker actions."""

from .adapter import DockerAdapter
from .cli import AttachedProcess, DockerCLI
from .errors import (
    CleanupError,
    ContainerConflictError,
    CreateOutcomeError,
    DockerCommandTimeout,
    DockerOperationError,
    DockerOutputError,
    DockerUnavailableError,
    ForeignContainerError,
    ImageUnavailableError,
)
from .types import CleanupResult, ContainerHandle, ContainerInspection, ImageIdentity

__all__ = [
    "AttachedProcess",
    "CleanupError",
    "CleanupResult",
    "ContainerConflictError",
    "ContainerHandle",
    "ContainerInspection",
    "CreateOutcomeError",
    "DockerAdapter",
    "DockerCLI",
    "DockerCommandTimeout",
    "DockerOperationError",
    "DockerOutputError",
    "DockerUnavailableError",
    "ForeignContainerError",
    "ImageIdentity",
    "ImageUnavailableError",
]
