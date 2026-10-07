"""Private per-run storage; explicit conservative startup disposal."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from codetrace_contracts import validate_contract

from ..runs.protocol import encode
from .io import FileOps, owned_io

MAGIC = "CodeTrace temporary result v1"
PREFIX = f"codetrace-result-v1-{os.getuid()}-"
FILES = frozenset(
    {"owner.json", "states.ndjson", "events.ndjson", "console.ndjson", "trace.tmp", "trace.json"}
)


def create_directory(run_id: str, root: Path | None = None) -> Path:
    validate_contract(run_id, "trace", "RunId")
    directory = Path(tempfile.mkdtemp(prefix=PREFIX, dir=root))
    os.chmod(directory, 0o700)
    marker = directory / "owner.json"
    try:
        with marker.open("xb", buffering=0) as file:
            os.chmod(marker, 0o600)
            FileOps().write(
                file, encode({"magic": MAGIC, "uid": os.getuid(), "runId": run_id}), "owner"
            )
        return directory
    except BaseException:
        FileOps().remove_directory(directory)
        raise


def owned_directory(path: Path) -> bool:
    try:
        info = path.lstat()
        if (
            not re.fullmatch(re.escape(PREFIX) + r"[a-z0-9_]{8}", path.name)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            return False
        marker = path / "owner.json"
        info = marker.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 512
        ):
            return False
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            data = json.loads(file.read(513))
        if (
            set(data) != {"magic", "uid", "runId"}
            or data["magic"] != MAGIC
            or data["uid"] != os.getuid()
        ):
            return False
        validate_contract(data["runId"], "trace", "RunId")
        for child in path.iterdir():
            info = child.lstat()
            if (
                child.name not in FILES
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
            ):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


async def cleanup_stale_results(root: Path | None = None) -> int:
    """Call at startup before accepting runs, with exclusive backend ownership.

    Not a scavenger for concurrently running backend processes or arbitrary temp
    trees. Symlinks, unknown layouts/owners/markers are deliberately left alone.
    """
    directory = Path(tempfile.gettempdir()) if root is None else root

    def clean() -> int:
        count = 0
        for path in directory.glob(PREFIX + "*"):
            if owned_directory(path):
                FileOps().remove_directory(path)
                count += 1
        return count

    return await owned_io(clean)
