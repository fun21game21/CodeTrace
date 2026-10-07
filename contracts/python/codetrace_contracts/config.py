"""Effective v1 defaults; each caller receives its own configuration dict."""

import json
from typing import Literal, TypedDict, cast

from .types import ResourceLimits
from .validators import CONTRACTS_DIRECTORY, validate_contract

SCHEMA_VERSION = "1.0"
API_VERSION = 1
PROTOCOL_VERSION = 1
PYTHON_VERSION = "3.13.16"
SOURCE_FILE_NAME = "<codetrace-user>"
BATCH_TARGET_BYTES = 65536
DIAGNOSTIC_BYTES = 16384
RECORDER_DISK_BYTES = 120 * 1024 * 1024
CONTAINER_UID = 65532
CONTAINER_GID = 65532


class ConfigDefaults(TypedDict):
    host: Literal["127.0.0.1"]
    port: Literal[8000]
    python_version: Literal["3.13.16"]
    image_ref: Literal["codetrace-runner:1.0"]
    limits: ResourceLimits


def default_config() -> ConfigDefaults:
    value = json.loads((CONTRACTS_DIRECTORY / "defaults.json").read_text())
    validate_contract(value["limits"], "trace", "ResourceLimits")
    return cast(ConfigDefaults, value)


def default_limits() -> ResourceLimits:
    return default_config()["limits"]
