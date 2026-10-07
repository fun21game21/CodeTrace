"""Actual FastAPI → DOCKER/WATCHDOG/RECORDER Docker transport. No alternative Runner."""

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from codetrace_backend.app import create_app
from codetrace_backend.config import AppConfig
from codetrace_backend.docker_adapter import DockerAdapter
from codetrace_backend.runs import RunManager
from codetrace_backend.transport.lifecycle import Runtime
from codetrace_contracts import validate_trace, validate_ws
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/api/runs"
SESSION = os.environ.get("CODETRACE_RUNS_TEST_SESSION", str(uuid4()))
OWNED = set()


class Docker(DockerAdapter):
    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = ["--label", "codetrace.runs_test=" + SESSION]
        return tuple(argv)

    async def create(self, run_id):
        handle = await super().create(run_id)
        OWNED.add(handle.id)
        return handle


@pytest.fixture
def app_client(tmp_path):
    config = AppConfig(image_path=ROOT / "backend/local-image.json", temp_root=tmp_path)
    manager = RunManager(adapter_factory=Docker)
    runtime = Runtime(config, manager)
    with TestClient(create_app(config, runtime=runtime), base_url=ORIGIN) as client:
        assert client.get("/api/health").json()["available"]
        yield client, runtime
    assert list(tmp_path.glob("codetrace-result-v1-*")) == []


def run(client, code, inputs=()):
    messages = []
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ready = ws.receive_json()
        validate_ws(ready)
        assert ready["type"] == "socket_ready" and ready["seq"] == 1
        request = str(uuid4())
        ws.send_json(
            {
                "apiVersion": 1,
                "seq": 1,
                "type": "start",
                "runId": None,
                "payload": {"clientRequestId": request, "code": code},
            }
        )
        accepted = ws.receive_json()
        validate_ws(accepted)
        if accepted["type"] == "run_rejected":
            return accepted, None, messages
        assert (
            accepted["type"] == "run_accepted" and accepted["payload"]["clientRequestId"] == request
        )
        run_id = accepted["runId"]
        seq = 2
        server_seq = 3
        answers = iter(inputs)
        while True:
            message = ws.receive_json()
            validate_ws(message)
            assert message["seq"] == server_seq and message["runId"] == run_id
            server_seq += 1
            messages.append(message)
            if message["type"] == "input_request":
                ws.send_json(
                    {
                        "apiVersion": 1,
                        "seq": seq,
                        "type": "input_response",
                        "runId": run_id,
                        "payload": {
                            "requestId": message["payload"]["requestId"],
                            "text": next(answers),
                        },
                    }
                )
                seq += 1
            if message["type"] == "run_finished":
                break
    finished = messages[-1]["payload"]
    assert finished["traceAvailable"] and finished["completion"]["cleanupConfirmed"]
    terminal = [m for m in messages if m["type"] == "status"][-1]
    assert terminal["seq"] < messages[-1]["seq"]
    response = client.get(finished["traceUrl"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert len(response.content) == finished["traceBytes"]
    assert response.content == client.get(finished["traceUrl"]).content
    trace = response.json()
    validate_trace(trace)
    assert trace["runId"] == run_id and trace["sourceCode"] == code
    assert trace["completion"] == finished["completion"]
    assert trace["events"][-1]["kind"] == "RUN_END"
    return finished, trace, messages


def release(client, run_id):
    assert (
        client.post("/api/runs/" + run_id + "/release", headers={"Origin": ORIGIN}).status_code
        == 204
    )


def begin_waiting(client):
    ws = client.websocket_connect(WS, headers={"Origin": ORIGIN})
    ws.__enter__()
    ws.receive_json()
    ws.send_json(
        {
            "apiVersion": 1,
            "seq": 1,
            "type": "start",
            "runId": None,
            "payload": {"clientRequestId": str(uuid4()), "code": 'x = input("Prompt: ")\n'},
        }
    )
    accepted = ws.receive_json()
    while True:
        message = ws.receive_json()
        if message["type"] == "input_request":
            return ws, accepted["runId"], message
        assert message["type"] != "run_finished"


def await_trace(client, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get("/api/runs/" + run_id + "/trace")
        if response.status_code == 200:
            trace = response.json()
            validate_trace(trace)
            return trace
        assert response.status_code == 409
        time.sleep(0.02)
    pytest.fail("sealed result not published")


def test_health_available(app_client):
    client, _ = app_client
    h = client.get("/api/health").json()
    assert h["available"] and h["reason"] is None


@pytest.mark.parametrize(
    "code,reason,inputs",
    [
        ("x = 42\r\n", "NONE", ()),
        ('print("<script>🙂</script>")\n', "NONE", ()),
        ("def broken(:\n", "SYNTAX_ERROR", ()),
        ("x = 1 / 0\n", "UNCAUGHT_EXCEPTION", ()),
        ('x = input("Empty: ")\nprint(repr(x))\n', "NONE", ("",)),
        ('x = input("Unicode: ")\nprint(x)\n', "NONE", ("  привет 🙂  ",)),
        ('a = input("A: ")\nb = input("B: ")\nprint(a, b)\n', "NONE", ("one", "  two 🙂  ")),
    ],
)
def test_actual_run_publication_get_release(app_client, code, reason, inputs):
    client, runtime = app_client
    summary, trace, messages = run(client, code, inputs)
    assert summary["completion"]["reason"] == reason
    console = [m["payload"]["entry"] for m in messages if m["type"] == "console"]
    assert console == trace["console"]
    if inputs:
        assert [e["text"] for e in console if e["kind"] == "input"] == [
            text + "\n" for text in inputs
        ]
    if 'print("<script>' in code:
        assert "".join(e["text"] for e in console) == "<script>🙂</script>\n"
    assert runtime.manager.availability.busy
    release(client, trace["runId"])
    release(client, trace["runId"])
    assert not runtime.manager.availability.busy
    assert client.get("/api/runs/" + trace["runId"] + "/trace").status_code == 404


def test_busy_before_release_and_second_run_after(app_client):
    client, runtime = app_client
    _, trace, _ = run(client, "x=1\n")
    rejected, _, _ = run(client, "x=2\n")
    assert rejected["payload"]["code"] == "RUN_BUSY"
    release(client, trace["runId"])
    _, second, _ = run(client, "x=2\n")
    assert second["runId"] != trace["runId"]
    release(client, second["runId"])
    assert not runtime.manager.availability.busy


def test_active_http_is_409_and_release_is_not_stop(app_client):
    client, _ = app_client
    ws, run_id, message = begin_waiting(client)
    try:
        assert client.get("/api/runs/" + run_id + "/trace").status_code == 409
        assert (
            client.post("/api/runs/" + run_id + "/release", headers={"Origin": ORIGIN}).status_code
            == 409
        )
        ws.send_json(
            {
                "apiVersion": 1,
                "seq": 2,
                "type": "input_response",
                "runId": run_id,
                "payload": {"requestId": message["payload"]["requestId"], "text": "continued"},
            }
        )
        while ws.receive_json()["type"] != "run_finished":
            pass
    finally:
        ws.__exit__(None, None, None)
    trace = await_trace(client, run_id)
    assert trace["completion"]["reason"] == "NONE"
    release(client, run_id)


def test_connection_loss_publishes_partial_trace(app_client):
    client, _ = app_client
    ws, run_id, _ = begin_waiting(client)
    ws.__exit__(None, None, None)
    trace = await_trace(client, run_id)
    assert trace["completion"]["reason"] == "CONNECTION_LOST"
    assert not trace["completion"]["traceComplete"]
    release(client, run_id)


def test_client_protocol_error_is_distinct(app_client):
    client, _ = app_client
    ws, run_id, _ = begin_waiting(client)
    try:
        ws.send_json(
            {
                "apiVersion": 1,
                "seq": 9,
                "type": "input_response",
                "runId": run_id,
                "payload": {"requestId": "q1", "text": "ignored"},
            }
        )
        while ws.receive_json()["type"] != "protocol_error":
            pass
    finally:
        ws.__exit__(None, None, None)
    trace = await_trace(client, run_id)
    assert trace["completion"]["reason"] == "CLIENT_PROTOCOL_ERROR"
    release(client, run_id)


def test_input_rejection_does_not_reset_deadline(app_client):
    client, _ = app_client
    ws, run_id, message = begin_waiting(client)
    try:
        ws.send_json(
            {
                "apiVersion": 1,
                "seq": 2,
                "type": "input_response",
                "runId": run_id,
                "payload": {"requestId": "q99", "text": "wrong"},
            }
        )
        rejected = ws.receive_json()
        assert (
            rejected["type"] == "input_rejected" and rejected["payload"]["code"] == "INVALID_INPUT"
        )
        ws.send_json(
            {
                "apiVersion": 1,
                "seq": 3,
                "type": "input_response",
                "runId": run_id,
                "payload": {"requestId": "q1", "text": "right"},
            }
        )
        while ws.receive_json()["type"] != "run_finished":
            pass
    finally:
        ws.__exit__(None, None, None)
    trace = await_trace(client, run_id)
    assert trace["completion"]["reason"] == "NONE"
    assert message["payload"]["deadlineAt"].endswith("Z")
    release(client, run_id)


def test_application_shutdown_cleans_active_run_and_results(tmp_path):
    config = AppConfig(image_path=ROOT / "backend/local-image.json", temp_root=tmp_path)
    runtime = Runtime(config, RunManager(adapter_factory=Docker))
    with TestClient(create_app(config, runtime=runtime), base_url=ORIGIN) as client:
        ws, run_id, _ = begin_waiting(client)
        client.portal.call(runtime.manager.shutdown)
        while ws.receive_json()["type"] != "run_finished":
            pass
        trace = await_trace(client, run_id)
        assert trace["completion"]["reason"] == "BACKEND_SHUTDOWN"
        ws.__exit__(None, None, None)
    assert not list(tmp_path.glob("codetrace-result-v1-*"))


def test_owned_containers_absent():
    async def check():
        from codetrace_backend.docker_adapter import ImageIdentity

        adapter = DockerAdapter(ImageIdentity.load(ROOT / "backend/local-image.json"))
        managed = await adapter.list_managed_containers()
        assert not {h.id for h in managed} & OWNED

    asyncio.run(check())
