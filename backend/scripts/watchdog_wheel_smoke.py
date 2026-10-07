"""Run the installed WATCHDOG wheel outside the checkout against the actual RUNNER_IO image."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import codetrace_backend
from codetrace_backend.docker_adapter import ImageIdentity
from codetrace_backend.runs import RunManager


class Sink:
    def __init__(self):
        self.events = 0
        self.outputs = 0

    async def commit_chunk(self, records):
        self.events += sum(item["kind"] == "event" for item in records)

    async def commit_console(self, entry):
        self.outputs += 1


async def main():
    target = Path(sys.argv[1]).resolve()
    assert Path(codetrace_backend.__file__).resolve().is_relative_to(target)
    manager = RunManager()
    sink = Sink()
    lease = await manager.start_run(
        run_id=str(uuid4()),
        code="print(42)",
        image=ImageIdentity.load(Path(sys.argv[2])),
        sink=sink,
        subscriber=lambda event: None,
    )
    try:
        result = await asyncio.wait_for(lease.session.await_result(), 20)
        assert result.reason == "NONE" and result.cleanupConfirmed
        assert sink.events > 0 and sink.outputs > 0
        lease.finish_ownership()
        assert not manager.availability.busy
        print(
            "Installed WATCHDOG wheel: real Runner SUCCESS, records, output, "
            "cleanup and slot handoff passed"
        )
    finally:
        await manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
