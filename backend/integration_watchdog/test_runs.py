"""Real WATCHDOG orchestration over the installed RUNNER_IO image and actual DOCKER adapter."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from codetrace_backend.docker_adapter import DockerAdapter, ImageIdentity
from codetrace_backend.runs import RunManager, RunRejected
from codetrace_contracts import default_limits, validate_contract

ROOT = Path(__file__).resolve().parents[2]
IMAGE = ImageIdentity.load(ROOT / "backend/local-image.json")
SESSION = os.environ.get("CODETRACE_WATCHDOG_TEST_SESSION", str(uuid4()))
OWNED = set()


class Docker(DockerAdapter):
    def __init__(self, image, limits, program=None):
        super().__init__(image, limits)
        self.program = program

    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = ["--label", "codetrace.watchdog_test=" + SESSION]
        if self.program is not None:
            # Trusted infrastructure fixture only; never user source or production override.
            argv[-1:-1] = ["--entrypoint=python"]
            argv.extend(["-c", self.program])
        return tuple(argv)

    async def create(self, run_id):
        handle = await super().create(run_id)
        OWNED.add(handle.id)
        return handle


class Sink:
    def __init__(self):
        self.records = []
        self.console = []

    async def commit_chunk(self, records):
        for item in records:
            validate_contract(
                item["record"],
                "trace",
                "StateRecord" if item["kind"] == "state" else "PythonTraceEvent",
            )
        assert len(self.records) + len(records) < 20000
        self.records.extend(records)

    async def commit_console(self, entry):
        validate_contract(entry, "trace", "ConsoleEntry")
        self.console.append(entry)


async def start(code, *, program=None, **overrides):
    limits = default_limits()
    limits.update(overrides)
    adapter = Docker(IMAGE, limits, program)
    manager = RunManager(adapter_factory=lambda image, config: adapter)
    sink = Sink()
    queue = asyncio.Queue(maxsize=10000)
    lease = await manager.start_run(
        run_id=str(uuid4()),
        code=code,
        image=IMAGE,
        limits=limits,
        sink=sink,
        subscriber=queue.put_nowait,
    )
    return manager, lease.session, adapter, sink, queue, lease


async def requested(queue):
    async with asyncio.timeout(10):
        while True:
            event = await queue.get()
            if event.kind == "input_requested":
                return event.payload
            assert event.kind != "result", event.payload


async def assert_clean(session, adapter):
    result = await asyncio.wait_for(session.await_result(), 15)
    assert result.cleanupConfirmed, result
    if session.container:
        assert await adapter.inspect(session.container) is None
        assert await adapter._lookup(session.container.name, by_name=True) is None
    assert session.attachment is None or session.attachment.returncode is not None
    assert all(task.done() for task in session._readers)
    return result


@pytest.mark.parametrize(
    "source,reason",
    [
        ("x=1", "NONE"),
        ("print('hello')", "NONE"),
        ("if :", "SYNTAX_ERROR"),
        ("1/0", "UNCAUGHT_EXCEPTION"),
        ("import os", "UNSUPPORTED_FEATURE"),
    ],
)
def test_real_normal_and_errors(source, reason):
    async def scenario():
        manager, s, adapter, sink, queue, lease = await start(source)
        try:
            result = await assert_clean(s, adapter)
            assert result.reason == reason and result.traceUsable and result.exitCode == 0
            if source.startswith("print"):
                assert "".join(e["text"] for e in sink.console) == "hello\n"
            assert manager.availability.busy
            lease.finish_ownership()
            assert not manager.availability.busy
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [1, 2])
def test_real_inputs(count):
    async def scenario():
        manager, s, adapter, sink, queue, lease = await start(
            "\n".join(f'print(input("Q{i}:"))' for i in range(count))
        )
        try:
            original = None
            for i in range(count):
                request = await requested(queue)
                observed = await adapter.inspect(s.container)
                current = (observed.id, observed.startedAt)
                if original is None:
                    original = current
                assert current == original
                assert (
                    await manager.submit_input(s.runId, request["requestId"], f"answer{i}")
                ).accepted
            assert (await assert_clean(s, adapter)).reason == "NONE"
            assert len([e for e in sink.console if e["kind"] == "input"]) == count
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "source", ["while True:\n pass", "import math\nmath.factorial(1000000000)"]
)
def test_real_external_active_watchdog(source):
    async def scenario():
        manager, s, adapter, _, _, _ = await start(source, active_timeout_ms=250)
        try:
            result = await assert_clean(s, adapter)
            assert result.reason == "ACTIVE_TIMEOUT" and result.terminalStatus == "LIMIT_EXCEEDED"
            assert result.exitCode == 137 and not result.oomKilled
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_real_wait_excludes_active_budget():
    async def scenario():
        manager, s, adapter, _, queue, _ = await start(
            'input("x:")\nx=2',
            active_timeout_ms=500,
            input_idle_timeout_ms=4000,
            container_lifetime_ms=10000,
        )
        try:
            request = await requested(queue)
            active = s.timing.elapsed()[0]
            assert 0 < active < 500
            await asyncio.sleep(0.7)
            assert s.status == "WAITING_INPUT" and s.timing.elapsed()[0] == active
            assert (await manager.submit_input(s.runId, request["requestId"], "")).accepted
            result = await assert_clean(s, adapter)
            assert result.reason == "NONE" and result.waitingElapsedMs >= 700
            assert active <= result.activeElapsedMs < 500
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"input_idle_timeout_ms": 150}, "INPUT_IDLE_TIMEOUT"),
        ({"container_lifetime_ms": 700, "input_idle_timeout_ms": 4000}, "CONTAINER_LIFETIME"),
    ],
)
def test_real_wait_deadlines(overrides, reason):
    async def scenario():
        manager, s, adapter, _, queue, _ = await start('input("x:")', **overrides)
        try:
            await requested(queue)
            result = await assert_clean(s, adapter)
            assert result.reason == reason and result.exitCode == 137
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "action,reason",
    [
        ("disconnect", "CONNECTION_LOST"),
        ("shutdown", "BACKEND_SHUTDOWN"),
        ("external-kill", "RUNNER_FAILURE"),
    ],
)
def test_real_termination_while_waiting(action, reason):
    async def scenario():
        manager, s, adapter, _, queue, _ = await start("input()")
        try:
            await requested(queue)
            if action == "disconnect":
                manager.connection_lost(s.runId)
            elif action == "shutdown":
                await manager.shutdown()
            else:
                await adapter.kill(s.container)
            result = await assert_clean(s, adapter)
            assert result.reason == reason and s.pendingInput is None
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_real_runner_abort_limit():
    async def scenario():
        manager, s, adapter, _, _, _ = await start("while True:\n pass", max_events=20)
        try:
            result = await assert_clean(s, adapter)
            assert result.reason == "EVENT_LIMIT" and result.terminalStatus == "LIMIT_EXCEEDED"
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_real_busy_no_second_container():
    async def scenario():
        manager, s, adapter, sink, queue, _ = await start("input()")
        try:
            await requested(queue)
            count = len(OWNED)
            with pytest.raises(RunRejected, match="RUN_BUSY"):
                await manager.start_run(
                    run_id=str(uuid4()),
                    code="",
                    image=IMAGE,
                    sink=sink,
                    subscriber=queue.put_nowait,
                )
            assert len(OWNED) == count
            manager.connection_lost(s.runId)
            await assert_clean(s, adapter)
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_real_native_stderr_overflow():
    program = """import os,sys,json,time
r=os.environ['CODETRACE_RUN_ID']
print(json.dumps({'protocolVersion':1,'runId':r,'seq':1,'type':'ready','payload':{'pythonVersion':'3.13.16','protocolVersion':1}}),flush=True)
sys.stdin.readline()
sys.stderr.write('x'*100000);sys.stderr.flush()
time.sleep(60)
"""

    async def scenario():
        manager, s, adapter, sink, _, _ = await start("", program=program, native_stderr_bytes=1024)
        try:
            result = await assert_clean(s, adapter)
            assert (
                result.reason == "RUNNER_FAILURE" and sink.console == [] and s._native_bytes == 1025
            )
        finally:
            await manager.shutdown()

    asyncio.run(scenario())


def test_real_startup_cleanup_owns_only_managed():
    async def scenario():
        adapter = Docker(IMAGE, default_limits())
        orphan = await adapter.create(str(uuid4()))
        foreign_run = str(uuid4())
        argv = list(adapter.create_argv(foreign_run, str(uuid4())))
        argv[argv.index("codetrace.managed=true")] = "codetrace.managed=false"
        foreign = (await adapter._command(tuple(argv[1:]))).decode().strip()
        manager = RunManager(adapter_factory=lambda image, limits: adapter)
        try:
            assert (await manager.startup_cleanup(IMAGE)).available
            assert await adapter.inspect(orphan) is None
            assert await adapter._lookup(foreign) == foreign
        finally:
            await adapter.cleanup(orphan)
            await adapter._command(("rm", "--force", foreign))

    asyncio.run(scenario())


def test_z_no_remaining_test_containers():
    async def scenario():
        adapter = Docker(IMAGE, default_limits())
        remaining = await adapter._command(
            (
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                "label=codetrace.watchdog_test=" + SESSION,
                "--format",
                "{{.ID}}",
            )
        )
        assert not remaining.strip()
        for identifier in OWNED:
            assert await adapter._lookup(identifier) is None

    asyncio.run(scenario())


def test_real_full_sized_input_echo():
    async def scenario():
        manager, s, adapter, sink, queue, _ = await start("x=input()")
        try:
            request = await requested(queue)
            assert (await manager.submit_input(s.runId, request["requestId"], "x" * 8192)).accepted
            result = await assert_clean(s, adapter)
            assert result.reason == "NONE"
            assert [e["text"] for e in sink.console if e["kind"] == "input"] == ["x" * 8192 + "\n"]
        finally:
            await manager.shutdown()

    asyncio.run(scenario())
