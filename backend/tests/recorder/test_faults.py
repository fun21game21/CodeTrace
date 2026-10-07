import asyncio

import pytest
from codetrace_backend.trace_recorder import RecorderError, TraceRecorder

from .helpers import RUN_ID, FaultOps, document, entry, metadata, records, terminal, wait_thread


@pytest.mark.parametrize(
    "stage,occurrence",
    [
        ("commit.states", 1),
        ("commit.states", 2),
        ("commit.events", 1),
        ("commit.events", 2),
        ("commit.console", 1),
    ],
)
def test_partial_batch_fault_preserves_previous_prefix(tmp_path, stage, occurrence):
    async def run():
        ops = FaultOps()
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path, file_ops=ops)
        await recorder.commit_chunk(records()[:1])
        before = {p.name: p.read_bytes() for p in recorder.directory.glob("*.ndjson")}
        counters = recorder.counters
        ops.stage, ops.occurrence = stage, occurrence
        with pytest.raises(OSError):
            if stage == "commit.console":
                await recorder.commit_console(entry())
            else:
                await recorder.commit_chunk(records()[1:])
        assert recorder.counters == counters
        assert {p.name: p.read_bytes() for p in recorder.directory.glob("*.ndjson")} == before
        ops.stage = None
        await recorder.commit_chunk(records()[1:])
        sealed = await recorder.seal(metadata(), terminal(), deadline=1e30)
        document(sealed.path)
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["commit.states", "commit.events", "commit.console"])
@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_cancel_during_io_worker_cannot_escape_rollback(tmp_path, stage, repeat_cancel):
    async def run():
        ops = FaultOps()
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path, file_ops=ops)
        await recorder.commit_chunk(records()[:1])
        counters = recorder.counters
        ops.stage, ops.gate = stage, True
        operation = (
            recorder.commit_console(entry())
            if stage == "commit.console"
            else recorder.commit_chunk(records()[1:])
        )
        task = asyncio.create_task(operation)
        await wait_thread(ops.started)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        if repeat_cancel:
            task.cancel()
        ops.proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert recorder.counters == counters
        assert (recorder.directory / "events.ndjson").stat().st_size == 0
        assert (recorder.directory / "console.ndjson").stat().st_size == 0
        ops.stage = None
        await recorder.commit_chunk(records()[1:])
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "stage",
    [
        "seal.header",
        "seal.array",
        "seal.record",
        "seal.run_end",
        "seal.completion",
        "fsync",
        "replace",
        "corrupt",
    ],
)
def test_sealing_fault_no_public_file(tmp_path, stage):
    async def run():
        ops = FaultOps(stage)
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path, file_ops=ops)
        await recorder.commit_chunk(records())
        with pytest.raises((OSError, RecorderError)):
            await recorder.seal(metadata(), terminal(), deadline=1e30)
        assert not (recorder.directory / "trace.json").exists()
        assert not (recorder.directory / "trace.tmp").exists()
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("name", ["states", "events", "console"])
def test_incomplete_uncommitted_tail_ignored(tmp_path, name):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        path = recorder.directory / (name + ".ndjson")
        with path.open("ab") as file:
            file.write(b'{"broken":')
        sealed = await recorder.seal(metadata(), terminal(), deadline=1e30)
        d = document(sealed.path)
        assert len(d["states"]) == len(d["events"]) == 3
        assert d["console"] == []
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("corruption", ["malformed", "valid_change", "truncated", "duplicate_key"])
def test_committed_corruption_not_repaired(tmp_path, corruption):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        path = recorder.directory / "events.ndjson"
        raw = path.read_bytes()
        if corruption == "malformed":
            raw = b"!" + raw[1:]
        elif corruption == "valid_change":
            raw = raw.replace(b'"consoleOffset":0', b'"consoleOffset":9', 1)
        elif corruption == "truncated":
            raw = raw[:-2]
        else:
            raw = raw.replace(b'"index":0', b'"index":0,"index":0', 1)
        path.write_bytes(raw)
        with pytest.raises((RecorderError, ValueError)):
            await recorder.seal(metadata(), terminal(), deadline=1e30)
        assert not (recorder.directory / "trace.json").exists()
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["seal.header", "seal.record", "seal.completion"])
def test_cancel_sealing_deletes_temp_and_published_path(tmp_path, stage):
    async def run():
        ops = FaultOps(stage, gate=True)
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path, file_ops=ops)
        await recorder.commit_chunk(records())
        task = asyncio.create_task(recorder.seal(metadata(), terminal(), deadline=1e30))
        await wait_thread(ops.started)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        ops.proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not (recorder.directory / "trace.json").exists()
        assert not (recorder.directory / "trace.tmp").exists()
        await recorder.dispose()

    asyncio.run(run())
