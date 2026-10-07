import ast
import builtins

import pytest
from codetrace_contracts import default_limits
from codetrace_runner.canonical import canonical_size
from codetrace_runner.runner import preflight
from codetrace_runner.runner_diagnostics import UnsupportedFeature, uncaught

from .helpers import execute, records


@pytest.mark.parametrize(
    "source",
    [
        "def f():\n yield 1",
        "def f():\n yield from []",
        "x=(i for i in [])",
        "async def f():\n pass",
        "await f()",
        "async for x in y:\n pass",
        "async with x:\n pass",
        "eval('1')",
        "exec('x=1')",
        "compile('1','x','eval')",
    ],
)
def test_preflight_unsupported(source):
    _, messages, status = execute(source)
    assert status == 0
    assert records(messages, "event") == []
    assert len(records(messages, "state")) == 1
    assert messages[-1]["payload"]["reason"] == "UNSUPPORTED_FEATURE"
    assert messages[-1]["payload"]["finalStateVersion"] == 0


@pytest.mark.parametrize(
    "source", ["if", "x=", "def f(:", "x = '", "a = 1\n  b=2", "# 😀\nif", "漢 = (", "return 1"]
)
def test_syntax(source):
    _, messages, status = execute(source)
    assert status == 0
    payload = messages[-1]["payload"]
    assert payload["reason"] == "SYNTAX_ERROR" and payload["finalStateVersion"] == 0
    assert records(messages, "event") == []
    assert payload["primaryDiagnostic"]["category"] == "syntax"
    try:
        compile(source, "<codetrace-user>", "exec", dont_inherit=True, optimize=0)
    except SyntaxError as error:
        offset = error.offset if error.offset and error.offset > 0 else None
        assert payload["primaryDiagnostic"]["columnNumber"] == offset


@pytest.mark.parametrize("name", ["os", "sys", "subprocess", "threading", "builtins", "random"])
def test_disallowed_import(name):
    _, messages, status = execute("x=1\nimport " + name)
    assert status == 0 and records(messages, "event")
    assert messages[-1]["payload"]["reason"] == "UNSUPPORTED_FEATURE"


@pytest.mark.parametrize(
    "source",
    [
        "import math\nx=math.sqrt(4)",
        "from collections import deque\nx=deque([1,2])",
        "from math import pi\nx=pi",
    ],
)
def test_allowed_import(source):
    _, messages, status = execute(source)
    assert status == 0 and messages[-1]["payload"]["outcome"] == "SUCCESS"


@pytest.mark.parametrize(
    "source",
    [
        "1/0",
        "x=[1]\nx[3]",
        "raise ValueError('message')",
        "raise SystemExit(2)",
        "raise KeyboardInterrupt()",
    ],
)
def test_uncaught(source):
    _, messages, status = execute(source)
    assert status == 0
    assert messages[-1]["payload"]["reason"] == "UNCAUGHT_EXCEPTION"
    diagnostic = messages[-1]["payload"]["primaryDiagnostic"]
    assert diagnostic["category"] == "runtime"
    assert diagnostic["traceback"]
    assert all(f["source"]["fileName"] == "<codetrace-user>" for f in diagnostic["traceback"])
    assert any(e["kind"] == "EXCEPTION" for e in records(messages, "event"))


def test_handled_error():
    _, messages, status = execute("try:\n 1/0\nexcept ZeroDivisionError:\n pass")
    assert status == 0 and messages[-1]["payload"]["outcome"] == "SUCCESS"
    assert any(e["kind"] == "EXCEPTION" for e in records(messages, "event"))


def test_hostile_exception_hooks_never_formatted():
    source = """class E(Exception):
 def __str__(self):
  print('STR CALLED')
  raise RuntimeError()
 def __getattribute__(self, name):
  if name == '__traceback__':
   print('ATTRIBUTE CALLED')
   raise RuntimeError()
  return super().__getattribute__(name)
raise E('safe')
"""
    _, messages, status = execute(source)
    assert status == 0
    assert not any(m["type"] == "output" for m in messages)
    assert messages[-1]["payload"]["primaryDiagnostic"]["message"] == "safe"


@pytest.mark.parametrize("arg", ["😀" * 30000, object(), {"a": 1}])
def test_bounded_safe_terminal_diagnostic(arg):
    class E(Exception):
        def __str__(self):
            raise AssertionError("hook invoked")

    dto = uncaught(E(arg), None)
    assert canonical_size(dto) <= 16384
    if isinstance(arg, str):
        assert dto["message"].endswith("[truncated]")


@pytest.mark.parametrize(
    "key,value,source,reason",
    [
        ("registry_objects", 1, "a=[]\nb=[]", "REGISTRY_LIMIT"),
        ("string_bytes", 2, "x='abc'", "STRING_LIMIT"),
        ("integer_decimal_digits", 2, "x=100", "INT_DIGITS_LIMIT"),
        ("collection_items", 2, "x=[1,2,3]", "COLLECTION_LIMIT"),
        ("active_frames", 1, "def f():\n pass\nf()", "FRAME_LIMIT"),
        ("state_bytes", 300, "x=1", "STATE_BYTES_LIMIT"),
        ("max_events", 2, "x=1", "EVENT_LIMIT"),
        ("console_bytes", 2, "print('abc')", "CONSOLE_LIMIT"),
        ("input_requests", 1, "input()\ninput()", "INPUT_REQUEST_LIMIT"),
    ],
)
def test_domain_limits(key, value, source, reason):
    limits = default_limits()
    limits[key] = value
    _, messages, status = execute(source, limits=limits, responses=[""])
    assert status == 1 and messages[-1]["type"] == "abort_request"
    assert messages[-1]["payload"]["reason"] == reason
    assert messages[-1]["payload"]["diagnostic"]["category"] == "limit"
    assert not any(m["type"] in ("fatal", "finished") for m in messages)


def test_trace_bytes_limit():
    limits = default_limits()
    limits["trace_bytes"] = limits["terminal_reserve_bytes"] + 250
    _, messages, status = execute("x=1", limits=limits)
    assert status == 1
    assert messages[-1]["payload"]["reason"] == "TRACE_BYTES_LIMIT"


@pytest.mark.parametrize("layer", ["engine", "serializer", "encoder", "batch"])
def test_internal_failures(layer, monkeypatch):
    from codetrace_runner.encoder import TraceEncoderSession
    from codetrace_runner.io_protocol import TraceBatch
    from codetrace_runner.serializer import GraphSerializer
    from codetrace_runner.trace_engine import TraceEngine

    target, method = {
        "engine": (TraceEngine, "_observe"),
        "serializer": (GraphSerializer, "serialize"),
        "encoder": (TraceEncoderSession, "consume"),
        "batch": (TraceBatch, "add"),
    }[layer]
    original = getattr(target, method)
    calls = 0

    def broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("SECRET INFRA VALUE")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, method, broken)
    _, messages, status = execute("x=1\nx=2")
    assert status == 1 and messages[-1]["type"] == "fatal"
    assert messages[-1]["payload"]["reason"] == "RUNNER_FAILURE"
    assert "SECRET" not in str(messages)
    assert not any(m["type"] == "finished" for m in messages)


def test_callback_limit_notified_before_user_catch():
    limits = default_limits()
    limits["string_bytes"] = 2
    source = """caught=False
try:
 x='abc'
 x=1
except BaseException:
 caught=True
"""
    _, messages, status = execute(source, limits=limits)
    assert status == 1 and messages[-1]["type"] == "abort_request"
    assert messages[-1]["payload"]["reason"] == "STRING_LIMIT"


def test_compile_exec_once_no_ast_rewrite(monkeypatch):
    original_compile, original_exec = builtins.compile, builtins.exec
    compiles, executions = [], []

    def count_compile(*args, **kwargs):
        if (
            len(args) > 1
            and args[1] == "<codetrace-user>"
            and not ((args[3] if len(args) > 3 else kwargs.get("flags", 0)) & ast.PyCF_ONLY_AST)
        ):
            compiles.append((args, kwargs))
        return original_compile(*args, **kwargs)

    def count_exec(code, globals=None, locals=None, **kwargs):
        if getattr(code, "co_filename", None) == "<codetrace-user>":
            executions.append(code)
        return original_exec(code, globals, locals, **kwargs)

    monkeypatch.setattr(builtins, "compile", count_compile)
    monkeypatch.setattr(builtins, "exec", count_exec)
    _, _, status = execute("x=1")
    assert status == 0 and len(compiles) == len(executions) == 1
    assert compiles[0][1] == {"dont_inherit": True, "optimize": 0}
    assert isinstance(compiles[0][0][0], str)


@pytest.mark.parametrize("builtin", ["open", "breakpoint", "exec", "eval", "compile"])
def test_user_builtins_omitted(builtin):
    _, messages, status = execute(f"present={builtin!r} in __builtins__")
    assert status == 0
    from encoder.helpers import seek

    state = seek(records(messages, "state"), messages[-1]["payload"]["finalStateVersion"])
    assert next(b["value"]["value"] for b in state["globals"] if b["name"] == "present") is False


def test_preflight_calls_only_direct_names():
    preflight("class A:\n def eval(self):\n  return 1\nx=A().eval()")
    with pytest.raises(UnsupportedFeature):
        preflight("eval('1')")
