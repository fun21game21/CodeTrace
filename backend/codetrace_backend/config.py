"""Local application paths and explicit origins; production address is fixed."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from codetrace_contracts import default_config, default_limits, validate_contract
from codetrace_contracts.types import ResourceLimits

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8000"
HOST = "127.0.0.1:8000"
DEV_ORIGIN = "http://127.0.0.1:5173"


@dataclass(frozen=True, slots=True)
class AppConfig:
    image_path: Path = ROOT / "backend/local-image.json"
    frontend_dist: Path = ROOT / "frontend/dist"
    temp_root: Path | None = None
    development: bool = False
    limits: ResourceLimits = field(default_factory=default_limits)

    def __post_init__(self) -> None:
        validate_contract(self.limits, "trace", "ResourceLimits")
        object.__setattr__(self, "limits", self.limits.copy())

    @property
    def origins(self) -> frozenset[str]:
        return frozenset((ORIGIN, DEV_ORIGIN)) if self.development else frozenset((ORIGIN,))

    @classmethod
    def from_environment(cls) -> "AppConfig":
        mode = os.environ.get("CODETRACE_MODE", "production")
        if mode not in {"production", "development"}:
            raise ValueError("CODETRACE_MODE must be production or development")
        return cls(
            image_path=Path(
                os.environ.get("CODETRACE_IMAGE_IDENTITY", str(ROOT / "backend/local-image.json"))
            ),
            frontend_dist=Path(
                os.environ.get("CODETRACE_FRONTEND_DIST", str(ROOT / "frontend/dist"))
            ),
            development=mode == "development",
        )


def production_address() -> tuple[str, int]:
    defaults = default_config()
    return defaults["host"], defaults["port"]
