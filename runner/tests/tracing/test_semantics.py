import dataclasses

import pytest
from codetrace_runner.reference import trace_source

from .helpers import assert_trace_invariants, bindings, frame, named, record
from .semantic_cases import CASES


@pytest.mark.parametrize("case", CASES)
def test_normative_independent_event_fixtures(case):
    source, rows = CASES[case]
    result = trace_source(source)
    kinds = {"C": "CALL", "L": "LINE", "R": "RETURN", "E": "EXCEPTION"}
    expected = []
    for row in rows.split(";"):
        kind, line, stack = row.split()
        stack_ids = [f"f{suffix}" for suffix in stack.split("/")]
        expected.append((kinds[kind], int(line) or None, stack_ids[-1], stack_ids))
    assert [
        (p.kind, p.source["lineNumber"], p.frameId, p.snapshot["stack"])
        for p in result.trace.points
    ] == expected
    assert result.exception_escaped is False
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize("source", ["", "\n", "# comment\n", "\n# comment\n\n"])
def test_empty_has_no_invented_line(source):
    points = trace_source(source).trace.points
    assert [p.kind for p in points] == ["CALL", "RETURN"]
    assert all(p.source == {"fileName": "<codetrace-user>", "lineNumber": None} for p in points)
    assert all(p.snapshot["stack"] == ["f1"] for p in points)


def test_line_is_before_assignment_and_semicolon_is_one_observation():
    points = trace_source("x = 1\nx = 2\nx = 3; y = 4\n").trace.points
    lines = [p for p in points if p.kind == "LINE"]
    assert bindings(lines[0].snapshot["globals"]) == {}
    assert bindings(lines[1].snapshot["globals"])["x"]["value"] == "1"
    assert bindings(lines[2].snapshot["globals"])["x"]["value"] == "2"
    assert bindings(points[-1].snapshot["globals"])["x"]["value"] == "3"
    assert bindings(points[-1].snapshot["globals"])["y"]["value"] == "4"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_multiline_expression_and_line_endings(ending):
    result = trace_source(ending.join(["x = (", "    1 +", "    2", ")", "y = x"]))
    assert bindings(result.trace.points[-1].snapshot["globals"])["y"]["value"] == "3"
    for p in result.trace.points:
        if p.kind == "LINE" and p.source["lineNumber"] < 5:
            assert "x" not in bindings(p.snapshot["globals"])
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize(
    "loop", ["for i in range(4):\n    pass", "i = 0\nwhile i < 4:\n    i += 1"]
)
def test_repeated_loop_lines(loop):
    result = trace_source(loop)
    lines = [p.source["lineNumber"] for p in result.trace.points if p.kind == "LINE"]
    assert max(lines.count(line) for line in lines) >= 4
    assert_trace_invariants(result.trace)


def test_equal_state_and_line_do_not_deduplicate_events():
    points = trace_source("for i in [0, 0, 0]: pass\n").trace.points
    lines = [p for p in points if p.kind == "LINE"]
    assert len(lines) == 4
    assert lines[1].snapshot == lines[2].snapshot == lines[3].snapshot
    assert lines[1].snapshot is not lines[2].snapshot


@pytest.mark.parametrize("source", ["pass", "x=1", "def f():\n    pass\nf()", "1/0"])
def test_module_lifecycle_globals_not_duplicated(source):
    result = trace_source(source)
    first, last = result.trace.points[0], result.trace.points[-1]
    assert first.kind == "CALL" and last.kind == "RETURN"
    assert first.frameId == last.frameId == "f1"
    assert frame(first)["kind"] == "module"
    assert frame(first)["parentId"] is None
    assert frame(first)["callSite"] is None
    for p in result.trace.points:
        module = frame(p, "f1")
        assert module["localsSource"] == "globals" and module["locals"] == []
        assert module["parameterNames"] == []
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize(
    "expr,kind,value",
    [
        ("0", "int", "0"),
        ("False", "bool", False),
        ("''", "str", ""),
        ("2.5", "float", "2.5"),
        ("-0.0", "float", "-0.0"),
    ],
)
def test_non_none_return_is_known_even_if_falsey(expr, kind, value):
    points = trace_source(f"def f():\n    return {expr}\ny = f()\n").trace.points
    returned = named(points, "f", "RETURN")[0]
    assert returned.payload == {
        "returnValueKnown": True,
        "returnValue": {"kind": "scalar", "type": kind, "value": value},
    }
    assert returned.snapshot["eventRoots"] == [returned.payload["returnValue"]]


@pytest.mark.parametrize("body", ["return None", "return", "pass", "1 / 0"])
def test_none_return_is_unknown_including_unwind(body):
    result = trace_source(f"def f():\n    {body}\nf()\n")
    returned = named(result.trace.points, "f", "RETURN")[0]
    assert returned.payload == {"returnValueKnown": False, "returnValue": None}
    assert returned.snapshot["eventRoots"] == []
    assert result.exception_escaped is (body == "1 / 0")
    assert_trace_invariants(result.trace)


def test_return_snapshot_has_locals_then_removes_only_that_frame():
    points = trace_source("def f():\n    x = 7\n    return x\ny = f()\nz = 9\n").trace.points
    returned = named(points, "f", "RETURN")[0]
    assert bindings(frame(returned)["locals"])["x"]["value"] == "7"
    following = points[points.index(returned) + 1]
    assert following.snapshot["stack"] == ["f1"]
    assert all(f["id"] != returned.frameId for f in following.snapshot["frames"])


@pytest.mark.parametrize(
    "expr,kind", [("[1]", "list"), ("{'a': 1}", "dict"), ("Node()", "instance")]
)
def test_return_only_object_is_root_before_caller_binding(expr, kind):
    points = trace_source(
        f"class Node: pass\ndef make():\n    return {expr}\nx = make()\n"
    ).trace.points
    returned = named(points, "make", "RETURN")[0]
    assert "x" not in bindings(returned.snapshot["globals"])
    assert frame(returned)["locals"] == []
    ref = returned.payload["returnValue"]
    assert record(returned, ref)["kind"] == kind
    assert returned.snapshot["eventRoots"] == [ref]
    assert bindings(points[-1].snapshot["globals"])["x"] == ref


def test_return_existing_alias_keeps_object_id():
    points = trace_source("a = []\ndef f():\n    return a\nb = f()\n").trace.points
    p = named(points, "f", "RETURN")[0]
    assert p.payload["returnValue"] == bindings(p.snapshot["globals"])["a"]
    final = bindings(points[-1].snapshot["globals"])
    assert final["a"] == final["b"] == p.payload["returnValue"]


def test_nested_calls_metadata_and_all_active_locals():
    result = trace_source(
        "def outer(a):\n    x = [a]\n    def inner(b):\n        return b\n"
        "    return inner(x)\ny = outer(3)\n"
    )
    p = named(result.trace.points, "inner", "CALL")[0]
    outer, inner = frame(p, "f2"), frame(p)
    assert p.snapshot["stack"] == ["f1", "f2", "f3"]
    assert outer["qualifiedName"] == "outer" and inner["qualifiedName"] == "outer.<locals>.inner"
    assert inner["definitionLine"] == 3
    assert inner["callSite"] == {"fileName": "<codetrace-user>", "lineNumber": 5}
    assert bindings(inner["locals"])["b"] == bindings(outer["locals"])["x"]
    assert bindings(outer["locals"])["a"]["value"] == "3"
    assert_trace_invariants(result.trace)


def test_factorial_four_unique_live_frames():
    result = trace_source(
        "def factorial(n):\n    if n == 1: return 1\n"
        "    return n * factorial(n-1)\ny = factorial(4)\n"
    )
    calls = named(result.trace.points, "factorial", "CALL")
    assert [p.frameId for p in calls] == ["f2", "f3", "f4", "f5"]
    assert [bindings(frame(p)["locals"])["n"]["value"] for p in calls] == ["4", "3", "2", "1"]
    assert calls[-1].snapshot["stack"] == ["f1", "f2", "f3", "f4", "f5"]
    assert bindings(result.trace.points[-1].snapshot["globals"])["y"]["value"] == "24"
    assert_trace_invariants(result.trace)


def test_mutual_recursion_and_repeated_calls_never_reuse_frame_ids():
    result = trace_source(
        "def a(n):\n    if n: return b(n-1)\n    return 0\n"
        "def b(n):\n    return a(n)\nx = a(2)\ny = a(2)\n"
    )
    assert len(named(result.trace.points, "a", "CALL")) == 6
    assert len(named(result.trace.points, "b", "CALL")) == 4
    assert_trace_invariants(result.trace)
    assert trace_source("pass").trace.points[0].frameId == "f1"


def test_nested_class_method_lambda_are_not_classified_by_name():
    result = trace_source(
        "def outer():\n    class A:\n        x = 1\n"
        "        def m(self): return 2\n    return A\n"
        "A = outer()\na = A()\ny = a.m()\nz = (lambda: 3)()\n"
    )
    calls = [p for p in result.trace.points if p.kind == "CALL"]
    assert [(frame(p)["name"], frame(p)["kind"]) for p in calls] == [
        ("<module>", "module"),
        ("outer", "function"),
        ("A", "class"),
        ("m", "function"),
        ("<lambda>", "function"),
    ]
    cls = named(result.trace.points, "A", "RETURN")[0]
    assert frame(cls)["parameterNames"] == [] and frame(cls)["localsSource"] == "own"
    assert bindings(frame(cls)["locals"])["x"]["value"] == "1"
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize(
    "params,args,names",
    [
        ("a, b", "1, 2", ["a", "b"]),
        ("a, /, b", "1, 2", ["a", "b"]),
        ("*, k", "k=1", ["k"]),
        ("*args", "1, 2", ["args"]),
        ("**kwargs", "k=1", ["kwargs"]),
        (
            "a, /, b=2, *args, c=3, d=4, **kwargs",
            "1, 2, 5, c=6, z=7",
            ["a", "b", "args", "c", "d", "kwargs"],
        ),
    ],
)
def test_parameters_declaration_order_and_arguments_present_on_call(params, args, names):
    points = trace_source(f"def f({params}):\n    body = 1\nf({args})\n").trace.points
    call = named(points, "f", "CALL")[0]
    assert frame(call)["parameterNames"] == names
    assert set(bindings(frame(call)["locals"])) == set(names)
    assert frame(call)["definitionLine"] == 1
    assert frame(call)["callSite"]["lineNumber"] == 3


def test_reference_shell_is_frozen_and_has_no_production_fields():
    result = trace_source("pass")
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.trace.points = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.trace.points[0].kind = "LINE"
    assert_trace_invariants(result.trace)
