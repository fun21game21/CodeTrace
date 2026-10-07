import random

import pytest
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.reference import trace_source

from .helpers import assert_differential, scalar, sequence, snapshot

PROGRAMS = [
    "",
    "pass",
    "x = 1\nx = 2\n",
    "for i in range(4):\n    x = i\n",
    "def f(x):\n    return x+1\ny = f(3)\n",
    "def f(n):\n    if n < 2: return 1\n    return n*f(n-1)\ny = f(4)\n",
    "try:\n    1/0\nexcept ZeroDivisionError:\n    x = 1\n",
    "class A:\n    x = 1\n    def m(self): return 2\na = A()\ny = a.m()\n",
    "def make():\n    return [1,2]\nx = make()\n",
    "a = []\na.append(a)\nb = a\n",
    "a = []\nb = [a]\na.append(b)\na.append(7)\n",
    "shared = []\ndef outer(a):\n    def inner(b):\n        b.append(1)\n"
    "        return b\n    return inner(a)\nx = outer(shared)\n",
    "class N:\n    def __init__(self, value):\n        self.value = value\n"
    "        self.left = None\n"
    "        self.right = None\nroot = N(1)\nroot.left = N(2)\nroot.right = N(3)\n"
    "def dfs(n):\n    if n is None: return\n    dfs(n.left)\n    dfs(n.right)\ndfs(root)\n",
    "from collections import deque\ng = {0:[1,2],1:[2],2:[]}\nq = deque([0])\nseen = set()\n"
    "while q:\n    node = q.popleft()\n    if node not in seen:\n        seen.add(node)\n"
    "        q.extend(g[node])\n",
    "def a(n):\n    if n: return b(n-1)\n    return 0\ndef b(n): return a(n)\nx = a(3)\n",
    "def a():\n    b()\ndef b():\n    raise ValueError('boom')\ntry:\n    a()\n"
    "except ValueError:\n    handled = True\n",
    "def f():\n    try:\n        return 1\n    finally:\n        x = 2\ny = f()\n",
]


@pytest.mark.parametrize("source", PROGRAMS)
@pytest.mark.parametrize("interval", [1, 2, 128])
def test_each_real_tracing_event_equals_independent_reconstruction(source, interval):
    reference = trace_source(source).trace
    encoded = TraceEncoder(checkpoint_interval=interval).encode(reference)
    decoded = assert_differential(encoded, reference)
    order = list(range(len(encoded.events)))
    random.Random(0).shuffle(order)
    from .helpers import seek

    for index in order + list(reversed(range(len(encoded.events)))):
        event = encoded.events[index]
        assert seek(encoded.states, event.stateVersion) == decoded[event.stateVersion]


def test_return_snapshot_and_later_frame_removal_are_not_reinterpreted():
    reference = trace_source("def f():\n    a = []\n    return a\nx = f()\ny = 1\n").trace
    encoded = TraceEncoder().encode(reference)
    i = next(i for i, p in enumerate(reference.points) if p.kind == "RETURN" and p.frameId == "f2")
    returned, following = encoded.events[i], encoded.events[i + 1]
    assert returned.payload == reference.points[i].payload
    assert encoded.states[following.stateVersion]["changes"]["framesRemove"] == ["f2"]
    assert encoded.states[following.stateVersion]["changes"]["eventRoots"] == []
    assert returned.payload["returnValue"] == reference.points[i].snapshot["eventRoots"][0]
    assert_differential(encoded, reference)


def test_exception_metadata_alone_does_not_create_a_state_version():
    reference = trace_source("try:\n    1/0\nexcept ZeroDivisionError:\n    pass\n").trace
    encoded = TraceEncoder().encode(reference)
    i = next(i for i, e in enumerate(encoded.events) if e.kind == "EXCEPTION")
    assert encoded.events[i].stateVersion == encoded.events[i - 1].stateVersion
    assert encoded.events[i].payload == reference.points[i].payload
    assert_differential(encoded, reference)


@pytest.mark.parametrize("seed", range(25))
def test_seeded_snapshot_graphs_mutations_rebinding_reachability_and_cycles(seed):
    rng = random.Random(seed)
    records = {f"o{i}": {"id": f"o{i}", "kind": "list", "items": []} for i in range(1, 6)}
    states = []
    for step in range(20):
        oid = rng.choice(list(records))
        records[oid]["items"] = [scalar(step), {"$ref": rng.choice(list(records))}]
        root = rng.choice(list(records))
        reachable, pending = set(), [root]
        while pending:
            current = pending.pop()
            if current not in reachable:
                reachable.add(current)
                pending.extend(v["$ref"] for v in records[current]["items"] if "$ref" in v)
        from .helpers import copy_json

        states.append(
            snapshot(
                globals_=[{"name": "x", "value": {"$ref": root}}],
                objects=[copy_json(records[oid]) for oid in sorted(reachable)],
            )
        )
        if step % 3 == 0:
            states.append(states[-1])
    reference = sequence(*states)
    encoded = TraceEncoder(checkpoint_interval=4).encode(reference)
    assert_differential(encoded, reference)


def test_loop_equal_states_preserves_every_observed_event():
    reference = trace_source("for i in [0,0,0,0]: pass\n").trace
    encoded = TraceEncoder().encode(reference)
    assert len(encoded.events) == len(reference.points)
    assert len(encoded.states) < len(encoded.events)
    assert_differential(encoded, reference)


def test_test_decoder_checks_refs_after_the_whole_delta_not_partial_fields():
    from .helpers import apply_record

    before = {
        "version": 1,
        **snapshot(
            globals_=[{"name": "x", "value": {"$ref": "o1"}}],
            objects=[{"id": "o1", "kind": "list", "items": []}],
        ),
    }
    delta = {
        "kind": "delta",
        "version": 2,
        "baseVersion": 1,
        "changes": {
            "globals": [{"name": "x", "value": {"$ref": "o2"}}],
            "stack": None,
            "eventRoots": None,
            "framesUpsert": [],
            "framesRemove": [],
            "objectsUpsert": [{"id": "o2", "kind": "list", "items": []}],
            "objectsRemove": ["o1"],
        },
    }
    assert apply_record(before, delta)["objects"][0]["id"] == "o2"


def test_decoder_detects_a_corrupted_upsert_independently_of_encoder():
    from .helpers import copy_json, seek

    reference = sequence(snapshot(globals_=[{"name": "x", "value": scalar(1)}]))
    encoded = TraceEncoder().encode(reference)
    corrupted = copy_json(list(encoded.states))
    corrupted[1]["changes"]["globals"][0]["value"]["value"] = "999"
    assert seek(corrupted, 1) != {"version": 1, **reference.points[0].snapshot}
