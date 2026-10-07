"""Opt-in real unchanged DOCKER → WATCHDOG → RECORDER bytes → separate Node TRACE process."""

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from codetrace_backend.docker_adapter import DockerAdapter, ImageIdentity
from codetrace_backend.runs import RunManager
from codetrace_backend.trace_recorder import ResultService, TraceRecorder

from frontend.tests.trace.generate_fixtures import expected_steps

ROOT = Path(__file__).resolve().parents[3]
IMAGE = ImageIdentity.load(ROOT / "backend/local-image.json")
SESSION = os.environ.get("CODETRACE_TRACE_TEST_SESSION", str(uuid4()))
NODE = "node"
DIRECTORIES = set()


class Docker(DockerAdapter):
    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = ["--label", "codetrace.trace_test=" + SESSION]
        return tuple(argv)


@pytest.mark.parametrize(
    "name,code,answer,reason",
    [
        ("assignment_list", "x=42\na=[1]\na.append(x)", None, "NONE"),
        ("recursion", "def f(n):\n if n<2: return 1\n return n*f(n-1)\nr=f(5)", None, "NONE"),
        ("console", "print('A',end='')\nprint('B')", None, "NONE"),
        ("input", "x=input('Q:')\nprint(x)", "  😀  ", "NONE"),
        ("user_error", "x=1\n1/0", None, "UNCAUGHT_EXCEPTION"),
        ("syntax", "if :", None, "SYNTAX_ERROR"),
        ("partial", "x=input('Wait:')", "disconnect", "CONNECTION_LOST"),
    ],
)
def test_real_sealed_document_every_step_in_node(name, code, answer, reason):
    async def run():
        manager = RunManager(adapter_factory=Docker)
        service = ResultService(clock=manager.clock)
        recorder = TraceRecorder(str(uuid4()))
        DIRECTORIES.add(recorder.directory)
        service.register(recorder, code)
        queue = asyncio.Queue(maxsize=10000)

        def subscriber(event):
            service.observe(event)
            queue.put_nowait(event)

        try:
            lease = await manager.start_run(
                run_id=recorder.runId, code=code, image=IMAGE, sink=recorder, subscriber=subscriber
            )
            handle = service.bind(lease)
            if answer is not None:
                async with asyncio.timeout(10):
                    while True:
                        event = await queue.get()
                        if event.kind == "input_requested":
                            break
                        assert event.kind != "result"
                if answer == "disconnect":
                    manager.connection_lost(recorder.runId)
                else:
                    assert (
                        await manager.submit_input(
                            recorder.runId, event.payload["requestId"], answer
                        )
                    ).accepted
            execution = await asyncio.wait_for(lease.session.await_result(), 20)
            result = await asyncio.wait_for(handle.await_result(), 20)
            assert execution.cleanupConfirmed and result.traceAvailable
            assert result.completion["reason"] == reason
            assert await lease.session.adapter.inspect(lease.session.container) is None
            with service.get_trace(recorder.runId) as access:
                parts = []  # Small integration inputs, not the production recorder.
                while part := access.read():
                    parts.append(part)
            raw = b"".join(parts)
            assert len(raw) == result.traceBytes
            document = json.loads(raw)
            expected = expected_steps(document)
            final = expected[-1]["state"]
            values = {b["name"]: b["value"] for b in final["globals"]}
            if name == "assignment_list":
                assert values["x"]["value"] == "42"
                assert next(o for o in final["objects"] if o["id"] == values["a"]["$ref"])[
                    "items"
                ] == [
                    {"kind": "scalar", "type": "int", "value": "1"},
                    {"kind": "scalar", "type": "int", "value": "42"},
                ]
            if name == "recursion":
                assert values["r"]["value"] == "120"
            if name == "input":
                assert values["x"]["value"] == answer
            if name not in {"partial", "syntax"}:
                assert final["stack"] == final["frames"] == []
            with tempfile.TemporaryDirectory(prefix="codetrace-trace-sealed-") as temporary:
                trace_path, oracle_path = (
                    Path(temporary) / "trace.json",
                    Path(temporary) / "expected.json",
                )
                trace_path.write_bytes(raw)  # Exact RECORDER bytes, not reserialized DTO.
                oracle_path.write_text(json.dumps(expected, ensure_ascii=True))
                completed = await asyncio.to_thread(
                    subprocess.run,
                    [
                        NODE,
                        str(ROOT / "frontend/tests/trace/verify_sealed.ts"),
                        str(trace_path),
                        str(oracle_path),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=40,
                )
                assert completed.returncode == 0, completed.stderr + completed.stdout
                report = json.loads(completed.stdout)
                assert report["comparisons"] == len(expected) * 3
                print(json.dumps(dict(scenario=name, **report)))
            await service.release(recorder.runId)
            assert not manager.availability.busy
        finally:
            await manager.shutdown()
            await service.shutdown()
            assert not recorder.directory.exists()

    asyncio.run(run())


def test_no_owned_runtime_artifacts():
    assert all(not p.exists() for p in DIRECTORIES)

    async def check():
        output = await Docker(IMAGE)._command(
            (
                "container",
                "ls",
                "--all",
                "--filter",
                "label=codetrace.trace_test=" + SESSION,
                "--format",
                "{{.ID}}",
            )
        )
        assert not output.strip()

    asyncio.run(check())
