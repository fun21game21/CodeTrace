import asyncio
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from codetrace_backend.runs import LifecycleEvent, TerminalResult
from codetrace_backend.trace_recorder import FileOps, ResultService, RunMetadata, TraceRecorder
from codetrace_contracts import default_limits, validate_contract
from watchdog.helpers import FakeClock

ROOT = Path(__file__).resolve().parents[3]
DOCUMENT = json.loads((ROOT / "contracts/fixtures/valid/trace-minimal-success.json").read_text())
RUN_ID = DOCUMENT["runId"]


def records():
    d = copy.deepcopy(DOCUMENT)
    return [
        {"kind": "state", "record": d["states"][0]},
        {"kind": "state", "record": d["states"][1]},
        {"kind": "event", "record": d["events"][0]},
        {"kind": "event", "record": d["events"][1]},
        {"kind": "state", "record": d["states"][2]},
    ]


def terminal(**overrides):
    fields = dict(
        runId=RUN_ID,
        terminalStatus="SUCCEEDED",
        outcome="SUCCESS",
        reason="NONE",
        primaryCause=None,
        runnerDiagnostic=None,
        finalStateVersion=2,
        exitCode=0,
        oomKilled=False,
        cleanupConfirmed=True,
        activeElapsedMs=125.25,
        waitingElapsedMs=0,
        acceptedAt=DOCUMENT["completion"]["acceptedAt"],
        finishedAt=DOCUMENT["completion"]["finishedAt"],
        traceUsable=True,
        eventCount=2,
        consoleCount=0,
    )
    fields.update(overrides)
    return TerminalResult(**fields)


def metadata(limits=None, code=""):
    return RunMetadata(
        RUN_ID, code, terminal().acceptedAt, default_limits() if limits is None else limits
    )


def entry(index=0, kind="stdout", text="hello\n"):
    return {
        "index": index,
        "kind": kind,
        "text": text,
        "requestId": "q1" if kind == "input" else None,
    }


def read_all(access):
    pieces = []  # Small test fixtures only, never production.
    with access:
        while data := access.read():
            pieces.append(data)
    return b"".join(pieces)


def document(path):
    result = json.loads(path.read_bytes())
    validate_contract(result, "trace")
    return result


class FaultOps(FileOps):
    def __init__(self, stage=None, occurrence=1, *, gate=False):
        self.stage, self.occurrence = stage, occurrence
        self.calls = 0
        self.started = threading.Event()
        self.proceed = threading.Event()
        self.gate = gate

    def write(self, file, data, stage):
        if stage == self.stage:
            self.calls += 1
            if self.calls == self.occurrence:
                super().write(file, data[: max(1, len(data) // 2)], stage)
                if self.gate:
                    self.started.set()
                    assert self.proceed.wait(5)
                    super().write(file, data[max(1, len(data) // 2) :], stage)
                    return
                else:
                    raise OSError("simulated disk full")
        super().write(file, data, stage)

    def fsync(self, file):
        if self.stage == "fsync":
            raise OSError("fsync failure")
        if self.stage == "corrupt":
            file.seek(0)
            file.write(b"!")
        super().fsync(file)

    def replace(self, source, destination):
        if self.stage == "replace":
            raise OSError("rename failure")
        super().replace(source, destination)


async def wait_thread(event):
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


class Lease:
    def __init__(self, result, limits, clock):
        self.calls = 0
        self._manager = SimpleNamespace(availability=SimpleNamespace(available=True))
        self.future = asyncio.get_running_loop().create_future()
        self.future.set_result(result)

        async def await_result():
            return await asyncio.shield(self.future)

        self.session = SimpleNamespace(
            runId=result.runId,
            limits=limits,
            acceptedAt=result.acceptedAt,
            clock=clock,
            await_result=await_result,
        )

    def finish_ownership(self):
        self.calls += 1


async def setup(root, result=None, *, ops=None, limits=None, clock=None, prefix=None, code=""):
    result = terminal() if result is None else result
    limits = default_limits() if limits is None else limits
    clock = FakeClock() if clock is None else clock
    recorder = TraceRecorder(RUN_ID, limits, temp_root=root, file_ops=ops)
    await recorder.commit_chunk(records() if prefix is None else prefix)
    service = ResultService(clock=clock)
    service.register(recorder, code)
    service.observe(LifecycleEvent("accepted", RUN_ID, {"acceptedAt": result.acceptedAt}))
    service.observe(LifecycleEvent("status", RUN_ID, "FINALIZING"))
    lease = Lease(result, limits, clock)
    lease.session._code = code
    handle = service.bind(lease)
    return service, recorder, lease, clock, handle
