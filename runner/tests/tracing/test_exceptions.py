import pytest
from codetrace_contracts import validate_contract
from codetrace_contracts.config import DIAGNOSTIC_BYTES
from codetrace_runner.canonical import canonical_size
from codetrace_runner.reference import trace_source
from codetrace_runner.trace_diagnostics import observed_exception

from .helpers import assert_trace_invariants, bindings, frame, named


def test_handled_exception_diagnostic_and_continuing_lines():
    result = trace_source("try:\n    1 / 0\nexcept ZeroDivisionError:\n    x = 1\ny = 2\n")
    exception = next(p for p in result.trace.points if p.kind == "EXCEPTION")
    diagnostic = exception.payload["diagnostic"]
    assert diagnostic == {
        "category": "runtime",
        "code": "EXCEPTION_OBSERVED",
        "typeName": "ZeroDivisionError",
        "message": "division by zero",
        "source": {"fileName": "<codetrace-user>", "lineNumber": 2},
        "columnNumber": None,
        "traceback": [],
        "omittedTracebackFrames": 0,
    }
    assert result.exception_escaped is False
    later = result.trace.points[result.trace.points.index(exception) + 1 :]
    assert any(p.kind == "LINE" and p.source["lineNumber"] == 5 for p in later)
    assert bindings(later[-1].snapshot["globals"])["x"]["value"] == "1"
    assert not hasattr(result.trace, "completion") and not hasattr(result.trace, "failed")


@pytest.mark.parametrize("caught", [True, False])
def test_each_propagation_callback_retained_and_unwind_pops_each_frame(caught):
    source = "def a():\n    b()\ndef b():\n    1 / 0\n"
    source += "try:\n    a()\nexcept ZeroDivisionError:\n    pass\n" if caught else "a()\n"
    result = trace_source(source)
    observations = [p for p in result.trace.points if p.kind == "EXCEPTION"]
    assert [frame(p)["name"] for p in observations] == ["b", "a", "<module>"]
    assert [p.frameId for p in observations] == ["f3", "f2", "f1"]
    assert [p.source["lineNumber"] for p in observations] == [4, 2, 6 if caught else 5]
    assert all(p.payload["diagnostic"]["code"] == "EXCEPTION_OBSERVED" for p in observations)
    assert result.exception_escaped is not caught
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize(
    "body",
    [
        "try:\n        1/0\n    except ZeroDivisionError:\n        x = 1\n    return x",
        "try:\n        1/0\n    finally:\n        x = 2",
        "try:\n        return 1\n    finally:\n        x = 2",
        "try:\n        1/0\n    finally:\n        return 3",
        "try:\n        return 1\n    finally:\n        raise ValueError('new')",
    ],
)
def test_exception_and_finally_lifecycle(body):
    result = trace_source("def f():\n    " + body + "\ny = f()\n")
    assert len(named(result.trace.points, "f", "RETURN")) == 1
    assert_trace_invariants(result.trace)
    returned = named(result.trace.points, "f", "RETURN")[0]
    if "x = 2" in body:
        assert bindings(frame(returned)["locals"])["x"]["value"] == "2"


def test_recursive_exception_caught_in_caller_preserves_distinct_frames():
    result = trace_source(
        "def f(n):\n    if n: return f(n-1)\n    raise ValueError('bottom')\n"
        "try:\n    f(3)\nexcept ValueError:\n    done = True\n"
    )
    observed = [p for p in result.trace.points if p.kind == "EXCEPTION"]
    assert [p.frameId for p in observed] == ["f5", "f4", "f3", "f2", "f1"]
    assert result.exception_escaped is False
    assert_trace_invariants(result.trace)


def test_reraise_and_reused_exception_are_not_deduplicated():
    result = trace_source(
        "e = ValueError('same')\nfor i in range(2):\n    try:\n"
        "        raise e\n    except ValueError:\n        pass\n"
    )
    observed = [p for p in result.trace.points if p.kind == "EXCEPTION"]
    assert len(observed) == 2
    assert [p.source["lineNumber"] for p in observed] == [4, 4]
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize("args,expected", [("'safe'", "safe"), ("42", None), ("", None)])
def test_custom_exception_str_repr_and_attribute_hooks_never_called(args, expected):
    result = trace_source(
        "hits = []\nclass Danger(Exception):\n"
        "    def __str__(self):\n        hits.append('str')\n        raise RuntimeError()\n"
        "    def __repr__(self):\n        hits.append('repr')\n        raise RuntimeError()\n"
        "    def __getattribute__(self, name):\n"
        "        hits.append('attribute')\n        raise RuntimeError()\n"
        f"try:\n    raise Danger({args})\nexcept Danger:\n    done = True\n"
    )
    diagnostic = next(p.payload["diagnostic"] for p in result.trace.points if p.kind == "EXCEPTION")
    assert diagnostic["typeName"] == "Danger"
    if expected is None:
        assert "unavailable" in diagnostic["message"]
    else:
        assert diagnostic["message"] == expected
    assert result.exception_escaped is False
    hits_ref = bindings(result.trace.points[-1].snapshot["globals"])["hits"]
    hits = next(
        o for o in result.trace.points[-1].snapshot["objects"] if o["id"] == hits_ref["$ref"]
    )
    assert hits["items"] == []
    assert named(result.trace.points, "__str__") == []
    assert named(result.trace.points, "__repr__") == []


@pytest.mark.parametrize("exception_type", [Exception, ValueError, KeyError, OSError])
def test_builtin_exception_with_hostile_args_is_not_formatted(exception_type):
    class Hostile:
        def __repr__(self):
            raise AssertionError("repr hook")

        def __str__(self):
            raise AssertionError("str hook")

    diagnostic = observed_exception(
        exception_type(Hostile()), {"fileName": "<codetrace-user>", "lineNumber": 1}
    )
    assert "unavailable" in diagnostic["message"]
    validate_contract(diagnostic, "trace", "Diagnostic")


def test_intrinsic_args_and_type_name_bypass_custom_descriptors():
    class Meta(type):
        @property
        def __name__(cls):
            raise AssertionError("metaclass name hook")

    class Danger(Exception, metaclass=Meta):
        @property
        def args(self):
            raise AssertionError("args hook")

    diagnostic = observed_exception(
        Danger("safe"), {"fileName": "<codetrace-user>", "lineNumber": None}
    )
    assert diagnostic["typeName"] == "Danger" and diagnostic["message"] == "safe"


@pytest.mark.parametrize("location", ["name", "message"])
def test_string_subclasses_in_exception_metadata_do_not_run_hooks(location):
    class HostileString(str):
        def __len__(self):
            raise AssertionError("len hook")

        def __getitem__(self, key):
            raise AssertionError("getitem hook")

        def __str__(self):
            raise AssertionError("str hook")

    class Danger(Exception):
        pass

    if location == "name":
        Danger.__name__ = HostileString("Danger")
        exception = Danger("safe")
    else:
        exception = Danger(HostileString("message"))
    diagnostic = observed_exception(exception, {"fileName": "<codetrace-user>", "lineNumber": 1})
    assert diagnostic["typeName"] == ("Exception" if location == "name" else "Danger")
    assert (
        diagnostic["message"] == "safe"
        if location == "name"
        else ("unavailable" in diagnostic["message"])
    )
    validate_contract(diagnostic, "trace", "Diagnostic")


@pytest.mark.parametrize("text", ["x", "😀", "\ud800", '"', "\n"])
def test_diagnostic_total_canonical_budget_and_explicit_truncation(text):
    diagnostic = observed_exception(
        ValueError(text * 20000), {"fileName": "<codetrace-user>", "lineNumber": 999}
    )
    assert canonical_size(diagnostic) <= DIAGNOSTIC_BYTES
    assert diagnostic["message"].endswith("[truncated]")
    validate_contract(diagnostic, "trace", "Diagnostic")


def test_exception_message_does_not_become_a_large_state_root():
    result = trace_source("raise ValueError('x' * 100000)\n")
    diagnostic = next(p.payload["diagnostic"] for p in result.trace.points if p.kind == "EXCEPTION")
    assert diagnostic["message"].endswith("[truncated]")
    assert result.exception_escaped is True
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize("source", ["raise SystemExit(0)", "raise KeyboardInterrupt()"])
def test_base_exception_escapes_harness_without_retaining_exception(source):
    result = trace_source(source)
    assert result.exception_escaped is True
    assert_trace_invariants(result.trace)
