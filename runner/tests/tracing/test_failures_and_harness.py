import builtins
import sys

import pytest
from codetrace_contracts import default_limits
from codetrace_runner.reference import trace_source
from codetrace_runner.registry import ObjectRegistry
from codetrace_runner.serializer import GraphSerializer
from codetrace_runner.trace_engine import ReferenceLimitError, TraceEngine, TraceEngineError

from .helpers import assert_trace_invariants


@pytest.mark.parametrize("at", [1, 2, 3, 4, 5, 6, 7])
def test_serializer_failure_at_each_boundary_never_publishes_partial_trace(monkeypatch, at):
    original = GraphSerializer.serialize
    count = 0

    def fail(self, *args, **kwargs):
        nonlocal count
        count += 1
        if count == at:
            raise RuntimeError("injected serializer failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GraphSerializer, "serialize", fail)
    previous = sys.gettrace()
    with pytest.raises(TraceEngineError) as error:
        trace_source("def f():\n    return 1\nx = f()\n")
    assert count == at
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert sys.gettrace() is previous


def test_user_catching_callback_abort_cannot_hide_sticky_failure(monkeypatch):
    original = GraphSerializer.serialize
    caught = []
    monkeypatch.setattr(builtins, "_codetrace_caught", lambda: caught.append(True), raising=False)

    def fail(self, user_globals, frames=(), stack=(), event_roots=()):
        if any("marker" in f.live_locals for f in frames):
            raise RuntimeError("injected")
        return original(self, user_globals, frames, stack, event_roots)

    monkeypatch.setattr(GraphSerializer, "serialize", fail)
    with pytest.raises(TraceEngineError):
        trace_source(
            "def f():\n    marker = 1\n    return marker\ntry:\n    f()\n"
            "except BaseException:\n    _codetrace_caught()\n"
        )
    assert caught == [True]


@pytest.mark.parametrize("source", ["pass", "raise ValueError('user')", "class :"])
def test_previous_trace_restored_on_success_user_exception_and_compile_error(source):
    def previous_trace(frame, event, arg):
        return previous_trace

    saved = sys.gettrace()
    sys.settrace(previous_trace)
    try:
        if source == "class :":
            with pytest.raises(SyntaxError):
                trace_source(source)
        else:
            trace_source(source)
        assert sys.gettrace() is previous_trace
    finally:
        sys.settrace(saved)


def test_previous_trace_restored_after_internal_fault(monkeypatch):
    def previous_trace(frame, event, arg):
        return previous_trace

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    saved = sys.gettrace()
    sys.settrace(previous_trace)
    monkeypatch.setattr(GraphSerializer, "serialize", fail)
    try:
        with pytest.raises(TraceEngineError):
            trace_source("pass")
        assert sys.gettrace() is previous_trace
    finally:
        sys.settrace(saved)


def test_single_compile_and_single_execution(monkeypatch):
    original = builtins.compile
    compilations = []
    executions = []

    def compile_spy(source, filename, mode, **kwargs):
        if filename == "<codetrace-user>":
            compilations.append((source, filename, mode, kwargs))
        return original(source, filename, mode, **kwargs)

    monkeypatch.setattr(builtins, "compile", compile_spy)
    monkeypatch.setattr(builtins, "_codetrace_tick", lambda: executions.append(True), raising=False)
    source = "_codetrace_tick()\nx = 1\n"
    result = trace_source(source)
    assert len(compilations) == 1
    assert compilations[0][:3] == (source, "<codetrace-user>", "exec")
    assert executions == [True]
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize("source", ["pass", "1/0", "def f():\n    return []\nx = f()"])
def test_cleanup_releases_active_frames_registry_and_globals(monkeypatch, source):
    engine_close = TraceEngine.close
    registry_close = ObjectRegistry.close
    engines = []
    registries = []

    def close_engine(self):
        engine_close(self)
        engines.append(self)

    def close_registry(self):
        registry_close(self)
        registries.append(self)

    monkeypatch.setattr(TraceEngine, "close", close_engine)
    monkeypatch.setattr(ObjectRegistry, "close", close_registry)
    result = trace_source(source)
    assert len(engines) == len(registries) == 1
    assert engines[0]._active == {} and engines[0]._stack == []
    assert engines[0]._globals == {} and engines[0]._points == []
    assert registries[0].closed and registries[0].count == 0
    assert_trace_invariants(result.trace)


def test_completed_frames_released_before_next_user_event(monkeypatch):
    original = TraceEngine._observe
    observations = 0

    def observe(self, frame, event, arg):
        nonlocal observations
        original(self, frame, event, arg)
        if event == "return":
            observations += 1
            assert id(frame) not in self._active
            assert all(entry.live is not frame for entry in self._stack)

    monkeypatch.setattr(TraceEngine, "_observe", observe)
    trace_source("def f(): return 1\nfor i in range(10): f()\n")
    assert observations == 11


def test_cleanup_also_runs_after_serializer_fault(monkeypatch):
    original = TraceEngine.close
    closed = []

    def close(self):
        original(self)
        closed.append(self)

    def fail(*args, **kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(TraceEngine, "close", close)
    monkeypatch.setattr(GraphSerializer, "serialize", fail)
    with pytest.raises(TraceEngineError):
        trace_source("pass")
    assert len(closed) == 1
    assert closed[0]._active == {} and closed[0]._stack == [] and closed[0]._globals == {}
    assert closed[0]._serializer._registry.closed


@pytest.mark.parametrize(
    "limit,value,source,reason",
    [
        ("active_frames", 1, "def f(): pass\nf()", "FRAME_LIMIT"),
        ("collection_items", 1, "x = [1, 2]", "COLLECTION_LIMIT"),
        ("registry_objects", 1, "x = []\ny = []", "REGISTRY_LIMIT"),
        ("integer_decimal_digits", 1, "x = 10", "INT_DIGITS_LIMIT"),
        ("state_bytes", 1, "pass", "STATE_BYTES_LIMIT"),
    ],
)
def test_domain_limits_are_not_user_exception_or_infrastructure_failure(
    limit, value, source, reason
):
    limits = default_limits()
    limits[limit] = value
    previous = sys.gettrace()
    with pytest.raises(ReferenceLimitError) as error:
        trace_source(source, limits=limits)
    assert error.value.reason == reason
    assert error.value.diagnostic["category"] == "limit"
    assert error.value.__context__ is None
    assert sys.gettrace() is previous


def test_unsupported_tracing_interference_is_not_a_valid_reference_trace():
    previous = sys.gettrace()
    with pytest.raises(TraceEngineError):
        trace_source("import sys\nsys.settrace(None)\nx = 1\n")
    assert sys.gettrace() is previous


@pytest.mark.parametrize("source", [None, b"pass", 1, []])
def test_invalid_source_rejected_before_execution(source):
    with pytest.raises(TypeError, match="exact string"):
        trace_source(source)
