import asyncio
import json
import os
import tempfile

import pytest
from codetrace_backend.trace_recorder import ResultService, TraceRecorder, cleanup_stale_results

from .helpers import RUN_ID


@pytest.mark.parametrize(
    "foreign",
    [
        "name",
        "marker",
        "owner",
        "mode",
        "marker_mode",
        "unknown_file",
        "symlink",
        "child_symlink",
    ],
)
def test_startup_cleanup_only_proven_owned_directories(tmp_path, foreign):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        path = recorder.directory
        for file in recorder._files.values():
            file.close()
        if foreign == "name":
            path = path.rename(tmp_path / "foreign")
        elif foreign in {"marker", "owner"}:
            marker = json.loads((path / "owner.json").read_bytes())
            marker["magic" if foreign == "marker" else "uid"] = "foreign"
            (path / "owner.json").write_text(json.dumps(marker))
        elif foreign == "mode":
            path.chmod(0o755)
        elif foreign == "marker_mode":
            (path / "owner.json").chmod(0o644)
        elif foreign == "unknown_file":
            (path / "foreign.txt").write_text("keep")
        elif foreign == "symlink":
            original = path.rename(tmp_path / "target")
            path.symlink_to(original, target_is_directory=True)
        else:
            (path / "events.ndjson").unlink()
            (path / "events.ndjson").symlink_to(tmp_path / "outside")
        assert await cleanup_stale_results(tmp_path) == 0
        assert path.exists() and (path / "owner.json").exists()

    asyncio.run(run())


def test_stale_spool_and_sealed_results_removed_no_recovery(tmp_path):
    async def run():
        for _ in range(2):
            recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
            for file in recorder._files.values():
                file.close()
            (recorder.directory / "trace.tmp").write_bytes(b"partial")
            (recorder.directory / "trace.json").write_bytes(b"old")
        assert await cleanup_stale_results(tmp_path) == 2
        assert await cleanup_stale_results(tmp_path) == 0
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run())


def test_default_uses_os_private_temp_not_checkout():
    async def run():
        recorder = TraceRecorder(RUN_ID)
        assert recorder.directory.parent == type(recorder.directory)(tempfile.gettempdir())
        assert recorder.directory.stat().st_uid == os.getuid()
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in recorder.directory.iterdir())
        service = ResultService()
        service.register(recorder, "")
        await service.abandon(RUN_ID)
        assert not recorder.directory.exists()

    asyncio.run(run())
