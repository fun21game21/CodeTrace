import asyncio
import threading

import pytest
from codetrace_backend.trace_recorder import TraceRecorder

from .helpers import RUN_ID, FaultOps, records, wait_thread


def test_repeated_cancellation_while_rollback_is_running(tmp_path):
    class Recorder(TraceRecorder):
        def __init__(self):
            super().__init__(RUN_ID, temp_root=tmp_path, file_ops=FaultOps("commit.states"))
            self.started = threading.Event()
            self.proceed = threading.Event()
            self.calls = 0

        def _rollback(self):
            self.calls += 1
            if self.calls == 2:
                self.started.set()
                assert self.proceed.wait(5)
            super()._rollback()

    async def run():
        recorder = Recorder()
        before = recorder.counters
        task = asyncio.create_task(recorder.commit_chunk(records()))
        await wait_thread(recorder.started)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert not task.done()
        recorder.proceed.set()
        with pytest.raises(OSError):
            await task
        assert not recorder._poisoned and recorder.counters == before
        assert all(p.stat().st_size == 0 for p in recorder.directory.glob("*.ndjson"))
        await recorder.dispose()

    asyncio.run(run())
