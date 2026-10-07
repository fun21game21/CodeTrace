import builtins
import sys

import pytest
from codetrace_contracts import default_limits
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.encoder_errors import EncoderLimitError, TraceEncodingError
from codetrace_runner.reference import trace_source
from codetrace_runner.registry import ObjectRegistry
from codetrace_runner.serializer import GraphSerializer
from codetrace_runner.trace_engine import TraceEngine
from codetrace_runner.trace_types import ReferenceTrace
from encoder.helpers import seek

from .helpers import execute, records

SOURCES = [
    "x=1\nx=2",
    "for i in range(3):\n x=i",
    "def f(x):\n return x+1\ny=f(2)",
    "def f(n):\n return 1 if n<2 else n*f(n-1)\nx=f(4)",
    "try:\n 1/0\nexcept ZeroDivisionError:\n x=2",
    "class A:\n def __init__(self):\n  self.x=1\na=A()",
    "def f():\n return [1,2]\nx=f()",
    "a=[]\nb=a\na.append(a)",
    "pass",
    "",
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("interval", [1, 2, 128])
def test_stream_batch_equivalence(source, interval):
    reference = trace_source(source).trace
    expected = TraceEncoder(checkpoint_interval=interval).encode(reference)
    observed, events, states = [], [], []
    session = TraceEncoder(checkpoint_interval=interval).session()
    states.append(session.start())

    def sink(point):
        observed.append(point)
        record, event = session.consume(point)
        if record is not None:
            states.append(record)
        events.append(event)

    code = compile(source, "<codetrace-user>", "exec", dont_inherit=True, optimize=0)
    namespace = {
        "__name__": "__main__",
        "__file__": "<codetrace-user>",
        "__builtins__": vars(builtins).copy(),
    }
    registry = ObjectRegistry()
    engine = TraceEngine(
        code,
        len(source.split("\n")) if source else 0,
        namespace,
        GraphSerializer(registry),
        point_sink=sink,
        collect_points=False,
    )
    previous = sys.gettrace()
    try:
        sys.settrace(engine.callback)
        exec(code, namespace)
        assert engine.result() == ReferenceTrace(())
        assert engine._points == []
    finally:
        sys.settrace(previous)
        engine.close()
        registry.close()
    assert tuple(observed) == reference.points
    assert tuple(states) == expected.states
    assert tuple(events) == expected.events
    assert session.accounting == expected.accounting


@pytest.mark.parametrize("source", SOURCES + ["x=9\n1/0", "import os"])
def test_final_state_and_wire_order(source):
    _, messages, status = execute(source)
    assert status == 0 and messages[-1]["type"] == "finished"
    states = records(messages, "state")
    events = records(messages, "event")
    assert states[0]["version"] == 0
    assert [e["index"] for e in events] == list(range(len(events)))
    assert all(e["kind"] != "RUN_END" for e in events)
    version = messages[-1]["payload"]["finalStateVersion"]
    state = seek(states, version)
    assert state["stack"] == state["frames"] == state["eventRoots"] == []
    assert version == states[-1]["version"]
    received = set()
    for message in messages:
        if message["type"] == "trace_chunk":
            for record in message["payload"]["records"]:
                if record["kind"] == "state":
                    received.add(record["record"]["version"])
                else:
                    assert record["record"]["stateVersion"] in received
    if source.startswith("x=9"):
        assert {b["name"]: b["value"]["value"] for b in state["globals"]}["x"] == "9"


def test_final_transition_no_change_and_closed_session():
    session = TraceEncoder().session()
    initial = session.start()
    assert session.accept_final_snapshot(
        {k: v for k, v in initial["state"].items() if k != "version"}
    ) == (None, 0)
    with pytest.raises(TraceEncodingError):
        session.accept_ordinary_bytes(1)
    with pytest.raises(TraceEncodingError):
        session.start()


def test_final_uses_terminal_reserve():
    reference = trace_source("x=1").trace
    session = TraceEncoder().session()
    session.start()
    for point in reference.points:
        session.consume(point)
    ordinary = session.ordinaryBytes
    state = reference.points[-1].snapshot.copy()
    state.update(frames=[], stack=[], eventRoots=[])
    record, version = session.accept_final_snapshot(state)
    assert version > reference.points[-1].snapshot.get("version", 0)
    assert record is not None and session.terminalStateBytes > 0
    assert session.ordinaryBytes == ordinary


def test_shared_accounting_real_offsets_and_console():
    limits = default_limits()
    session = TraceEncoder(limits).session()
    session.start()
    point = trace_source("pass").trace.points[0]
    before = session.ordinaryBytes
    session.consume(point, console_offset=12)
    assert session.ordinaryBytes > before + session.accounting.eventCoreBytes
    session.accept_ordinary_bytes(20)
    assert (
        session.ordinaryBytes
        == session.accounting.recordBytes + len(',"consoleOffset":12') + 2 + 20
    )
    remaining = session.accounting.ordinaryBudgetBytes - session.ordinaryBytes
    session.accept_ordinary_bytes(remaining)
    with pytest.raises(EncoderLimitError):
        session.accept_ordinary_bytes(1)


def test_production_memory_bounds(monkeypatch):
    import codetrace_runner.runner as module

    engines = []
    original = module.TraceEngine

    def capture(*args, **kwargs):
        engine = original(*args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(module, "TraceEngine", capture)
    runner, messages, status = execute("for i in range(1500):\n print('',end='')")
    assert status == 0 and len(records(messages, "event")) > 3000
    assert engines[0]._points == [] and not engines[0]._collect_points
    assert runner.batch.peakBytes < 65536
    assert runner.batch._records == []
    assert not any(isinstance(v, list) for v in vars(runner.session).values())
    assert not any(isinstance(v, list) for v in vars(runner.io).values())


def test_memory_during_observation_not_only_after_cleanup(monkeypatch):
    import codetrace_runner.runner as module

    original = module.Runner._point
    observed = 0

    def inspect_point(runner, point):
        nonlocal observed
        observed += 1
        # No collection is hidden in the synchronous sink/session.
        assert not any(isinstance(value, list) for value in vars(runner.session).values())
        original(runner, point)
        assert runner.batch._bytes <= 65536

    monkeypatch.setattr(module.Runner, "_point", inspect_point)
    _, _, status = execute("for i in range(500):\n pass")
    assert status == 0 and observed > 1000


def test_identical_events_preserved_and_checkpoint_boundary():
    limits = default_limits()
    limits["checkpoint_interval"] = 2
    _, messages, status = execute("for i in range(3):\n pass", limits=limits)
    assert status == 0
    events, states = records(messages, "event"), records(messages, "state")
    assert any(a["stateVersion"] == b["stateVersion"] for a, b in zip(events, events[1:]))
    assert all(r["kind"] == ("checkpoint" if r["version"] % 2 == 0 else "delta") for r in states)


def test_same_physical_line_has_no_invented_event():
    _, messages, status = execute("print('A');x=1\nx=2")
    assert status == 0
    assert (
        len(
            [
                e
                for e in records(messages, "event")
                if e["kind"] == "LINE" and e["source"]["lineNumber"] == 1
            ]
        )
        == 1
    )
