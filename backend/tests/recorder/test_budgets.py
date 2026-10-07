import asyncio
import copy

import pytest
from codetrace_backend.runs.protocol import encode
from codetrace_backend.trace_recorder import RecorderError, TraceRecorder
from codetrace_contracts import default_limits

from .helpers import DOCUMENT, RUN_ID, document, entry, metadata, records, terminal


@pytest.mark.parametrize("extra", ["none", "source", "console", "diagnostic"])
@pytest.mark.parametrize("over", [0, 1])
def test_absolute_size_exact_boundary_and_one_byte_over(tmp_path, extra, over):
    async def run():
        config = default_limits()
        config["terminal_reserve_bytes"] = 1000
        code = "😀\r\n" * 30 if extra == "source" else ""
        d = copy.deepcopy(DOCUMENT)
        d["limits"] = config
        d["sourceCode"] = code
        t = terminal()
        if extra == "console":
            d["console"] = [entry(text="ё😀" * 30)]
            d["events"][-1]["consoleOffset"] = 1
            t = terminal(consoleCount=1)
        if extra == "diagnostic":
            from codetrace_backend.trace_recorder import build_completion

            t = terminal(outcome="USER_ERROR", reason="UNCAUGHT_EXCEPTION")
            d["completion"] = build_completion(t, 2)
        for _ in range(5):
            config["trace_bytes"] = len(encode(d)) - over
        expected = len(encode(d))
        assert expected == config["trace_bytes"] + over
        recorder = TraceRecorder(RUN_ID, config, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        if extra == "console":
            await recorder.commit_console(d["console"][0])
        if over:
            with pytest.raises(RecorderError):
                await recorder.seal(metadata(config, code), t, deadline=1e30)
            assert not (recorder.directory / "trace.json").exists()
        else:
            sealed = await recorder.seal(metadata(config, code), t, deadline=1e30)
            assert sealed.traceBytes == expected == config["trace_bytes"]
            assert sealed.path.read_bytes() == encode(d)
            document(sealed.path)
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("where", ["commit", "seal"])
def test_disk_budget_includes_all_live_files(tmp_path, where):
    async def run():
        first = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await first.commit_chunk(records())
        spool = first.counters["spoolBytes"]
        await first.dispose()
        budget = spool - 1 if where == "commit" else spool + 100
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path, disk_budget=budget)
        with pytest.raises(RecorderError):
            if where == "commit":
                await recorder.commit_chunk(records())
            else:
                await recorder.commit_chunk(records())
                await recorder.seal(metadata(), terminal(), deadline=1e30)
        assert sum(p.stat().st_size for p in recorder.directory.iterdir()) <= budget
        if where == "commit":
            assert recorder.counters["stateCount"] == 0
        assert not (recorder.directory / "trace.json").exists()
        await recorder.dispose()

    asyncio.run(run())


def test_ordinary_cannot_consume_terminal_reserve(tmp_path):
    async def run():
        config = default_limits()
        config["trace_bytes"] = 3000
        config["terminal_reserve_bytes"] = 2800
        recorder = TraceRecorder(RUN_ID, config, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:1])
        before = recorder.counters
        with pytest.raises(ValueError):
            await recorder.commit_chunk(records()[1:])
        assert recorder.counters == before
        await recorder.dispose()

    asyncio.run(run())
