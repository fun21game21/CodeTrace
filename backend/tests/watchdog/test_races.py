import asyncio

import pytest

from .helpers import RUN_ID, FakeClock, checkpoint, diagnostic, finish, pump, setup


def message(kind, payload):
    return {"protocolVersion": 1, "runId": RUN_ID, "seq": 2, "type": kind, "payload": payload}


@pytest.mark.parametrize("first", ["timeout", "input"])
def test_race_active_input(first):
    async def scenario():
        clock = FakeClock()
        _, lease, fake, _, _ = await setup(clock=clock)
        s = lease.session
        clock.advance(9)
        if first == "timeout":
            clock.advance(1)
            s.check_deadline()
        await s._message(
            message("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
        )
        if first == "input":
            clock.advance(2)
            assert not s.check_deadline() and s.status == "WAITING_INPUT"
            assert (await s.submit_input(RUN_ID, "q1", "x")).accepted
            clock.advance(1)
            s.check_deadline()
        result = await s.await_result()
        assert result.reason == "ACTIVE_TIMEOUT" and fake.killed == fake.cleaned == 1
        assert result.waitingElapsedMs == (2000 if first == "input" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["idle", "response"])
def test_race_idle_response(first):
    async def scenario():
        clock = FakeClock()
        _, lease, fake, _, _ = await setup(clock=clock)
        s = lease.session
        await s._message(
            message("input_request", {"requestId": "q1", "prompt": "", "consoleOffset": 0})
        )
        clock.advance(119)
        if first == "idle":
            clock.advance(1)
            s.check_deadline()
        answer = await s.submit_input(RUN_ID, "q1", "x")
        if first == "response":
            clock.advance(1)
            assert not s.check_deadline()
            result = await finish(fake, s)
            assert result.reason == "NONE" and answer.accepted
        else:
            result = await s.await_result()
            assert result.reason == "INPUT_IDLE_TIMEOUT" and not answer.accepted
        assert fake.cleaned == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["disconnect", "finished"])
def test_race_disconnect_finished(first):
    async def scenario():
        manager, lease, fake, _, events = await setup()
        s = lease.session
        await s._message(message("trace_chunk", {"records": [checkpoint()]}))
        payload = {
            "outcome": "SUCCESS",
            "reason": "NONE",
            "primaryDiagnostic": None,
            "finalStateVersion": 0,
        }
        if first == "disconnect":
            manager.connection_lost(RUN_ID)
        await s._message(message("finished", payload))
        manager.connection_lost(RUN_ID)
        fake.end()
        result = await s.await_result()
        assert result.reason == ("CONNECTION_LOST" if first == "disconnect" else "NONE")
        assert sum(e.kind == "result" for e in events) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["abort", "timeout"])
def test_race_abort_watchdog(first):
    async def scenario():
        clock = FakeClock()
        _, lease, fake, _, _ = await setup(clock=clock)
        s = lease.session
        if first == "timeout":
            clock.advance(10)
            s.check_deadline()
        await s._message(
            message(
                "abort_request",
                {
                    "reason": "EVENT_LIMIT",
                    "diagnostic": {**diagnostic("EVENT_LIMIT"), "category": "limit"},
                },
            )
        )
        clock.advance(10)
        s.check_deadline()
        result = await s.await_result()
        assert result.reason == ("EVENT_LIMIT" if first == "abort" else "ACTIVE_TIMEOUT")
        assert fake.killed == fake.cleaned == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["exit", "finished"])
def test_race_exit_finished_drains_before_decision(first):
    async def scenario():
        _, lease, fake, _, _ = await setup()
        s = lease.session
        fake.att.send("trace_chunk", {"records": [checkpoint()]})
        await pump()
        if first == "exit":
            fake.end(eof=False)
            await pump()
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
        result = await s.await_result()
        assert result.reason == "NONE" and fake.cleaned == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["shutdown", "timeout"])
def test_race_shutdown_termination(first):
    async def scenario():
        manager, lease, fake, _, _ = await setup()
        s = lease.session
        if first == "timeout":
            s.terminate("ACTIVE_TIMEOUT")
        shutdown = asyncio.create_task(manager.shutdown())
        await pump()
        s.terminate("ACTIVE_TIMEOUT")
        result = await shutdown
        assert result.reason == ("BACKEND_SHUTDOWN" if first == "shutdown" else "ACTIVE_TIMEOUT")
        assert fake.cleaned == fake.killed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["ACTIVE_TIMEOUT", "CONNECTION_LOST", "EVENT_LIMIT"])
def test_race_simultaneous_termination_stress(first):
    async def scenario():
        for _ in range(10):
            _, lease, fake, _, events = await setup()
            s = lease.session
            gate = asyncio.Event()

            async def terminate(reason):
                await gate.wait()
                return s.terminate(reason)

            tasks = [
                asyncio.create_task(terminate(reason))
                for reason in [first, "RUNNER_FAILURE", "BACKEND_SHUTDOWN"]
            ]
            gate.set()
            assert await asyncio.gather(*tasks) == [True, False, False]
            result = await s.await_result()
            assert result.reason == first and fake.cleaned == fake.killed == 1
            assert sum(e.kind == "result" for e in events) == 1

    asyncio.run(scenario())
