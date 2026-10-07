import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

from codetrace_backend.config import AppConfig
from codetrace_backend.docker_adapter import ImageIdentity
from codetrace_backend.runs import (
    Availability,
    InputSubmission,
    LifecycleEvent,
    RunRejected,
    SystemClock,
)
from codetrace_backend.trace_recorder import ResultAccessError, ResultSummary
from codetrace_backend.transport.lifecycle import Runtime
from codetrace_contracts import default_limits

ORIGIN = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/api/runs"
REQUEST = "dd53c9a6-1c0e-4a60-b075-c95b00a23854"
ROOT = Path(__file__).resolve().parents[3]
COMPLETION = json.loads((ROOT / "contracts/fixtures/valid/trace-minimal-success.json").read_text())[
    "completion"
]


class Access:
    contentType = "application/json"

    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.traceBytes = len(data)
        self.closed = False

    def read(self, size=65536):
        assert size <= 65536
        return self.data.read(size)

    def close(self):
        self.closed = True
        self.data.close()


class Service:
    def __init__(self, log):
        self.log = log
        self.recorder = None
        self.code = None
        self.future = None
        self.accesses = []
        self.data = b'{ "exact": "\\ud83d\\ude42", "number": 42 }\n'
        self.access_status = 200
        self.release_status = 204
        self.deadline = 123.0

    def register(self, recorder, code):
        self.log.append("register")
        self.recorder, self.code = recorder, code

    def observe(self, event):
        self.log.append("observe:" + event.kind)

    def bind(self, lease):
        self.log.append("bind")
        assert lease.session.code == self.code
        self.future = asyncio.get_running_loop().create_future()
        service = self

        class Handle:
            async def await_result(self):
                return await asyncio.shield(service.future)

        return Handle()

    def publish(self):
        completion = dict(COMPLETION)
        self.future.set_result(
            ResultSummary(
                completion, True, "/api/runs/" + self.recorder.runId + "/trace", len(self.data), 3
            )
        )

    def get_trace(self, run_id):
        if self.access_status != 200:
            raise ResultAccessError(self.access_status)
        access = Access(self.data)
        self.accesses.append(access)
        return access

    async def release(self, run_id):
        if self.release_status != 204:
            raise ResultAccessError(self.release_status)
        return 204

    async def abandon(self, run_id):
        self.log.append("abandon")
        await self.recorder.dispose()
        self.recorder = None

    async def shutdown(self):
        self.log.append("service.shutdown")
        if self.recorder:
            await self.recorder.dispose()

    def recover_failed_ownership(self, manager):
        self.log.append("recover")


class Manager:
    def __init__(self, log):
        self.log, self.clock = log, SystemClock()
        self.busy = False
        self.reason = None
        self.reject = None
        self.subscriber = None
        self.run_id = None
        self.session = None
        self.responses = []
        self.input_result = InputSubmission(True)
        self.ack_during_submit = True

    @property
    def availability(self):
        return Availability(self.reason is None, self.busy, self.reason)

    async def start_run(self, **kwargs):
        self.log.append("start")
        if self.reject:
            raise RunRejected(self.reject)
        self.busy = True
        self.run_id = kwargs["run_id"]
        self.subscriber = kwargs["subscriber"]
        self.session = SimpleNamespace(
            code=kwargs["code"], timing=SimpleNamespace(since=self.clock.now())
        )
        self.emit("accepted", {"acceptedAt": "2026-10-05T20:00:00Z", "status": "STARTING"})
        return SimpleNamespace(session=self.session)

    def emit(self, kind, payload):
        self.subscriber(LifecycleEvent(kind, self.run_id, payload))

    async def submit_input(self, run_id, request_id, text):
        self.responses.append((run_id, request_id, text))
        if self.input_result.accepted and self.ack_during_submit:
            self.emit("status", "RUNNING")
            self.emit("input_accepted", request_id)
        return self.input_result

    def connection_lost(self, run_id):
        self.log.append("CONNECTION_LOST")

    def client_protocol_error(self, run_id):
        self.log.append("CLIENT_PROTOCOL_ERROR")

    async def shutdown(self):
        self.log.append("manager.shutdown")


class FakeRuntime(Runtime):
    def __init__(self, tmp_path):
        self.log = []
        super().__init__(
            AppConfig(temp_root=tmp_path, frontend_dist=tmp_path / "dist"),
            Manager(self.log),
            Service(self.log),
        )
        self.image = ImageIdentity("sha256:" + "a" * 64)

    async def startup(self):
        self.log.append("startup")

    async def refresh(self):
        return {
            "apiVersion": 1,
            "available": self.manager.reason is None,
            "reason": self.manager.reason,
            "pythonVersion": "3.13.16",
            "limits": default_limits(),
        }


def envelope(kind="start", seq=1, run_id=None, **payload):
    if kind == "start":
        payload = {"clientRequestId": REQUEST, "code": "x = 42\r\n", **payload}
    return {"apiVersion": 1, "seq": seq, "type": kind, "runId": run_id, "payload": payload}


def accepted(ws):
    ready = ws.receive_json()
    assert ready["type"] == "socket_ready"
    ws.send_json(envelope())
    result = ws.receive_json()
    assert result["type"] == "run_accepted"
    assert ws.receive_json()["payload"]["status"] == "STARTING"
    return result["runId"]
