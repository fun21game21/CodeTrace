import asyncio

import pytest
from codetrace_backend.runs import RunManager, RunRejected
from codetrace_contracts import default_limits

from .helpers import (
    IMAGE,
    RUN_ID,
    FakeClock,
    FakeDocker,
    Sink,
    checkpoint,
    diagnostic,
    event,
    finish,
    pump,
    setup,
)


def test_success_lifecycle_slot_and_handoff():
    async def scenario():
        manager, lease, fake, sink, events = await setup()
        assert lease.session.status == "RUNNING" and fake.created == 1
        with pytest.raises(RunRejected, match="RUN_BUSY"):
            await manager.start_run(
                run_id=RUN_ID, code="", image=IMAGE, sink=sink, subscriber=events.append
            )
        with pytest.raises(RunRejected):
            lease.finish_ownership()
        result = await finish(fake, lease.session)
        assert result.reason == "NONE" and result.cleanupConfirmed
        assert [e.payload for e in events if e.kind == "status"] == [
            "RUNNING",
            "FINALIZING",
            "SUCCEEDED",
        ]
        assert sum(e.kind == "result" for e in events) == 1
        assert fake.killed == 0 and fake.cleaned == 1 and fake.att.closed and fake.att.stdin.closed
        assert manager.availability.busy
        lease.finish_ownership()
        assert not manager.availability.busy
        lease.finish_ownership()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason,status",
    [
        ("ACTIVE_TIMEOUT", "LIMIT_EXCEEDED"),
        ("CONNECTION_LOST", "INTERRUPTED"),
        ("BACKEND_SHUTDOWN", "INTERRUPTED"),
        ("RUNNER_FAILURE", "FAILED"),
    ],
)
def test_termination_once(reason, status):
    async def scenario():
        manager, lease, fake, sink, events = await setup()
        s = lease.session
        assert s.terminate(reason)
        task = s._finalization
        assert not s.terminate("CONNECTION_LOST") and task is s._finalization
        result = await s.await_result()
        assert result.reason == reason and result.terminalStatus == status
        assert fake.killed == fake.cleaned == 1
        assert sum(e.kind == "result" for e in events) == 1
        manager.connection_lost(RUN_ID)
        assert (await s.await_result()) is result

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["SYNTAX_ERROR", "UNCAUGHT_EXCEPTION", "UNSUPPORTED_FEATURE"])
def test_user_error(reason):
    async def scenario():
        _, lease, fake, _, _ = await setup()
        result = await finish(fake, lease.session, reason=reason)
        assert result.outcome == "USER_ERROR" and result.terminalStatus == "FAILED"
        assert result.reason == reason and result.traceUsable

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code,oom,reason",
    [(0, False, "RUNNER_FAILURE"), (127, False, "RUNNER_FAILURE"), (137, True, "MEMORY_LIMIT")],
)
def test_exit_without_finished(code, oom, reason):
    async def scenario():
        _, lease, fake, _, _ = await setup()
        fake.end(code, oom)
        result = await lease.session.await_result()
        assert result.reason == reason and result.oomKilled == oom and result.exitCode == code
        assert result.cleanupConfirmed and fake.cleaned == 1

    asyncio.run(scenario())


def test_input_two_responses_exact_budget():
    async def scenario():
        clock = FakeClock()
        manager, lease, fake, sink, events = await setup(clock=clock)
        s = lease.session
        clock.advance(2)
        fake.att.send("input_request", {"requestId": "q1", "prompt": "A:", "consoleOffset": 0})
        await pump()
        clock.advance(50)
        await pump()
        assert s.status == "WAITING_INPUT" and s.timing.elapsed() == (2000, 50000)
        rejected = [
            ("q2", "x", "INVALID_INPUT"),
            ("q1", "x\n", "INVALID_INPUT"),
            ("q1", "x" * 8193, "INVALID_INPUT"),
        ]
        for request, text, code in rejected:
            before = s.timing.deadline()
            assert (await manager.submit_input(RUN_ID, request, text)).code == code
            assert s.timing.deadline() == before
        assert (await manager.submit_input(RUN_ID, "q1", "  ")).accepted
        assert (await manager.submit_input(RUN_ID, "q1", "again")).code == "INPUT_ALREADY_ACCEPTED"
        clock.advance(3)
        fake.att.send("input_request", {"requestId": "q2", "prompt": "B:", "consoleOffset": 0})
        await pump()
        clock.advance(60)
        await pump()
        assert (await manager.submit_input(RUN_ID, "q2", "")).accepted
        clock.advance(1)
        result = await finish(fake, s)
        assert result.activeElapsedMs == 6000 and result.waitingElapsedMs == 110000
        assert [m["seq"] for m in fake.att.stdin.messages] == [1, 2, 3]
        assert s.pendingInput is None
        assert (await manager.submit_input(RUN_ID, "q3", "")).code == "INVALID_INPUT"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "phase,advance,reason",
    [
        ("starting", 16, "STARTUP_TIMEOUT"),
        ("running", 11, "ACTIVE_TIMEOUT"),
        ("waiting", 121, "INPUT_IDLE_TIMEOUT"),
        ("lifetime", 301, "CONTAINER_LIFETIME"),
    ],
)
def test_independent_watchdog(phase, advance, reason):
    async def scenario():
        clock = FakeClock()
        _, lease, fake, _, _ = await setup(
            clock=clock,
            ready=phase != "starting",
            input_idle_timeout_ms=400000 if phase == "lifetime" else 120000,
        )
        if phase in ("waiting", "lifetime"):
            fake.att.send("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
            await pump()
        clock.advance(advance)
        await pump()
        result = await lease.session.await_result()
        assert result.reason == reason and result.cleanupConfirmed and fake.killed == 1
        if phase == "starting":
            assert fake.att.stdin.messages == []

    asyncio.run(scenario())


@pytest.mark.parametrize("during", ["STARTING", "RUNNING", "WAITING_INPUT"])
def test_disconnect_and_shutdown(during):
    async def scenario():
        manager, lease, fake, _, _ = await setup(ready=during != "STARTING")
        if during == "WAITING_INPUT":
            fake.att.send("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
            await pump()
        manager.connection_lost("other")
        assert lease.session.status == during
        manager.connection_lost(RUN_ID)
        result = await manager.shutdown()
        assert result.reason == "CONNECTION_LOST" and not manager.availability.available
        assert lease.session.pendingInput is None

    asyncio.run(scenario())


def test_shutdown_first():
    async def scenario():
        manager, lease, fake, _, _ = await setup()
        result = await manager.shutdown()
        assert result.reason == "BACKEND_SHUTDOWN" and result.cleanupConfirmed

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["abort_request", "fatal"])
def test_runner_terminal_control(kind):
    async def scenario():
        _, lease, fake, _, _ = await setup()
        reason = "EVENT_LIMIT" if kind == "abort_request" else "RUNNER_FAILURE"
        diag = diagnostic(reason)
        diag["category"] = "limit" if kind == "abort_request" else "infrastructure"
        fake.att.send(kind, {"reason": reason, "diagnostic": diag})
        result = await lease.session.await_result()
        assert result.reason == reason and fake.killed == fake.cleaned == 1

    asyncio.run(scenario())


def test_startup_fatal():
    async def scenario():
        _, lease, fake, _, _ = await setup(ready=False)
        fake.att.send("fatal", {"reason": "RUNNER_FAILURE", "diagnostic": diagnostic()})
        result = await lease.session.await_result()
        assert result.reason == "RUNNER_FAILURE" and fake.att.stdin.messages == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault",
    [
        "seq",
        "runId",
        "python",
        "json",
        "duplicate-key",
        "partial",
        "wrong-direction",
        "input-offset",
        "second-input",
        "console-index",
        "duplicate-finished",
        "post-finished",
    ],
)
def test_protocol_failures(fault):
    async def scenario():
        _, lease, fake, sink, _ = await setup(ready=fault != "python")
        if fault == "python":
            fake.att.send("ready", {"pythonVersion": "3.14.0", "protocolVersion": 1})
        elif fault == "seq":
            fake.att.send("trace_chunk", {"records": []}, seq=44)
        elif fault == "runId":
            fake.att.send(
                "trace_chunk", {"records": []}, runId="22345678-1234-4234-8234-123456789abc"
            )
        elif fault == "json":
            fake.att.stdout.feed_data(b"{broken}\n")
        elif fault == "duplicate-key":
            fake.att.stdout.feed_data(b'{"x":1,"x":2}\n')
        elif fault == "partial":
            fake.att.stdout.feed_data(b"{")
            fake.end()
        elif fault == "wrong-direction":
            fake.att.send("input_response", {"requestId": "q1", "text": ""})
        elif fault == "input-offset":
            fake.att.send("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 1})
        elif fault == "second-input":
            for q in ["q1", "q2"]:
                fake.att.send("input_request", {"requestId": q, "prompt": "", "consoleOffset": 0})
        elif fault == "console-index":
            fake.att.send(
                "output", {"entry": {"index": 1, "kind": "stdout", "text": "x", "requestId": None}}
            )
        else:
            fake.att.send("trace_chunk", {"records": [checkpoint()]})
            payload = {
                "outcome": "SUCCESS",
                "reason": "NONE",
                "primaryDiagnostic": None,
                "finalStateVersion": 0,
            }
            fake.att.send("finished", payload)
            fake.att.send("finished", payload) if fault == "duplicate-finished" else fake.att.send(
                "trace_chunk", {"records": []}
            )
        result = await asyncio.wait_for(lease.session.await_result(), 1)
        assert result.reason == "RUNNER_PROTOCOL_ERROR" and result.cleanupConfirmed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad",
    [
        event(index=1),
        event(version=1),
        event(offset=1),
        checkpoint(2),
        {"kind": "event", "record": {"kind": "RUN_END"}},
    ],
)
def test_invalid_whole_chunk_no_partial_commit(bad):
    async def scenario():
        _, lease, fake, sink, _ = await setup()
        fake.att.send("trace_chunk", {"records": [checkpoint(), bad]})
        result = await lease.session.await_result()
        assert result.reason == "RUNNER_PROTOCOL_ERROR" and sink.records == []
        assert lease.session.prefix.version == -1

    asyncio.run(scenario())


def test_valid_chunk_and_console_forwarding():
    async def scenario():
        _, lease, fake, sink, events = await setup()
        entry = {"index": 0, "kind": "stdout", "text": "hello", "requestId": None}
        fake.att.send("output", {"entry": entry})
        fake.att.send("trace_chunk", {"records": [checkpoint(), event(offset=1)]})
        await pump()
        result = await finish(fake, lease.session)
        assert result.reason == "NONE" and result.eventCount == result.consoleCount == 1
        assert len(sink.records) == 2 and sink.console == [entry]
        assert [e.payload for e in events if e.kind == "console"] == [entry]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["chunk", "console"])
def test_sink_fault_stops_execution(kind):
    async def scenario():
        _, lease, fake, sink, _ = await setup()
        sink.fail = True
        if kind == "chunk":
            fake.att.send("trace_chunk", {"records": [checkpoint()]})
        else:
            fake.att.send(
                "output", {"entry": {"index": 0, "kind": "stdout", "text": "x", "requestId": None}}
            )
        result = await lease.session.await_result()
        assert result.reason == "TRACE_RECORDER_FAILURE" and not result.traceUsable
        assert sink.records == sink.console == []

    asyncio.run(scenario())


def test_stderr_overflow_is_drained_and_not_console():
    async def scenario():
        _, lease, fake, sink, _ = await setup(native_stderr_bytes=100)
        fake.att.stderr.feed_data(b"x" * 10000)
        result = await lease.session.await_result()
        assert result.reason == "RUNNER_FAILURE" and sink.console == []
        assert fake.att.stderr.at_eof() and lease.session._native_bytes == 101

    asyncio.run(scenario())


def test_cleanup_failure_blocks_until_recovery():
    async def scenario():
        manager, lease, fake, sink, events = await setup()
        fake.cleanup_ok = False
        result = await finish(fake, lease.session)
        assert result.reason == "CLEANUP_FAILED" and not result.traceUsable
        lease.finish_ownership()
        with pytest.raises(RunRejected, match="CLEANUP_FAILED"):
            await manager.start_run(
                run_id=RUN_ID, code="", image=IMAGE, sink=sink, subscriber=events.append
            )
        fake.cleanup_ok = True
        assert (await manager.retry_cleanup(IMAGE)).available and fake.handle is None

    asyncio.run(scenario())


def test_failed_start_cleanup_created_container():
    async def scenario():
        fake = FakeDocker()
        fake.attach_fail = True
        manager = RunManager(adapter_factory=lambda image, limits: fake)
        lease = await manager.start_run(
            run_id=RUN_ID, code="", image=IMAGE, sink=Sink(), subscriber=lambda e: None
        )
        result = await asyncio.wait_for(lease.session.await_result(), 1)
        assert result.reason == "RUNNER_FAILURE" and fake.cleaned == 1 and result.cleanupConfirmed

    asyncio.run(scenario())


def test_startup_cancel_recovers_unknown_container():
    async def scenario():
        fake = FakeDocker()
        fake.create_gate = asyncio.Event()
        manager = RunManager(adapter_factory=lambda image, limits: fake)
        lease = await manager.start_run(
            run_id=RUN_ID, code="", image=IMAGE, sink=Sink(), subscriber=lambda e: None
        )
        await pump()
        manager.connection_lost(RUN_ID)
        result = await asyncio.wait_for(lease.session.await_result(), 1)
        assert result.reason == "CONNECTION_LOST" and result.cleanupConfirmed and fake.cleaned == 1

    asyncio.run(scenario())


def test_finalization_timeout_unconfirmed_cleanup_blocks():
    async def scenario():
        manager, lease, fake, _, _ = await setup(finalization_timeout_ms=10)
        fake.cleanup_gate = asyncio.Event()
        result = await finish(fake, lease.session)
        assert result.reason == "CLEANUP_FAILED" and lease.session.arbiter.finalizationExpired
        assert not manager.availability.available and fake.att.closed
        assert all(t.done() for t in lease.session._readers)

    asyncio.run(scenario())


def test_bounded_drain_timeout_kills_and_cleans():
    async def scenario():
        _, lease, fake, _, _ = await setup(cleanup_command_timeout_ms=10)
        fake.att.send("trace_chunk", {"records": [checkpoint()]})
        fake.att.send(
            "finished",
            {
                "outcome": "SUCCESS",
                "reason": "NONE",
                "primaryDiagnostic": None,
                "finalStateVersion": 0,
            },
        )
        result = await asyncio.wait_for(lease.session.await_result(), 1)
        assert result.reason == "RUNNER_FAILURE" and result.cleanupConfirmed and fake.killed == 1

    asyncio.run(scenario())


def test_cancelling_result_waiter_does_not_cancel_run():
    async def scenario():
        _, lease, fake, _, _ = await setup()
        waiter = asyncio.create_task(lease.session.await_result())
        await pump()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (await finish(fake, lease.session)).reason == "NONE"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value", [("active_runs", 2), ("active_timeout_ms", 0), ("memory_bytes", -1)]
)
def test_invalid_limits_no_container(field, value):
    async def scenario():
        fake = FakeDocker()
        manager = RunManager(adapter_factory=lambda image, limits: fake)
        limits = default_limits()
        limits[field] = value
        with pytest.raises(RunRejected, match="INVALID_START"):
            await manager.start_run(
                run_id=RUN_ID,
                code="",
                image=IMAGE,
                sink=Sink(),
                subscriber=lambda e: None,
                limits=limits,
            )
        assert fake.created == 0 and not manager.availability.busy

    asyncio.run(scenario())


def test_input_write_failure_is_infrastructure_failure():
    async def scenario():
        manager, lease, fake, _, _ = await setup()
        fake.att.send("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
        await pump()
        fake.att.stdin.fail = True
        with pytest.raises(BrokenPipeError):
            await manager.submit_input(RUN_ID, "q1", "x")
        result = await lease.session.await_result()
        assert result.reason == "RUNNER_FAILURE" and lease.session.pendingInput is None

    asyncio.run(scenario())


def test_process_exit_while_waiting_clears_pending_and_waiting_interval():
    async def scenario():
        clock = FakeClock()
        manager, lease, fake, _, _ = await setup(clock=clock)
        fake.att.send("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
        await pump()
        clock.advance(4)
        fake.end(127)
        result = await lease.session.await_result()
        assert result.reason == "RUNNER_FAILURE" and result.waitingElapsedMs == 4000
        assert (await manager.submit_input(RUN_ID, "q1", "late")).code == "INVALID_INPUT"

    asyncio.run(scenario())


def test_second_input_continues_remaining_active_budget():
    async def scenario():
        clock = FakeClock()
        manager, lease, fake, _, _ = await setup(clock=clock)
        for index in [1, 2]:
            clock.advance(4)
            fake.att.send(
                "input_request", {"requestId": f"q{index}", "prompt": "", "consoleOffset": 0}
            )
            await pump()
            clock.advance(20)
            await manager.submit_input(RUN_ID, f"q{index}", "")
        clock.advance(2)
        await pump()
        result = await lease.session.await_result()
        assert (
            result.reason == "ACTIVE_TIMEOUT"
            and result.activeElapsedMs == 10000
            and result.waitingElapsedMs == 40000
        )

    asyncio.run(scenario())


def test_cleanup_override_preserves_primary_cause():
    async def scenario():
        _, lease, fake, _, _ = await setup()
        fake.cleanup_ok = False
        lease.session.terminate("ACTIVE_TIMEOUT")
        result = await lease.session.await_result()
        assert result.reason == "CLEANUP_FAILED" and result.primaryCause == "ACTIVE_TIMEOUT"

    asyncio.run(scenario())


def test_oom_does_not_replace_earlier_cause():
    async def scenario():
        _, lease, fake, _, _ = await setup()
        lease.session.terminate("ACTIVE_TIMEOUT")
        fake.end(137, oom=True)
        result = await lease.session.await_result()
        assert result.reason == "ACTIVE_TIMEOUT" and result.oomKilled

    asyncio.run(scenario())


def test_success_proposal_with_nonzero_exit_fails():
    async def scenario():
        _, lease, fake, _, _ = await setup()
        fake.att.send("trace_chunk", {"records": [checkpoint()]})
        fake.att.send(
            "finished",
            {
                "outcome": "SUCCESS",
                "reason": "NONE",
                "primaryDiagnostic": None,
                "finalStateVersion": 0,
            },
        )
        fake.end(1)
        assert (await lease.session.await_result()).reason == "RUNNER_FAILURE"

    asyncio.run(scenario())


def test_docker_unavailable_before_create_does_not_invent_cleanup_failure():
    from codetrace_backend.docker_adapter import DockerUnavailableError

    async def scenario():
        fake = FakeDocker()

        async def unavailable():
            raise DockerUnavailableError("daemon")

        fake.check_available = unavailable
        fake.list_managed_containers = unavailable
        manager = RunManager(adapter_factory=lambda image, limits: fake)
        lease = await manager.start_run(
            run_id=RUN_ID, code="", image=IMAGE, sink=Sink(), subscriber=lambda e: None
        )
        result = await lease.session.await_result()
        assert (
            result.reason == "DOCKER_UNAVAILABLE" and result.cleanupConfirmed and fake.created == 0
        )

    asyncio.run(scenario())


def test_failed_subscriber_cannot_strand_container():
    async def scenario():
        fake = FakeDocker()
        manager = RunManager(adapter_factory=lambda image, limits: fake)

        def observer(event):
            raise RuntimeError("subscriber failed")

        lease = await manager.start_run(
            run_id=RUN_ID, code="", image=IMAGE, sink=Sink(), subscriber=observer
        )
        result = await lease.session.await_result()
        assert result.reason == "CONNECTION_LOST" and result.cleanupConfirmed

    asyncio.run(scenario())


def test_slow_sink_does_not_block_watchdog():
    async def scenario():
        clock = FakeClock()
        _, lease, fake, sink, _ = await setup(clock=clock, cleanup_command_timeout_ms=10)
        sink.gate = asyncio.Event()
        fake.att.send("trace_chunk", {"records": [checkpoint()]})
        await pump()
        clock.advance(10)
        await pump()
        result = await asyncio.wait_for(lease.session.await_result(), 1)
        assert result.reason == "ACTIVE_TIMEOUT" and fake.killed == 1 and result.cleanupConfirmed
        assert sink.records == []

    asyncio.run(scenario())


def test_atomic_simultaneous_slot_reservation():
    async def scenario():
        fake = FakeDocker()
        manager = RunManager(adapter_factory=lambda image, limits: fake)

        async def start():
            return await manager.start_run(
                run_id=RUN_ID, code="", image=IMAGE, sink=Sink(), subscriber=lambda e: None
            )

        results = await asyncio.gather(start(), start(), return_exceptions=True)
        assert isinstance(results[1], RunRejected) and results[1].code == "RUN_BUSY"
        await pump()
        assert fake.created == 1
        await manager.shutdown()

    asyncio.run(scenario())


def test_startup_cleanup_attempts_all_orphans_and_recovers():
    from codetrace_backend.docker_adapter import ContainerHandle

    async def scenario():
        fake = FakeDocker()
        handles = [
            ContainerHandle(c * 64, r, "codetrace-" + r)
            for c, r in [("c", RUN_ID), ("d", "22345678-1234-4234-8234-123456789abc")]
        ]
        fake.orphans = handles.copy()
        seen = []

        async def cleanup(handle):
            seen.append(handle)
            if handle == handles[0]:
                raise RuntimeError("transient")
            fake.orphans.remove(handle)
            from codetrace_backend.docker_adapter import CleanupResult

            return CleanupResult(True, 1)

        fake.cleanup_managed_container = cleanup
        manager = RunManager(adapter_factory=lambda image, limits: fake)
        assert not (await manager.startup_cleanup(IMAGE)).available and seen == handles
        fake.orphans = []
        assert (await manager.retry_cleanup(IMAGE)).available

    asyncio.run(scenario())


def test_finalization_timeout_after_confirmed_removal_preserves_confirmation():
    async def scenario():
        _, lease, fake, _, _ = await setup(
            finalization_timeout_ms=10, cleanup_command_timeout_ms=10
        )

        async def hung_close():
            await asyncio.Event().wait()

        fake.att.close = hung_close
        result = await finish(fake, lease.session)
        assert (
            result.reason == "FINALIZATION_TIMEOUT"
            and result.cleanupConfirmed
            and not result.traceUsable
        )
        assert fake.cleaned == 1 and fake.state is None

    asyncio.run(scenario())
