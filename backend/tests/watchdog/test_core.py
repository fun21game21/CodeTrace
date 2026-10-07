import asyncio
from itertools import product

import pytest
from codetrace_backend.runs import InvariantError, TerminalArbiter, Timing
from codetrace_backend.runs.timing import TERMINAL, TRANSITIONS
from codetrace_contracts import default_limits

from .helpers import FakeClock


@pytest.mark.parametrize(
    "source,target", [(s, t) for s, targets in TRANSITIONS.items() for t in targets]
)
def test_each_legal_transition(source, target):
    timing = Timing(FakeClock(), default_limits())
    timing.status = source
    timing.transition(target)
    assert timing.status == target


@pytest.mark.parametrize(
    "source,target",
    [
        (s, t)
        for s, t in product(
            ["STARTING", "RUNNING", "WAITING_INPUT", "FINALIZING", *sorted(TERMINAL)], repeat=2
        )
        if t not in TRANSITIONS.get(s, set())
    ],
)
def test_invalid_transition(source, target):
    timing = Timing(FakeClock(), default_limits())
    timing.status = source
    with pytest.raises(InvariantError):
        timing.transition(target)
    assert timing.status == source


def test_exact_intervals_and_deadlines():
    clock = FakeClock()
    timing = Timing(clock, default_limits())
    clock.advance(2)
    assert timing.elapsed() == (0, 0)
    timing.started = clock.now()
    timing.transition("RUNNING")
    clock.advance(3)
    timing.transition("WAITING_INPUT")
    assert timing.elapsed() == (3000, 0)
    clock.advance(40)
    timing.transition("RUNNING")
    assert timing.deadline() == (52, "ACTIVE_TIMEOUT")
    clock.advance(2)
    timing.transition("WAITING_INPUT")
    clock.advance(60)
    timing.transition("RUNNING")
    assert timing.elapsed() == (5000, 100000)
    assert timing.deadline() == (112, "ACTIVE_TIMEOUT")
    clock.advance(1)
    timing.transition("FINALIZING")
    clock.advance(5)
    assert timing.elapsed() == (6000, 100000) and timing.deadline() is None


@pytest.mark.parametrize(
    "status,started,expected",
    [
        ("STARTING", None, "STARTUP_TIMEOUT"),
        ("RUNNING", 0, "ACTIVE_TIMEOUT"),
        ("WAITING_INPUT", 0, "INPUT_IDLE_TIMEOUT"),
    ],
)
def test_next_deadline(status, started, expected):
    timing = Timing(FakeClock(), default_limits())
    timing.status = status
    timing.started = started
    assert timing.deadline()[1] == expected


def test_lifetime_includes_input():
    timing = Timing(FakeClock(), default_limits())
    timing.started = -250
    timing.status = "WAITING_INPUT"
    assert timing.deadline() == (50, "CONTAINER_LIFETIME")


@pytest.mark.parametrize(
    "first,second",
    [
        ("ACTIVE_TIMEOUT", "CONNECTION_LOST"),
        ("CONNECTION_LOST", "ACTIVE_TIMEOUT"),
        ("EVENT_LIMIT", "ACTIVE_TIMEOUT"),
        ("INPUT_IDLE_TIMEOUT", "RUNNER_FAILURE"),
        ("RUNNER_FAILURE", "BACKEND_SHUTDOWN"),
    ],
)
def test_first_cause_wins(first, second):
    a = TerminalArbiter()
    assert a.accept(first)
    assert not a.accept(second)
    assert a.decide(exit_code=137, oom=True, cleanup=True).reason == first
    assert a.decide(exit_code=137, oom=True, cleanup=False).reason == "CLEANUP_FAILED"
    assert a.primaryCause == first


@pytest.mark.parametrize(
    "proposal,code,oom,expected",
    [
        ("SUCCESS", 0, False, "NONE"),
        ("SUCCESS", 1, False, "RUNNER_FAILURE"),
        ("USER_ERROR", 0, False, "SYNTAX_ERROR"),
        (None, 0, False, "RUNNER_FAILURE"),
        (None, 137, True, "MEMORY_LIMIT"),
        ("SUCCESS", 0, True, "MEMORY_LIMIT"),
    ],
)
def test_proposal_exit_arbitration(proposal, code, oom, expected):
    a = TerminalArbiter()
    if proposal:
        a.proposal = {
            "outcome": proposal,
            "reason": "NONE" if proposal == "SUCCESS" else "SYNTAX_ERROR",
        }
    assert a.decide(exit_code=code, oom=oom, cleanup=True).reason == expected


def test_finalization_override_retains_primary():
    a = TerminalArbiter()
    a.accept("ACTIVE_TIMEOUT")
    a.finalizationExpired = True
    assert a.decide(exit_code=137, oom=False, cleanup=True).reason == "FINALIZATION_TIMEOUT"
    assert a.primaryCause == "ACTIVE_TIMEOUT"
    assert a.decide(exit_code=137, oom=False, cleanup=False).reason == "CLEANUP_FAILED"


def test_clock_wakes_on_transition():
    async def scenario():
        clock = FakeClock()
        wake = asyncio.Event()
        task = asyncio.create_task(clock.wait(wake, 10))
        await asyncio.sleep(0)
        clock.advance(10)
        await task

    asyncio.run(scenario())
