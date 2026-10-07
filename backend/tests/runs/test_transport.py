import asyncio
from dataclasses import replace

import pytest
from codetrace_backend.app import create_app
from codetrace_backend.config import AppConfig, production_address
from codetrace_backend.runs import InputSubmission
from codetrace_backend.transport.http import TraceResponse
from codetrace_backend.transport.websocket import RunSocket
from codetrace_contracts import validate_ws
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .helpers import ORIGIN, WS, Access, FakeRuntime, accepted, envelope


@pytest.fixture
def runtime(tmp_path):
    return FakeRuntime(tmp_path)


@pytest.fixture
def client(runtime):
    with TestClient(create_app(runtime.config, runtime=runtime), base_url=ORIGIN) as client:
        yield client


def test_health_ready(client):
    health = client.get("/api/health").json()
    assert health["available"] and health["apiVersion"] == 1
    assert health["pythonVersion"] == "3.13.16"
    assert set(health) == {"available", "apiVersion", "reason", "pythonVersion", "limits"}


@pytest.mark.parametrize("reason", ["DOCKER_UNAVAILABLE", "IMAGE_UNAVAILABLE", "CLEANUP_FAILED"])
def test_health_unavailable(client, runtime, reason):
    runtime.manager.reason = reason
    assert client.get("/api/health").json()["reason"] == reason
    assert not client.get("/api/health").json()["available"]


@pytest.mark.parametrize("host", ["evil.example", "localhost:8000", "127.0.0.1", "127.0.0.1:8001"])
def test_exact_http_host(client, host):
    assert client.get("/api/health", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize(
    "origin", [None, "null", "https://evil.example", "http://localhost:8000", ORIGIN + "/"]
)
def test_ws_origin_rejected(client, origin):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS, headers={} if origin is None else {"Origin": origin}):
            pytest.fail("foreign origin accepted")


def test_ws_wrong_host(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS, headers={"Origin": ORIGIN, "Host": "evil.example"}):
            pytest.fail("foreign host accepted")


def test_socket_ready_first_and_seq(client):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ready = ws.receive_json()
        validate_ws(ready)
        assert ready["type"] == "socket_ready" and ready["runId"] is None and ready["seq"] == 1


def test_exact_source_registration_binding_order(client, runtime):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        run_id = accepted(ws)
        assert run_id != envelope()["payload"]["clientRequestId"]
        assert runtime.service.code == "x = 42\r\n"
        assert (
            runtime.log.index("register") < runtime.log.index("start") < runtime.log.index("bind")
        )
        assert runtime.log.index("observe:accepted") < runtime.log.index("bind")


@pytest.mark.parametrize(
    "code", ["RUN_BUSY", "DOCKER_UNAVAILABLE", "IMAGE_UNAVAILABLE", "INVALID_START"]
)
def test_rejected_start_abandons_recorder(client, runtime, code):
    runtime.manager.reject = code
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ws.receive_json()
        ws.send_json(envelope())
        rejected = ws.receive_json()
        assert rejected["type"] == "run_rejected" and rejected["runId"] is None
        assert rejected["payload"]["code"] == code
    assert "abandon" in runtime.log
    assert list(runtime.config.temp_root.glob("codetrace-result-v1-*")) == []


def test_busy_checked_before_recorder_creation(client, runtime):
    runtime.manager.busy = True
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ws.receive_json()
        ws.send_json(envelope())
        assert ws.receive_json()["payload"]["code"] == "RUN_BUSY"
    assert "register" not in runtime.log and "start" not in runtime.log


def test_code_bytes_reject_without_run(client, runtime):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ws.receive_json()
        ws.send_json(envelope(code="😀" * 16385))
        assert ws.receive_json()["payload"]["code"] == "CODE_TOO_LARGE"
    assert "register" not in runtime.log


@pytest.mark.parametrize(
    "variant",
    ["seq", "version", "runId", "unknown", "extra", "malformed", "duplicate-key", "binary"],
)
def test_invalid_before_accept_no_run(client, runtime, variant):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        ws.receive_json()
        message = envelope()
        if variant == "seq":
            message["seq"] = 2
        if variant == "version":
            message["apiVersion"] = 2
        if variant == "runId":
            message["runId"] = envelope()["payload"]["clientRequestId"]
        if variant == "unknown":
            message["type"] = "stop"
        if variant == "extra":
            message["extra"] = True
        if variant == "malformed":
            ws.send_text("not-json")
        elif variant == "duplicate-key":
            ws.send_text('{"seq":1,"seq":2}')
        elif variant == "binary":
            ws.send_bytes(b"{}")
        else:
            ws.send_json(message)
        assert ws.receive_json()["type"] == "protocol_error"
    assert "register" not in runtime.log


@pytest.mark.parametrize(
    "variant", ["duplicate-start", "wrong-run", "skipped-seq", "server-direction"]
)
def test_invalid_after_accept_client_protocol_error(client, runtime, variant):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        run_id = accepted(ws)
        if variant == "duplicate-start":
            message = envelope(seq=2)
        else:
            message = envelope("input_response", seq=2, run_id=run_id, requestId="q1", text="")
            if variant == "wrong-run":
                message["runId"] = envelope()["payload"]["clientRequestId"]
            if variant == "skipped-seq":
                message["seq"] = 3
            if variant == "server-direction":
                message = {
                    "apiVersion": 1,
                    "seq": 2,
                    "type": "status",
                    "runId": run_id,
                    "payload": {"status": "RUNNING"},
                }
        ws.send_json(message)
        assert ws.receive_json()["payload"]["code"] == "CLIENT_PROTOCOL_ERROR"
    assert "CLIENT_PROTOCOL_ERROR" in runtime.log
    assert "CONNECTION_LOST" not in runtime.log


@pytest.mark.parametrize(
    "status",
    [
        "RUNNING",
        "WAITING_INPUT",
        "FINALIZING",
        "SUCCEEDED",
        "FAILED",
        "LIMIT_EXCEEDED",
        "INTERRUPTED",
    ],
)
def test_status_forwarding(client, runtime, status):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        accepted(ws)
        client.portal.call(runtime.manager.emit, "status", status)
        message = ws.receive_json()
        validate_ws(message)
        assert (
            message["type"] == "status"
            and message["payload"]["status"] == status
            and message["seq"] == 4
        )


def test_console_plain_exact_forwarding(client, runtime):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        accepted(ws)
        entry = {"index": 0, "kind": "stdout", "text": "<script>🙂</script>\n", "requestId": None}
        client.portal.call(runtime.manager.emit, "console", entry)
        assert ws.receive_json()["payload"]["entry"] == entry


@pytest.mark.parametrize("text", ["", "  ", " привет 🙂 "])
def test_input_request_response_ack_exact(client, runtime, text):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        run_id = accepted(ws)
        client.portal.call(runtime.manager.emit, "status", "WAITING_INPUT")
        ws.receive_json()
        client.portal.call(
            runtime.manager.emit,
            "input_requested",
            {"requestId": "q1", "prompt": "Ответ: ", "consoleOffset": 0},
        )
        request = ws.receive_json()
        validate_ws(request)
        assert request["payload"]["deadlineAt"].endswith("Z")
        ws.send_json(envelope("input_response", seq=2, run_id=run_id, requestId="q1", text=text))
        assert ws.receive_json()["payload"]["status"] == "RUNNING"
        assert ws.receive_json()["type"] == "input_accepted"
        assert runtime.manager.responses == [(run_id, "q1", text)]
        client.portal.call(
            runtime.manager.emit,
            "console",
            {"index": 0, "kind": "input", "text": text + "\n", "requestId": "q1"},
        )
        assert ws.receive_json()["type"] == "console"  # no second ACK


@pytest.mark.parametrize("code", ["INVALID_INPUT", "INPUT_ALREADY_ACCEPTED"])
def test_input_rejected_uses_watchdog_result(client, runtime, code):
    runtime.manager.input_result = InputSubmission(False, code)
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        run_id = accepted(ws)
        ws.send_json(envelope("input_response", seq=2, run_id=run_id, requestId="q1", text="x"))
        result = ws.receive_json()
        assert result["type"] == "input_rejected" and result["payload"]["code"] == code


def test_publication_wait_and_single_finished(client, runtime):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        accepted(ws)
        client.portal.call(runtime.manager.emit, "status", "FINALIZING")
        assert ws.receive_json()["payload"]["status"] == "FINALIZING"
        client.portal.call(runtime.manager.emit, "status", "SUCCEEDED")
        assert ws.receive_json()["payload"]["status"] == "SUCCEEDED"
        assert not runtime.service.future.done()
        client.portal.call(runtime.service.publish)
        message = ws.receive_json()
        validate_ws(message)
        assert message["type"] == "run_finished" and set(message["payload"]) == {
            "completion",
            "traceAvailable",
            "traceUrl",
            "traceBytes",
            "eventCount",
        }
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_disconnect_calls_public_connection_lost(client, runtime):
    with client.websocket_connect(WS, headers={"Origin": ORIGIN}) as ws:
        accepted(ws)
    client.portal.call(asyncio.sleep, 0)
    assert "CONNECTION_LOST" in runtime.log


def test_queue_overflow_does_not_disable_recorder_observation(runtime):
    async def scenario():
        channel = RunSocket(None, runtime, queue_size=1)
        channel.enqueue("socket_ready", {"apiVersion": 1, "limits": runtime.config.limits})
        channel.run_id = envelope()["payload"]["clientRequestId"]
        from codetrace_backend.runs import LifecycleEvent

        channel.observe(
            LifecycleEvent("status", envelope()["payload"]["clientRequestId"], "RUNNING")
        )
        channel.observe(
            LifecycleEvent("status", envelope()["payload"]["clientRequestId"], "FINALIZING")
        )
        assert channel.failed.is_set() and runtime.log.count("observe:status") == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [409, 404])
def test_get_trace_domain_status(client, runtime, status):
    runtime.service.access_status = status
    assert client.get("/api/runs/any/trace").status_code == status


def test_get_trace_exact_bytes_twice_content_type_ttl_reader_closed(client, runtime):
    first = client.get("/api/runs/any/trace")
    second = client.get("/api/runs/any/trace")
    assert first.content == second.content == runtime.service.data
    assert first.headers["content-type"] == "application/json"
    assert all(access.closed for access in runtime.service.accesses)
    assert runtime.service.deadline == 123.0
    assert "access-control-allow-origin" not in first.headers


@pytest.mark.parametrize("status", [204, 409])
def test_release_domain_status_idempotence(client, runtime, status):
    runtime.service.release_status = status
    assert client.post("/api/runs/any/release", headers={"Origin": ORIGIN}).status_code == status
    assert client.post("/api/runs/any/release", headers={"Origin": ORIGIN}).status_code == status


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example"])
def test_release_origin_rejected(client, origin):
    assert (
        client.post(
            "/api/runs/any/release", headers={} if origin is None else {"Origin": origin}
        ).status_code
        == 403
    )


def test_static_assets_spa_and_api_404(client, runtime):
    root = runtime.config.frontend_dist
    root.mkdir()
    (root / "index.html").write_text("<html>CodeTrace</html>")
    (root / "app.js").write_text("hello")
    assert client.get("/").text == "<html>CodeTrace</html>"
    assert client.get("/app.js").text == "hello"
    assert client.get("/some/route").text == "<html>CodeTrace</html>"
    api = client.get("/api/not-found")
    assert api.status_code == 404 and "html" not in api.headers["content-type"]
    assert client.get("/missing.js").status_code == 404


@pytest.mark.parametrize("failure", ["cancel", "disconnect", "iterator-failure"])
def test_reader_closed_even_before_first_chunk(failure):
    async def scenario():
        access = Access(b"{}")
        response = TraceResponse(access)

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if failure == "cancel":
                raise asyncio.CancelledError
            if failure == "disconnect":
                raise OSError
            if failure == "iterator-failure":
                raise RuntimeError

        with pytest.raises(BaseException):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert access.closed

    asyncio.run(scenario())


def test_explicit_dev_origin_and_fixed_production_address(runtime):
    config = replace(runtime.config, development=True)
    with TestClient(create_app(config, runtime=runtime), base_url=ORIGIN) as client:
        with client.websocket_connect(WS, headers={"Origin": "http://127.0.0.1:5173"}) as ws:
            assert ws.receive_json()["type"] == "socket_ready"
    assert production_address() == ("127.0.0.1", 8000)
    assert AppConfig().origins == frozenset([ORIGIN])


def test_missing_image_serves_unavailable_health(tmp_path):
    with TestClient(
        create_app(AppConfig(image_path=tmp_path / "missing.json", temp_root=tmp_path)),
        base_url=ORIGIN,
    ) as client:
        assert client.get("/api/health").json()["reason"] == "IMAGE_UNAVAILABLE"


def test_shutdown_manager_before_service(runtime):
    asyncio.run(runtime.shutdown())
    assert runtime.log[-2:] == ["manager.shutdown", "service.shutdown"]
