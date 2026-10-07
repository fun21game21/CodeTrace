"""Installed RECORDER package, outside source checkout, with the actual RUNNER_IO image."""

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import codetrace_backend.trace_recorder
from codetrace_backend.docker_adapter import DockerAdapter, ImageIdentity
from codetrace_backend.runs import RunManager
from codetrace_backend.trace_recorder import ResultService, TraceRecorder
from codetrace_contracts import validate_contract


class Docker(DockerAdapter):
    def create_argv(self, run_id, token):
        argv = list(super().create_argv(run_id, token))
        argv[-1:-1] = [
            "--label",
            "codetrace.recorder_test=" + os.environ["CODETRACE_RECORDER_TEST_SESSION"],
        ]
        return tuple(argv)


async def main():
    target = Path(sys.argv[1]).resolve()
    assert Path(codetrace_backend.trace_recorder.__file__).resolve().is_relative_to(target)
    manager = RunManager(adapter_factory=Docker)
    service = ResultService(clock=manager.clock)
    run_id = str(uuid4())
    recorder = TraceRecorder(run_id)
    service.register(recorder, "print(42)")
    lease = await manager.start_run(
        run_id=run_id,
        code="print(42)",
        image=ImageIdentity.load(Path(sys.argv[2])),
        sink=recorder,
        subscriber=service.observe,
    )
    handle = service.bind(lease)
    try:
        summary = await asyncio.wait_for(handle.await_result(), 20)
        assert summary.traceAvailable and summary.completion["reason"] == "NONE"
        parts = []
        with service.get_trace(run_id) as access:
            while part := access.read():
                parts.append(part)
        raw = b"".join(parts)
        document = json.loads(raw)
        validate_contract(document, "trace")
        assert len(raw) == summary.traceBytes
        assert "".join(e["text"] for e in document["console"]) == "42\n"
        assert manager.availability.busy
        await service.release(run_id)
        assert not manager.availability.busy and not recorder.directory.exists()
        print(
            "Installed RECORDER wheel: real Runner, sealed trace, GET, release and cleanup passed"
        )
    finally:
        await manager.shutdown()
        await service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
