"""Opt-in real Docker → WATCHDOG → RECORDER. No skips or alternative Runner."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from codetrace_backend.docker_adapter import DockerAdapter, ImageIdentity
from codetrace_backend.runs import RunManager, RunRejected
from codetrace_backend.trace_recorder import ResultAccessError, ResultService, TraceRecorder
from codetrace_contracts import default_limits, validate_contract

from runner.tests.encoder.helpers import seek

ROOT = Path(__file__).resolve().parents[2]
IMAGE = ImageIdentity.load(ROOT / "backend/local-image.json")
SESSION = os.environ.get("CODETRACE_RECORDER_TEST_SESSION", str(uuid4()))
OWNED = set()
DIRECTORIES = set()


class Docker(DockerAdapter):
    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = ["--label", "codetrace.recorder_test=" + SESSION]
        return tuple(argv)

    async def create(self, run_id):
        handle = await super().create(run_id)
        OWNED.add(handle.id)
        return handle


async def start(code, *, manager=None, **overrides):
    limits = default_limits()
    limits.update(overrides)
    if manager is None:
        manager = RunManager(adapter_factory=Docker)
    service = ResultService(clock=manager.clock)
    recorder = TraceRecorder(str(uuid4()), limits)
    DIRECTORIES.add(recorder.directory)
    service.register(recorder, code)
    queue = asyncio.Queue(maxsize=10000)

    def subscriber(event):
        service.observe(event)
        queue.put_nowait(event)

    try:
        lease = await manager.start_run(
            run_id=recorder.runId,
            code=code,
            image=IMAGE,
            sink=recorder,
            subscriber=subscriber,
            limits=limits,
        )
    except BaseException:
        await service.abandon(recorder.runId)
        raise
    return manager, service, recorder, lease, service.bind(lease), queue


async def requested(queue):
    async with asyncio.timeout(10):
        while True:
            event = await queue.get()
            if event.kind == "input_requested":
                return event.payload
            assert event.kind != "result", event.payload


def bytes_of(service, run_id):
    parts = []  # Only small integration documents are parsed in tests.
    with service.get_trace(run_id) as access:
        while part := access.read():
            parts.append(part)
    return b"".join(parts)


async def checked(service, lease, handle):
    execution = await asyncio.wait_for(lease.session.await_result(), 20)
    summary = await asyncio.wait_for(handle.await_result(), 20)
    assert execution.cleanupConfirmed
    if lease.session.container:
        assert await lease.session.adapter.inspect(lease.session.container) is None
    validate_contract(summary.completion, "trace", "Completion")
    if summary.traceAvailable:
        raw = bytes_of(service, lease.session.runId)
        assert raw == bytes_of(service, lease.session.runId)
        assert len(raw) == summary.traceBytes <= lease.session.limits["trace_bytes"]
        d = json.loads(raw)
        validate_contract(d, "trace")
        assert sum(e["kind"] == "RUN_END" for e in d["events"]) == 1
        assert d["events"][-1]["kind"] == "RUN_END"
        assert d["events"][-1]["consoleOffset"] == len(d["console"])
        assert d["events"][-1]["stateVersion"] == len(d["states"]) - 1
        return execution, summary, d
    with pytest.raises(ResultAccessError) as error:
        service.get_trace(lease.session.runId)
    assert error.value.status == 404
    return execution, summary, None


async def cleanup(manager, service, recorder):
    await manager.shutdown()
    await service.shutdown()
    assert not recorder.directory.exists()


@pytest.mark.parametrize(
    "source,reason",
    [
        ("x=42", "NONE"),
        ("print('hello')", "NONE"),
        ("", "NONE"),
        ("if :", "SYNTAX_ERROR"),
        ("1/0", "UNCAUGHT_EXCEPTION"),
        ("import os", "UNSUPPORTED_FEATURE"),
        ("def f():\n yield 1", "UNSUPPORTED_FEATURE"),
    ],
)
def test_real_success_errors_full_contract_and_final_state(source, reason):
    async def run():
        manager, service, recorder, lease, handle, _ = await start(source)
        try:
            execution, summary, d = await checked(service, lease, handle)
            assert execution.reason == summary.completion["reason"] == reason
            assert summary.traceAvailable and summary.completion["traceComplete"]
            assert d["sourceCode"] == source
            if source in {"if :", "def f():\n yield 1"}:
                assert len(d["states"]) == len(d["events"]) == 1
                assert d["completion"]["stateAccuracy"] == "unavailable"
            else:
                final = seek(d["states"], d["events"][-1]["stateVersion"])
                assert final["frames"] == final["stack"] == final["eventRoots"] == []
                assert d["completion"]["stateAccuracy"] == "final"
                if source == "x=42":
                    assert final["globals"] == [
                        {"name": "x", "value": {"kind": "scalar", "type": "int", "value": "42"}}
                    ]
            if source.startswith("print"):
                assert "".join(e["text"] for e in d["console"]) == "hello\n"
            assert manager.availability.busy
            await service.release(lease.session.runId)
            assert not manager.availability.busy
        finally:
            await cleanup(manager, service, recorder)

    asyncio.run(run())


@pytest.mark.parametrize("count", [1, 2])
def test_real_input_same_process_console_and_retained_result(count):
    async def run():
        code = "\r\n".join(f'print(input("Q{i}:"))' for i in range(count))
        manager, service, recorder, lease, handle, queue = await start(code)
        try:
            original = None
            for i in range(count):
                request = await requested(queue)
                inspection = await lease.session.adapter.inspect(lease.session.container)
                current = inspection.id, inspection.startedAt
                original = current if original is None else original
                assert current == original
                text = "  😀  " if i == 0 else ""
                assert (
                    await manager.submit_input(lease.session.runId, request["requestId"], text)
                ).accepted
            _, summary, d = await checked(service, lease, handle)
            assert summary.traceAvailable and d["sourceCode"] == code
            inputs = [e for e in d["console"] if e["kind"] == "input"]
            assert len(inputs) == count and inputs[0]["text"] == "  😀  \n"
            if count == 2:
                assert inputs[1]["text"] == "\n" and inputs[1]["requestId"] == "q2"
        finally:
            await cleanup(manager, service, recorder)

    asyncio.run(run())


@pytest.mark.parametrize(
    "source,overrides,reason,wait",
    [
        ("while True:\n pass", {"active_timeout_ms": 800}, "ACTIVE_TIMEOUT", False),
        ('x=input("Q:")', {"input_idle_timeout_ms": 100}, "INPUT_IDLE_TIMEOUT", True),
        (
            'x=input("Q:")',
            {"container_lifetime_ms": 600, "input_idle_timeout_ms": 3000},
            "CONTAINER_LIFETIME",
            True,
        ),
        ("for i in range(100):\n x=i", {"max_events": 16}, "EVENT_LIMIT", False),
    ],
)
def test_real_limits_retained_valid_prefix(source, overrides, reason, wait):
    async def run():
        manager, service, recorder, lease, handle, queue = await start(source, **overrides)
        try:
            if wait:
                await requested(queue)
            execution, summary, d = await checked(service, lease, handle)
            assert execution.reason == summary.completion["reason"] == reason
            assert execution.traceUsable and summary.traceAvailable
            assert not d["completion"]["traceComplete"]
            assert d["completion"]["stateAccuracy"] == "last_observed"
        finally:
            await cleanup(manager, service, recorder)

    asyncio.run(run())


@pytest.mark.parametrize(
    "action,reason", [("disconnect", "CONNECTION_LOST"), ("shutdown", "BACKEND_SHUTDOWN")]
)
def test_real_interruption_retains_flushed_prefix(action, reason):
    async def run():
        manager, service, recorder, lease, handle, queue = await start('x=input("Q:")')
        try:
            await requested(queue)
            if action == "disconnect":
                manager.connection_lost(lease.session.runId)
            else:
                await manager.shutdown()
            execution, summary, d = await checked(service, lease, handle)
            assert execution.reason == reason and summary.traceAvailable
            assert d["completion"]["stateAccuracy"] == "last_observed"
            assert not d["completion"]["traceComplete"]
        finally:
            await cleanup(manager, service, recorder)

    asyncio.run(run())


@pytest.mark.parametrize("dispose", ["release", "ttl"])
def test_real_slot_busy_then_reusable_after_result_disposal(dispose):
    async def run():
        manager, service, recorder, lease, handle, _ = await start(
            "x=1", result_ttl_ms=250 if dispose == "ttl" else 300000
        )
        try:
            await checked(service, lease, handle)
            assert manager.availability.busy
            with pytest.raises(RunRejected) as error:
                await manager.start_run(
                    run_id=str(uuid4()),
                    code="x=2",
                    image=IMAGE,
                    sink=recorder,
                    subscriber=lambda event: None,
                )
            assert error.value.code == "RUN_BUSY"
            if dispose == "release":
                await service.release(lease.session.runId)
            else:
                async with asyncio.timeout(5):
                    while manager.availability.busy:
                        await asyncio.sleep(0.01)
            assert not recorder.directory.exists()
            _, service2, recorder2, lease2, handle2, _ = await start("x=2", manager=manager)
            try:
                _, summary2, _ = await checked(service2, lease2, handle2)
                assert summary2.traceAvailable
                await service2.release(lease2.session.runId)
                assert not manager.availability.busy
            finally:
                await cleanup(manager, service2, recorder2)
        finally:
            await cleanup(manager, service, recorder)

    asyncio.run(run())


def test_z_no_managed_suite_containers():
    async def run():
        adapter = Docker(IMAGE)
        remaining = await adapter._command(
            (
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                "label=codetrace.recorder_test=" + SESSION,
                "--format",
                "{{.ID}}",
            )
        )
        assert not remaining.strip()
        for identifier in OWNED:
            assert await adapter._lookup(identifier, by_name=False) is None

    asyncio.run(run())


def test_z_no_runtime_result_directories():
    assert all(not p.exists() for p in DIRECTORIES)
    assert all(p.parent == Path(tempfile.gettempdir()) for p in DIRECTORIES)
