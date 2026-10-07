import asyncio
import copy

import pytest
from codetrace_backend.runs.protocol import encode
from codetrace_backend.trace_recorder import RecorderError, RunMetadata, TraceRecorder
from codetrace_contracts import default_limits

from .helpers import DOCUMENT, RUN_ID, document, entry, metadata, records, terminal


def test_exact_fixture_canonical_source_and_arrays(tmp_path):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:3])
        await recorder.commit_chunk(records()[3:])
        sealed = await recorder.seal(metadata(), terminal(), deadline=1e30)
        assert sealed.path.read_bytes() == encode(DOCUMENT)
        assert sealed.traceBytes == len(encode(DOCUMENT))
        assert document(sealed.path) == DOCUMENT
        assert sealed.eventCount == 3
        assert sealed.path.stat().st_mode & 0o777 == 0o400
        assert recorder.directory.stat().st_mode & 0o777 == 0o700
        with pytest.raises(RecorderError):
            await recorder.commit_chunk(records())
        with pytest.raises(RecorderError):
            await recorder.seal(metadata(), terminal(), deadline=1e30)
        await recorder.dispose()
        await recorder.dispose()
        assert not recorder.directory.exists()

    asyncio.run(run())


@pytest.mark.parametrize("code", [" x = 1\r\n", "# ё\u0301 😀\r\n", "\ud800", "\x00\t \n"])
def test_source_is_exact_and_metadata_detached(tmp_path, code):
    async def run():
        config = default_limits()
        meta = metadata(config, code)
        config["max_events"] = 2
        assert meta.limits["max_events"] == 100000
        with pytest.raises(TypeError):
            meta.limits["max_events"] = 99
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        sealed = await recorder.seal(meta, terminal(), deadline=1e30)
        assert document(sealed.path)["sourceCode"] == code
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,text", [("stdout", "{}\n"), ("stderr", "ошибка😀"), ("input", "  \n")]
)
def test_console_and_run_end_fields(tmp_path, kind, text):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:3])
        console = entry(kind=kind, text=text)
        await recorder.commit_console(console)
        tail = records()[3:]
        tail[0]["record"]["consoleOffset"] = 1
        await recorder.commit_chunk(tail)
        sealed = await recorder.seal(metadata(), terminal(consoleCount=1), deadline=1e30)
        d = document(sealed.path)
        end = d["events"][-1]
        assert end == {
            "index": 2,
            "kind": "RUN_END",
            "stateVersion": 2,
            "frameId": None,
            "source": None,
            "consoleOffset": 1,
            "payload": {},
        }
        assert d["console"] == [console]
        assert d["events"][0]["consoleOffset"] == 0
        assert d["events"][1]["consoleOffset"] == 1
        assert sum(e["kind"] == "RUN_END" for e in d["events"]) == 1
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("reason", ["SYNTAX_ERROR", "UNSUPPORTED_FEATURE", "ACTIVE_TIMEOUT"])
def test_no_observation_only_checkpoint_and_end(tmp_path, reason):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:1])
        t = terminal(
            outcome="USER_ERROR" if reason != "ACTIVE_TIMEOUT" else "LIMIT_EXCEEDED",
            reason=reason,
            finalStateVersion=0,
            eventCount=0,
        )
        sealed = await recorder.seal(metadata(), t, deadline=1e30)
        d = document(sealed.path)
        assert len(d["states"]) == len(d["events"]) == 1
        assert d["completion"]["stateAccuracy"] == "unavailable"
        assert d["completion"]["traceComplete"] == (reason != "ACTIVE_TIMEOUT")
        assert d["events"][0]["kind"] == "RUN_END"
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["index", "version", "offset", "end", "empty_delta", "bad_console"]
)
def test_reject_invalid_record_without_commit(tmp_path, fault):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:3])
        previous = recorder.counters
        bad = copy.deepcopy(records()[3:])
        if fault == "index":
            bad[0]["record"]["index"] = 10
        elif fault == "version":
            bad[1]["record"]["version"] = 10
        elif fault == "offset":
            bad[0]["record"]["consoleOffset"] = 1
        elif fault == "end":
            bad[0]["record"] = DOCUMENT["events"][-1]
        elif fault == "empty_delta":
            bad[1]["record"]["changes"]["stack"] = None
            bad[1]["record"]["changes"]["framesRemove"] = []
        with pytest.raises((ValueError, RecorderError)):
            if fault == "bad_console":
                await recorder.commit_console(entry(3))
            else:
                await recorder.commit_chunk(bad)
        assert recorder.counters == previous
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("finalStateVersion", 99),
        ("finalStateVersion", 1),
        ("finalStateVersion", None),
        ("eventCount", 3),
        ("consoleCount", 1),
        ("runId", "12345678-1234-4234-8234-123456789abc"),
        ("acceptedAt", "2026-10-06T00:00:00Z"),
        ("traceUsable", False),
        ("cleanupConfirmed", False),
    ],
)
def test_inconsistent_terminal_never_sealed(tmp_path, field, value):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        with pytest.raises((ValueError, RecorderError)):
            await recorder.seal(metadata(), terminal(**{field: value}), deadline=1e30)
        assert not (recorder.directory / "trace.json").exists()
        await recorder.dispose()

    asyncio.run(run())


def test_event_limit_reserved_end_and_numeric_versions(tmp_path):
    async def run():
        config = default_limits()
        config["max_events"] = 3
        recorder = TraceRecorder(RUN_ID, config, temp_root=tmp_path)
        await recorder.commit_chunk(records())
        extra = copy.deepcopy(records()[3])
        extra["record"]["index"] = 2
        with pytest.raises(ValueError):
            await recorder.commit_chunk([extra])
        sealed = await recorder.seal(metadata(config), terminal(), deadline=1e30)
        assert len(document(sealed.path)["events"]) == 3
        await recorder.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("runId", "bad"),
        ("sourceFileName", "other.py"),
        ("pythonVersion", "3.14.7"),
        ("acceptedAt", "not-date"),
        ("sourceCode", 1),
        ("sourceCode", "a" * 65537),
    ],
)
def test_invalid_metadata(field, value):
    args = dict(
        runId=RUN_ID, sourceCode="", acceptedAt=terminal().acceptedAt, limits=default_limits()
    )
    args[field] = value
    with pytest.raises((ValueError, RecorderError)):
        RunMetadata(**args)


def test_thousands_events_bounded_live_metadata(tmp_path):
    async def run():
        recorder = TraceRecorder(RUN_ID, temp_root=tmp_path)
        await recorder.commit_chunk(records()[:3])
        for index in range(1, 3000):
            event = copy.deepcopy(records()[2])
            event["record"]["index"] = index
            await recorder.commit_chunk([event])
        assert recorder.counters["eventCount"] == 3000
        assert recorder.counters["stateCount"] == 2
        assert recorder.counters["spoolBytes"] > 400000
        assert not any(isinstance(v, list) for v in vars(recorder).values())
        assert all(
            len(v) <= 3 for k, v in vars(recorder).items() if isinstance(v, dict) and k != "_limits"
        )
        # Config is the only fixed-size dict larger than three.
        await recorder.dispose()

    # Avoid including config in the three-spool invariant.
    asyncio.run(run())
