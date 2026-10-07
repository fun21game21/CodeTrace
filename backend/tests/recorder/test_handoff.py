import asyncio
from uuid import uuid4

import pytest
from codetrace_backend.runs import RunManager, RunRejected
from codetrace_backend.trace_recorder import ResultAccessError, ResultService, TraceRecorder
from codetrace_contracts import default_limits
from watchdog.helpers import IMAGE, FakeClock, FakeDocker, checkpoint, event


@pytest.mark.parametrize("dispose", ["release", "ttl"])
def test_real_slot_lease_handoff_with_fake_container(tmp_path, dispose):
    async def run():
        clock = FakeClock()
        config = default_limits()
        fake = FakeDocker(limits=config)
        manager = RunManager(clock=clock, adapter_factory=lambda image, limits: fake)
        service = ResultService(clock=clock)
        run_id = "12345678-1234-4234-8234-123456789abc"
        recorder = TraceRecorder(run_id, config, temp_root=tmp_path)
        service.register(recorder, "x=1")
        lease = await manager.start_run(
            run_id=run_id, code="x=1", image=IMAGE, sink=recorder, subscriber=service.observe
        )
        handle = service.bind(lease)
        async with asyncio.timeout(3):
            while lease.session.status != "RUNNING":
                await asyncio.sleep(0)
        fake.att.send("trace_chunk", {"records": [checkpoint(), event()]})
        fake.att.send(
            "finished",
            {
                "outcome": "SUCCESS",
                "reason": "NONE",
                "primaryDiagnostic": None,
                "finalStateVersion": 0,
            },
        )
        fake.end()
        assert (await handle.await_result()).traceAvailable
        assert manager.availability.busy
        with pytest.raises(RunRejected) as error:
            await manager.start_run(
                run_id=str(uuid4()), code="", image=IMAGE, sink=recorder, subscriber=lambda e: None
            )
        assert error.value.code == "RUN_BUSY" and fake.created == 1
        if dispose == "release":
            await service.release(run_id)
        else:
            clock.advance(301)
            with pytest.raises(ResultAccessError) as error:
                service.get_trace(run_id)
            assert error.value.status == 404
            async with asyncio.timeout(3):
                while manager.availability.busy:
                    await asyncio.sleep(0.001)
        assert not manager.availability.busy and not recorder.directory.exists()
        # Stale release must not affect a newly accepted slot.
        next_id = str(uuid4())
        next_recorder = TraceRecorder(next_id, temp_root=tmp_path)
        fake2 = FakeDocker()
        manager._factory = lambda image, limits: fake2
        next_lease = await manager.start_run(
            run_id=next_id, code="", image=IMAGE, sink=next_recorder, subscriber=lambda e: None
        )
        assert await service.release(run_id) == 204
        assert manager.availability.busy
        await manager.shutdown()
        await next_recorder.dispose()
        next_lease.finish_ownership()

    asyncio.run(run())
