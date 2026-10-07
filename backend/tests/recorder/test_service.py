import asyncio

import pytest
from codetrace_backend.trace_recorder import RecorderError, ResultAccessError
from codetrace_contracts import default_limits, validate_contract

from .helpers import RUN_ID, FaultOps, document, read_all, records, setup, terminal, wait_thread


def test_get_same_bytes_release_slot_once_and_no_tombstone(tmp_path):
    async def run():
        service, recorder, lease, clock, handle = await setup(tmp_path)
        result = await handle.await_result()
        assert result.traceAvailable and result.eventCount == 3
        validate_contract(
            {
                "apiVersion": 1,
                "seq": 1,
                "runId": RUN_ID,
                "type": "run_finished",
                "payload": result.as_dict(),
            },
            "ws",
        )
        retained = service.retained
        assert retained.lease is lease and retained.expiresAt == clock.now() + 300
        assert retained.sealedAt == clock.utc()
        first = read_all(service.get_trace(RUN_ID))
        assert first == read_all(service.get_trace(RUN_ID)) == retained.path.read_bytes()
        assert result.traceBytes == len(first)
        result.completion["reason"] = "RUNNER_FAILURE"
        retained.completion["reason"] = "RUNNER_FAILURE"
        assert service.retained.completion["reason"] == "NONE"
        assert lease.calls == 0
        assert await service.release("stale") == 204 and lease.calls == 0
        assert await service.release(RUN_ID) == 204
        assert await service.release(RUN_ID) == 204
        assert lease.calls == 1
        assert not recorder.directory.exists() and service.retained is None
        with pytest.raises(ResultAccessError) as error:
            service.get_trace(RUN_ID)
        assert error.value.status == 404
        assert service._entry is None

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["two_release", "ttl_release", "ttl_get", "expiry_release"])
def test_ttl_and_release_races(tmp_path, mode):
    async def run():
        service, recorder, lease, clock, handle = await setup(tmp_path)
        summary = await handle.await_result()
        clock.advance(299.999)
        read_all(service.get_trace(RUN_ID))
        assert service.retained.expiresAt == 300
        if mode == "two_release":
            assert await asyncio.gather(service.release(RUN_ID), service.release(RUN_ID)) == [
                204,
                204,
            ]
        else:
            clock.advance(0.002)
            if mode == "ttl_get":
                with pytest.raises(ResultAccessError) as error:
                    service.get_trace(RUN_ID)
                assert error.value.status == 404
            else:
                assert await service.release(RUN_ID) == 204
        async with asyncio.timeout(3):
            while recorder.directory.exists() or lease.calls == 0:
                await asyncio.sleep(0.001)
        assert lease.calls == 1
        assert summary.completion == (await handle.await_result()).completion
        clock.advance(1000)
        await asyncio.sleep(0)
        assert lease.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("action", ["release", "ttl"])
def test_get_open_before_disposal_safe_bounded_and_pins_lease(tmp_path, action):
    async def run():
        service, recorder, lease, clock, handle = await setup(tmp_path)
        await handle.await_result()
        access = service.get_trace(RUN_ID)
        second = service.get_trace(RUN_ID)
        with pytest.raises(ResultAccessError) as error:
            service.get_trace(RUN_ID)
        assert error.value.status == 409
        expected = service.retained.path.read_bytes()
        second.close()
        if action == "release":
            await service.release(RUN_ID)
        else:
            clock.advance(301)
            async with asyncio.timeout(3):
                while recorder.directory.exists():
                    await asyncio.sleep(0.001)
        assert not recorder.directory.exists() and lease.calls == 0
        with pytest.raises(ResultAccessError) as error:
            service.get_trace(RUN_ID)
        assert error.value.status == 404
        assert read_all(access) == expected
        access.close()
        assert lease.calls == 1
        assert service._entry is None

    asyncio.run(run())


@pytest.mark.parametrize("action", ["get", "release", "cancel_waiter"])
def test_unsealed_conflict_and_waiter_cancellation(tmp_path, action):
    async def run():
        ops = FaultOps("seal.header", gate=True)
        service, recorder, lease, _, handle = await setup(tmp_path, ops=ops)
        await wait_thread(ops.started)
        if action == "get":
            with pytest.raises(ResultAccessError) as error:
                service.get_trace(RUN_ID)
            assert error.value.status == 409
        elif action == "release":
            with pytest.raises(ResultAccessError) as error:
                await service.release(RUN_ID)
            assert error.value.status == 409
        else:
            waiter = asyncio.create_task(handle.await_result())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert lease.calls == 0
        ops.proceed.set()
        assert (await handle.await_result()).traceAvailable
        await service.release(RUN_ID)

    asyncio.run(run())


@pytest.mark.parametrize(
    "reason,cleanup,usable",
    [
        ("CLEANUP_FAILED", False, False),
        ("RUNNER_FAILURE", True, False),
        ("FINALIZATION_TIMEOUT", True, False),
        ("TRACE_RECORDER_FAILURE", True, False),
    ],
)
def test_unusable_prefix_no_download_ownership_recovery(tmp_path, reason, cleanup, usable):
    async def run():
        execution = terminal(
            outcome="SYSTEM_ERROR", reason=reason, cleanupConfirmed=cleanup, traceUsable=usable
        )
        service, recorder, lease, _, handle = await setup(tmp_path, result=execution)
        summary = await handle.await_result()
        assert not summary.traceAvailable
        assert summary.traceBytes is summary.traceUrl is summary.eventCount is None
        assert summary.completion["reason"] == reason
        assert not recorder.directory.exists()
        assert lease.calls == (1 if cleanup else 0)
        if not cleanup:
            lease._manager.availability.available = False
            with pytest.raises(RecorderError):
                service.recover_failed_ownership(lease._manager)
            lease._manager.availability.available = True
            service.recover_failed_ownership(lease._manager)
            service.recover_failed_ownership(lease._manager)
            assert lease.calls == 1
        with pytest.raises(ResultAccessError) as error:
            service.get_trace(RUN_ID)
        assert error.value.status == 404

    asyncio.run(run())


@pytest.mark.parametrize(
    "reason",
    [
        "ACTIVE_TIMEOUT",
        "INPUT_IDLE_TIMEOUT",
        "CONTAINER_LIFETIME",
        "MEMORY_LIMIT",
        "CONNECTION_LOST",
        "BACKEND_SHUTDOWN",
        "EVENT_LIMIT",
    ],
)
def test_forced_prefix_sealed_last_observed(tmp_path, reason):
    async def run():
        outcome = (
            "INTERRUPTED" if reason in {"CONNECTION_LOST", "BACKEND_SHUTDOWN"} else "LIMIT_EXCEEDED"
        )
        execution = terminal(
            outcome=outcome,
            reason=reason,
            finalStateVersion=None,
            exitCode=137,
            oomKilled=reason == "MEMORY_LIMIT",
        )
        service, _, lease, _, handle = await setup(
            tmp_path, result=execution, prefix=records()[:-1]
        )
        summary = await handle.await_result()
        assert summary.traceAvailable
        d = document(service.retained.path)
        assert d["completion"]["stateAccuracy"] == "last_observed"
        assert not d["completion"]["traceComplete"]
        assert d["events"][-1]["stateVersion"] == 1
        await service.release(RUN_ID)
        assert lease.calls == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "stage", ["seal.header", "seal.array", "seal.completion", "fsync", "replace"]
)
def test_publication_failure_does_not_rewrite_execution_success(tmp_path, stage):
    async def run():
        execution = terminal()
        service, recorder, lease, _, handle = await setup(
            tmp_path, result=execution, ops=FaultOps(stage)
        )
        summary = await handle.await_result()
        assert not summary.traceAvailable
        assert summary.completion["outcome"] == "SYSTEM_ERROR"
        assert summary.completion["reason"] == "TRACE_RECORDER_FAILURE"
        assert execution.outcome == "SUCCESS" and execution.reason == "NONE"
        assert lease.calls == 1 and not recorder.directory.exists()
        validate_contract(summary.completion, "trace", "Completion")

    asyncio.run(run())


def test_sealing_uses_original_finalization_deadline(tmp_path):
    async def run():
        limits = default_limits()
        limits["finalization_timeout_ms"] = 100
        ops = FaultOps("seal.header", gate=True)
        service, recorder, lease, clock, handle = await setup(tmp_path, ops=ops, limits=limits)
        await wait_thread(ops.started)
        clock.advance(0.101)
        ops.proceed.set()
        result = await handle.await_result()
        assert result.completion["reason"] == "FINALIZATION_TIMEOUT"
        assert not result.traceAvailable and lease.calls == 1
        assert not recorder.directory.exists()

    asyncio.run(run())


def test_shutdown_removes_terminal_data(tmp_path):
    async def run():
        service, recorder, lease, _, handle = await setup(tmp_path)
        await handle.await_result()
        await service.shutdown()
        await service.shutdown()
        assert lease.calls == 1 and not recorder.directory.exists()

    asyncio.run(run())


@pytest.mark.parametrize("size", [0, -1, 65537, True])
def test_get_access_bounded_reads(tmp_path, size):
    async def run():
        service, _, _, _, handle = await setup(tmp_path)
        await handle.await_result()
        with service.get_trace(RUN_ID) as access:
            assert access.contentType == "application/json"
            with pytest.raises(ValueError):
                access.read(size)
        await service.release(RUN_ID)

    asyncio.run(run())
