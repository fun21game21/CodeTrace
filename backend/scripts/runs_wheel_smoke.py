"""Installed RUNS wheel outside source import paths, using actual Docker."""

import json
import sys
from pathlib import Path
from uuid import uuid4

import codetrace_backend
from codetrace_backend.app import create_app
from codetrace_backend.config import AppConfig
from codetrace_contracts import validate_trace
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:8000"


def main():
    target = Path(sys.argv[1]).resolve()
    assert Path(codetrace_backend.__file__).resolve().is_relative_to(target)
    config = AppConfig(
        image_path=ROOT / "backend/local-image.json", frontend_dist=ROOT / "frontend/dist"
    )
    with TestClient(create_app(config), base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["available"]
        with client.websocket_connect(
            "ws://127.0.0.1:8000/api/runs", headers={"Origin": ORIGIN}
        ) as ws:
            assert ws.receive_json()["type"] == "socket_ready"
            code = "x = 42\nprint(x)\n"
            ws.send_json(
                {
                    "apiVersion": 1,
                    "seq": 1,
                    "type": "start",
                    "runId": None,
                    "payload": {"clientRequestId": str(uuid4()), "code": code},
                }
            )
            accepted = ws.receive_json()
            assert accepted["type"] == "run_accepted"
            while True:
                message = ws.receive_json()
                if message["type"] == "run_finished":
                    break
        summary = message["payload"]
        assert summary["completion"]["outcome"] == "SUCCESS"
        raw = client.get(summary["traceUrl"])
        assert len(raw.content) == summary["traceBytes"]
        trace = json.loads(raw.content)
        validate_trace(trace)
        assert trace["sourceCode"] == code
        assert (
            client.post(
                "/api/runs/" + accepted["runId"] + "/release", headers={"Origin": ORIGIN}
            ).status_code
            == 204
        )
        assert not client.app.state.runtime.manager.availability.busy
    print("Installed RUNS wheel: actual WS/Docker/sealed GET/release/static frontend passed.")


if __name__ == "__main__":
    main()
