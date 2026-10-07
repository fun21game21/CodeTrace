import pytest
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.trace_types import ReferenceTrace

from .helpers import assert_differential, frame, point, scalar, sequence, snapshot


def test_empty_trace_is_only_checkpoint_zero():
    result = TraceEncoder().encode(ReferenceTrace(()))
    assert result.events == ()
    assert result.states == (
        {
            "kind": "checkpoint",
            "version": 0,
            "state": {
                "version": 0,
                "globals": [],
                "frames": [],
                "stack": [],
                "objects": [],
                "eventRoots": [],
            },
        },
    )
    assert result.checkpointInterval == 128


def test_no_change_events_different_source_and_kind_keep_same_version():
    state = snapshot()
    reference = ReferenceTrace(
        (
            point(state, kind="CALL"),
            point(state, line=2),
            point(state, line=3),
            point(state, kind="RETURN", payload={"returnValueKnown": False, "returnValue": None}),
        )
    )
    encoded = TraceEncoder().encode(reference)
    assert [e.index for e in encoded.events] == [0, 1, 2, 3]
    assert [e.stateVersion for e in encoded.events] == [1, 1, 1, 1]
    assert len(encoded.states) == 2
    assert_differential(encoded, reference)


@pytest.mark.parametrize("interval", [1, 2, 3, 128])
def test_versions_checkpoints_and_nochange_follow_state_version_not_event_index(interval):
    states = [snapshot(globals_=[{"name": "x", "value": scalar(i)}]) for i in range(257)]
    reference = sequence(*(state for state in states for _ in range(2)))
    encoded = TraceEncoder(checkpoint_interval=interval).encode(reference)
    assert [e.stateVersion for e in encoded.events] == [v for v in range(1, 258) for _ in range(2)]
    assert len(encoded.states) == 258
    assert [r["version"] for r in encoded.states if r["kind"] == "checkpoint"] == (
        list(range(0, 258, interval))
    )
    for v in [0, 1, 127, 128, 129, 255, 256]:
        record = encoded.states[v]
        assert record["kind"] == ("checkpoint" if v % interval == 0 else "delta")
        if record["kind"] == "checkpoint":
            assert record["state"]["version"] == v
            assert record["state"] == {
                "version": v,
                **(
                    states[v - 1]
                    if v
                    else {"globals": [], "frames": [], "stack": [], "objects": [], "eventRoots": []}
                ),
            }
        else:
            assert record["baseVersion"] == v - 1
    # All event states must be correct; explicit seeks avoid O(N²) test work here.
    from .helpers import apply_record, seek

    decoded = None
    for v, record in enumerate(encoded.states):
        decoded = apply_record(decoded, record)
        if v:
            assert decoded == {"version": v, **states[v - 1]}
    for v in [0, 1, 127, 128, 129, 255, 256]:
        assert seek(encoded.states, v)["version"] == v


@pytest.mark.parametrize(
    "before,after",
    [
        ([], [("a", 1)]),
        ([("a", 1)], []),
        ([("a", 1)], [("a", 2)]),
        ([("a", 1)], [("b", 1)]),
        ([("a", 1)], [("a", 1), ("b", 2)]),
    ],
)
def test_globals_full_replacement_add_remove_rebind_empty(before, after):
    old = [{"name": n, "value": scalar(v)} for n, v in before]
    new = [{"name": n, "value": scalar(v)} for n, v in after]
    reference = sequence(snapshot(globals_=old), snapshot(globals_=new))
    encoded = TraceEncoder().encode(reference)
    assert encoded.states[2]["changes"] == {
        "globals": new,
        "stack": None,
        "eventRoots": None,
        "framesUpsert": [],
        "framesRemove": [],
        "objectsUpsert": [],
        "objectsRemove": [],
    }
    assert_differential(encoded, reference)


def test_frames_add_update_remove_and_stack_replacement():
    f1, f2 = frame(), frame("f2", "f1", "function")
    f2_changed = frame("f2", "f1", "function", [{"name": "x", "value": scalar(7)}])
    reference = sequence(
        snapshot(),
        snapshot(frames=[f1, f2], stack=["f1", "f2"]),
        snapshot(frames=[f1, f2_changed], stack=["f1", "f2"]),
        snapshot(),
    )
    encoded = TraceEncoder().encode(reference)
    add, update, remove = [r["changes"] for r in encoded.states[2:]]
    assert add["framesUpsert"] == [f2] and add["framesRemove"] == []
    assert add["stack"] == ["f1", "f2"]
    assert update["framesUpsert"] == [f2_changed] and update["stack"] is None
    assert remove["framesRemove"] == ["f2"] and remove["framesUpsert"] == []
    assert remove["stack"] == ["f1"]
    assert_differential(encoded, reference)


def test_numeric_frame_and_object_remove_order_is_not_lexical():
    ids = [1, 2, 9, 10, 11, 20]
    frames = [frame()]
    for i, value in enumerate(ids[1:]):
        frames.append(frame(f"f{value}", frames[-1]["id"], "function"))
    objects = [{"id": f"o{i}", "kind": "list", "items": []} for i in ids]
    roots = [{"$ref": f"o{i}"} for i in ids]
    old = snapshot(frames=frames, stack=[f["id"] for f in frames], objects=objects, roots=roots)
    new = snapshot()
    reference = sequence(old, new)
    encoded = TraceEncoder().encode(reference)
    delta = encoded.states[2]["changes"]
    assert delta["framesRemove"] == ["f2", "f9", "f10", "f11", "f20"]
    assert delta["objectsRemove"] == ["o1", "o2", "o9", "o10", "o11", "o20"]
    assert encoded.states[1]["changes"]["objectsUpsert"] == objects
    assert_differential(encoded, reference)


def test_eventroots_alone_create_versions_and_empty_replacement():
    obj = {"id": "o1", "kind": "list", "items": []}
    binding = [{"name": "x", "value": {"$ref": "o1"}}]
    states = [
        snapshot(globals_=binding, objects=[obj]),
        snapshot(globals_=binding, objects=[obj], roots=[{"$ref": "o1"}]),
        snapshot(globals_=binding, objects=[obj]),
    ]
    reference = sequence(*states)
    encoded = TraceEncoder().encode(reference)
    a, b = encoded.states[2]["changes"], encoded.states[3]["changes"]
    assert a["eventRoots"] == [{"$ref": "o1"}] and b["eventRoots"] == []
    for changes in [a, b]:
        assert changes["globals"] is None and changes["objectsUpsert"] == []
        assert changes["framesUpsert"] == [] and changes["stack"] is None
    assert_differential(encoded, reference)


def test_forward_diff_can_replace_stack_with_empty_without_inventing_a_user_event():
    # A final empty stack will be supplied by the execution shell later. Verify
    # the delta rule directly, without adding a fake TRACING point or RUN_END here.
    from codetrace_runner.encoder import _changes

    from .helpers import apply_record

    old = {"version": 1, **snapshot()}
    new = {"version": 2, **snapshot(frames=[], stack=[])}
    changes = _changes(old, new)
    assert changes["stack"] == [] and changes["framesRemove"] == ["f1"]
    delta = {"kind": "delta", "version": 2, "baseVersion": 1, "changes": changes}
    assert apply_record(old, delta) == new


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.0, True, None])
def test_encoded_scalar_comparison_has_no_python_nan_instability(value):
    state = snapshot(globals_=[{"name": "x", "value": scalar(value)}])
    encoded = TraceEncoder().encode(sequence(state, state, state))
    assert [e.stateVersion for e in encoded.events] == [1, 1, 1]
    assert len(encoded.states) == 2
