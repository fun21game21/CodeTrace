import asyncio
import copy
import json
from dataclasses import replace

from codetrace_backend.docker_adapter import (
    CleanupResult,
    ContainerHandle,
    ContainerInspection,
    DockerAdapter,
    ImageIdentity,
)
from codetrace_backend.runs import RunManager
from codetrace_backend.runs.protocol import encode
from codetrace_contracts import default_limits

RUN_ID = "12345678-1234-4234-8234-123456789abc"
IMAGE = ImageIdentity("sha256:" + "a" * 64)


def checkpoint(version=0):
    return {
        "kind": "state",
        "record": {
            "kind": "checkpoint",
            "version": version,
            "state": {
                "version": version,
                "globals": [],
                "objects": [],
                "frames": [],
                "stack": [],
                "eventRoots": [],
            },
        },
    }


def event(index=0, version=0, offset=0):
    return {
        "kind": "event",
        "record": {
            "kind": "LINE",
            "index": index,
            "stateVersion": version,
            "consoleOffset": offset,
            "frameId": "f1",
            "source": {"fileName": "<codetrace-user>", "lineNumber": 1},
            "payload": {},
        },
    }


def diagnostic(reason="RUNNER_FAILURE"):
    return {
        "category": "infrastructure",
        "code": reason,
        "typeName": None,
        "message": "Failure",
        "source": None,
        "columnNumber": None,
        "traceback": [],
        "omittedTracebackFrames": 0,
    }


class FakeClock:
    def __init__(self):
        self.time = 0
        self.wakes = set()

    def now(self):
        return self.time

    def utc(self):
        return "2026-10-05T20:00:00Z"

    async def wait(self, wake, delay):
        self.wakes.add(wake)
        try:
            await wake.wait()
        finally:
            self.wakes.discard(wake)

    def advance(self, seconds):
        self.time += seconds
        for wake in self.wakes:
            wake.set()


class Sink:
    def __init__(self):
        self.records = []
        self.console = []
        self.fail = False
        self.gate = None

    async def commit_chunk(self, records):
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise OSError("test sink failure")
        assert len(self.records) + len(records) <= 1000
        self.records.extend(copy.deepcopy(records))

    async def commit_console(self, entry):
        if self.fail:
            raise OSError("test sink failure")
        self.console.append(copy.deepcopy(entry))


class Writer:
    def __init__(self):
        self.messages = []
        self.closed = False
        self.fail = False
        self.gate = None

    def write(self, raw):
        if self.fail:
            raise BrokenPipeError()
        self.messages.append(json.loads(raw))

    async def drain(self):
        if self.gate is not None:
            await self.gate.wait()

    def close(self):
        self.closed = True


class Attachment:
    def __init__(self):
        self.stdin = Writer()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.closed = False
        self.seq = 0

    async def close(self):
        self.closed = True

    def send(self, kind, payload, **overrides):
        self.seq += 1
        data = {
            "protocolVersion": 1,
            "runId": RUN_ID,
            "seq": self.seq,
            "type": kind,
            "payload": payload,
        }
        data.update(overrides)
        self.stdout.feed_data(encode(data) + b"\n")

    def eof(self):
        if not self.stdout.at_eof():
            self.stdout.feed_eof()
        if not self.stderr.at_eof():
            self.stderr.feed_eof()


class FakeDocker(DockerAdapter):
    def __init__(self, image=IMAGE, limits=None):
        super().__init__(image, limits)
        self.att = Attachment()
        self.created = 0
        self.killed = 0
        self.cleaned = 0
        self.handle = None
        self.state = None
        self.exited = asyncio.Event()
        self.cleanup_ok = True
        self.auto_ready = True
        self.create_gate = None
        self.attach_fail = False
        self.orphans = []
        self.inspect_fail = False
        self.cleanup_gate = None

    async def check_available(self):
        return "fake"

    async def create(self, run_id):
        self.created += 1
        self.handle = ContainerHandle("b" * 64, run_id, "codetrace-" + run_id)
        self.state = ContainerInspection(
            self.handle.id,
            self.handle.name,
            IMAGE.imageId,
            "created",
            False,
            0,
            False,
            None,
            None,
            {"codetrace.managed": "true", "codetrace.run_id": run_id},
        )
        if self.create_gate:
            await self.create_gate.wait()
        return self.handle

    async def start_attach(self, handle):
        if self.attach_fail:
            raise RuntimeError("start fault")
        self.state = replace(
            self.state, running=True, status="running", startedAt="2026-10-05T20:00:00Z"
        )
        if self.auto_ready:
            self.att.send("ready", {"pythonVersion": "3.13.16", "protocolVersion": 1})
        return self.att

    def end(self, code=0, oom=False, eof=True):
        self.state = replace(
            self.state, running=False, status="exited", exitCode=code, oomKilled=oom
        )
        if eof:
            self.att.eof()
        self.exited.set()

    async def wait(self, handle, **kwargs):
        await self.exited.wait()
        return self.state.exitCode

    async def inspect(self, handle):
        if self.inspect_fail:
            raise RuntimeError("inspect fault")
        return self.state

    async def kill(self, handle):
        self.killed += 1
        if self.state and self.state.running:
            self.end(137)
        return True

    async def cleanup(self, handle):
        self.cleaned += 1
        if self.cleanup_gate:
            await self.cleanup_gate.wait()
        if self.cleanup_ok:
            self.state = None
            self.handle = None
            self.att.eof()
        return CleanupResult(self.cleanup_ok, 1)

    async def list_managed_containers(self):
        return tuple(self.orphans + ([self.handle] if self.handle else []))

    async def cleanup_managed_container(self, handle):
        if handle in self.orphans:
            self.orphans.remove(handle)
            return CleanupResult(self.cleanup_ok, 1)
        return await self.cleanup(handle)


async def pump(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


async def setup(*, ready=True, clock=None, **overrides):
    limits = default_limits()
    limits.update(overrides)
    fake = FakeDocker(limits=limits)
    fake.auto_ready = ready
    manager = RunManager(clock=clock, adapter_factory=lambda image, limits: fake)
    sink = Sink()
    notifications = []
    lease = await manager.start_run(
        run_id=RUN_ID,
        code="x=1",
        image=IMAGE,
        sink=sink,
        subscriber=notifications.append,
        limits=limits,
    )
    await pump()
    return manager, lease, fake, sink, notifications


async def finish(fake, session, *, reason="NONE"):
    if session.prefix.version < 0:
        fake.att.send("trace_chunk", {"records": [checkpoint()]})
    payload = {
        "outcome": "SUCCESS" if reason == "NONE" else "USER_ERROR",
        "reason": reason,
        "primaryDiagnostic": None
        if reason == "NONE"
        else {
            **diagnostic(reason),
            "category": "syntax" if reason == "SYNTAX_ERROR" else "runtime",
        },
        "finalStateVersion": max(0, session.prefix.version),
    }
    fake.att.send("finished", payload)
    fake.end()
    return await asyncio.wait_for(session.await_result(), 1)
