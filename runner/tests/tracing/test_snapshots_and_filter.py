import collections
import dataclasses

import pytest
from codetrace_runner.canonical import canonical_json
from codetrace_runner.reference import trace_source

from .helpers import assert_trace_invariants, bindings, frame, json_tree, named, record


def test_shared_mutation_all_frames_and_old_snapshots_are_detached():
    result = trace_source(
        "shared = []\ndef outer(a):\n    held = {'child': a}\n"
        "    def inner(b):\n        b.append(7)\n        return b\n"
        "    return inner(a)\nx = outer(shared)\nshared.append(9)\n"
    )
    call = named(result.trace.points, "inner", "CALL")[0]
    returned = named(result.trace.points, "inner", "RETURN")[0]
    ref = bindings(call.snapshot["globals"])["shared"]
    assert bindings(frame(call)["locals"])["b"] == ref
    assert bindings(frame(call, "f2")["locals"])["a"] == ref
    held = record(call, bindings(frame(call, "f2")["locals"])["held"])
    assert held["entries"][0]["value"] == ref
    assert record(call, ref)["items"] == []
    assert [v["value"] for v in record(returned, ref)["items"]] == ["7"]
    assert [v["value"] for v in record(result.trace.points[-1], ref)["items"]] == ["7", "9"]
    saved = canonical_json([dataclasses.asdict(p) for p in result.trace.points])
    trace_source("shared = [100]\n")
    assert canonical_json([dataclasses.asdict(p) for p in result.trace.points]) == saved
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize(
    "source",
    [
        "a = []\na.append(a)\n",
        "a = {}\na['self'] = a\n",
        "a = []\nb = (a,)\na.append(b)\n",
        "from collections import deque\na = deque()\na.append(a)\n",
        "class Node: pass\na = Node()\na.next = a\n",
    ],
)
def test_cycles_preserve_identity_through_engine(source):
    result = trace_source(source)
    assert_trace_invariants(result.trace)
    final = result.trace.points[-1]
    ref = bindings(final.snapshot["globals"])["a"]
    obj = record(final, ref)
    if obj["kind"] in ("list", "deque"):
        child = obj["items"][0]
        assert child == ref or record(final, child)["items"][0] == ref
    elif obj["kind"] == "dict":
        assert obj["entries"][0]["value"] == ref
    else:
        assert obj["attrs"][0]["value"] == ref


@pytest.mark.parametrize(
    "source",
    [
        "import math\nx = math.sqrt(4)\n",
        "from collections import deque\nx = deque([1,2])\nx.append(3)\n",
        "def callback(x): return x + 1\nfrom collections import deque\n"
        "result = deque(map(callback, [1,2]))\n",
    ],
)
def test_supported_libraries_and_c_callbacks_have_only_user_frames(source):
    result = trace_source(source)
    for p in result.trace.points:
        assert p.source["fileName"] == "<codetrace-user>"
        assert frame(p)["name"] in ("<module>", "callback")
    if "callback" in source:
        calls = named(result.trace.points, "callback", "CALL")
        assert len(calls) == 2
        assert all(frame(p)["parentId"] == "f1" for p in calls)
        assert all(frame(p)["callSite"]["lineNumber"] == 3 for p in calls)
    assert_trace_invariants(result.trace)


def test_python_library_frame_between_users_is_skipped_for_parent_and_callsite(monkeypatch):
    # A trusted test shim supplies a Python library bridge; not a supported import extension.
    def library_bridge(callback):
        return callback(42)

    monkeypatch.setattr(collections, "_codetrace_test_bridge", library_bridge, raising=False)
    result = trace_source(
        "import collections\ndef callback(x):\n    return x\n"
        "y = collections._codetrace_test_bridge(callback)\n"
    )
    p = named(result.trace.points, "callback", "CALL")[0]
    assert p.snapshot["stack"] == ["f1", "f2"]
    assert frame(p)["parentId"] == "f1" and frame(p)["callSite"]["lineNumber"] == 4
    assert all(frame(p)["name"] != "library_bridge" for p in result.trace.points)
    assert_trace_invariants(result.trace)


@pytest.mark.parametrize("name", ["serialize", "callback", "trace_source", "_observe", "__str__"])
def test_user_function_names_matching_helpers_are_not_filtered(name):
    points = trace_source(f"def {name}(x): return x\ny = {name}(5)\n").trace.points
    assert len(named(points, name, "CALL")) == 1


@pytest.mark.parametrize(
    "source",
    [
        "a = []\nb = a\na.append(1)\n",
        "def f(x):\n    y = [x]\n    return y\nz = f(1)\n",
        "try:\n    1/0\nexcept Exception as e:\n    saved = e\n",
        "class A: pass\na = A()\na.self = a\n",
    ],
)
def test_entire_reference_output_has_only_dto_values(source):
    result = trace_source(source)
    json_tree(dataclasses.asdict(result))
    assert_trace_invariants(result.trace)


def test_snapshots_do_not_alias_live_dicts_or_containers(monkeypatch):
    from codetrace_runner.serializer import GraphSerializer

    original = GraphSerializer.serialize
    saved_snapshot_text = []

    def observe(self, user_globals, frames=(), stack=(), event_roots=()):
        live_ids = {id(user_globals)}
        for f in frames:
            live_ids.add(id(f.live_locals))
            live_ids.update(id(v) for v in f.live_locals.values() if type(v) in (dict, list))
        live_ids.update(id(v) for v in user_globals.values() if type(v) in (dict, list))
        snapshot = original(self, user_globals, frames, stack, event_roots)
        pending = [snapshot]
        while pending:
            value = pending.pop()
            if type(value) in (dict, list):
                assert id(value) not in live_ids
                pending.extend(value.values() if type(value) is dict else value)
        saved_snapshot_text.append(canonical_json(snapshot))
        return snapshot

    monkeypatch.setattr(GraphSerializer, "serialize", observe)
    result = trace_source("a = []\ndef f(x):\n    x.append(1)\n    return x\ny = f(a)\n")
    assert [canonical_json(p.snapshot) for p in result.trace.points] == saved_snapshot_text
