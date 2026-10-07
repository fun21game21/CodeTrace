import dataclasses
import inspect

import pytest
from codetrace_runner.canonical import canonical_json
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.encoder_errors import TraceEncodingError
from codetrace_runner.errors import ValueLimitError
from codetrace_runner.graph_errors import GraphLimitError
from codetrace_runner.reference import trace_source
from codetrace_runner.trace_engine import ReferenceLimitError
from codetrace_runner.trace_types import ReferenceTrace

from .helpers import assert_differential, point, scalar, sequence, snapshot


def result_json(result):
    return canonical_json(
        {
            "events": [e.as_dict() for e in result.events],
            "states": list(result.states),
            "checkpointInterval": result.checkpointInterval,
            "accounting": dataclasses.asdict(result.accounting),
        }
    )


def reference_json(reference):
    return canonical_json([dataclasses.asdict(p) for p in reference.points])


def test_encoder_does_not_mutate_input_or_share_mutable_input_containers():
    reference = trace_source("a = []\ndef f(x):\n    x.append(1)\n    return x\ny = f(a)\n").trace
    original = reference_json(reference)
    encoded = TraceEncoder(checkpoint_interval=2).encode(reference)
    assert reference_json(reference) == original
    encoded_original = result_json(encoded)
    for p in reference.points:
        p.snapshot["globals"].clear()
        p.snapshot["objects"].clear()
        p.source["lineNumber"] = 999
        p.payload.clear()
    assert result_json(encoded) == encoded_original


def test_later_points_do_not_change_historical_checkpoint_delta_or_core():
    prefix = trace_source("a = [1]\na.append(2)\na.append(3)\n").trace
    encoder = TraceEncoder(checkpoint_interval=2)
    shorter = ReferenceTrace(prefix.points[:3])
    first = encoder.encode(shorter)
    saved = result_json(first)
    full = encoder.encode(prefix)
    assert result_json(first) == saved
    assert full.states[: len(first.states)] == first.states
    assert full.events[: len(first.events)] == first.events


@pytest.mark.parametrize("interval", [1, 2, 128])
def test_same_trace_repeated_encoding_is_canonical_identical(interval):
    reference = trace_source("x = []\nx.append(x)\ny = x\nfor i in [0,0]: pass\n").trace
    encoder = TraceEncoder(checkpoint_interval=interval)
    before = reference_json(reference)
    assert result_json(encoder.encode(reference)) == result_json(encoder.encode(reference))
    assert reference_json(reference) == before


def test_json_field_insertion_order_does_not_create_state_change():
    state = snapshot(globals_=[{"name": "x", "value": scalar(1)}])
    reversed_fields = dict(reversed(list(state.items())))
    reference = sequence(state, reversed_fields)
    encoded = TraceEncoder().encode(reference)
    assert [e.stateVersion for e in encoded.events] == [1, 1]
    assert_differential(encoded, reference)


def test_output_shells_frozen_and_no_reverse_patch_or_future_module_fields():
    result = TraceEncoder().encode(trace_source("pass").trace)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.events[0].index = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.accounting.eventCoreBytes = 0
    for event in result.events:
        assert set(event.as_dict()) == {
            "index",
            "kind",
            "stateVersion",
            "frameId",
            "source",
            "payload",
        }
        assert event.kind != "RUN_END"
    for record in result.states:
        if record["kind"] == "delta":
            assert set(record) == {"kind", "version", "baseVersion", "changes"}
            assert set(record["changes"]) == {
                "globals",
                "stack",
                "eventRoots",
                "framesUpsert",
                "framesRemove",
                "objectsUpsert",
                "objectsRemove",
            }
    assert set(dataclasses.asdict(result)) == {
        "events",
        "states",
        "checkpointInterval",
        "accounting",
    }


def test_api_needs_only_reference_dtos_not_registry_or_live_frames():
    assert list(inspect.signature(TraceEncoder.encode).parameters) == ["self", "reference_trace"]
    encoder = TraceEncoder()
    reference = trace_source("x = 1").trace
    result = encoder.encode(reference)
    assert all(type(v) is int for v in vars(encoder).values())
    assert reference_json(reference)
    assert result_json(result)


@pytest.mark.parametrize("location", ["snapshot", "value", "key", "source", "payload", "kind"])
def test_non_dto_objects_and_subclasses_rejected_without_user_hooks(location):
    class Hostile:
        def __eq__(self, other):
            raise AssertionError("equality hook")

        def __repr__(self):
            raise AssertionError("repr hook")

        def __str__(self):
            raise AssertionError("str hook")

        def __hash__(self):
            raise AssertionError("hash hook")

        def __iter__(self):
            raise AssertionError("iterator hook")

    class HostileString(str):
        def __eq__(self, other):
            raise AssertionError("str equality hook")

        def __hash__(self):
            return str.__hash__(self)

    class HostileDict(dict):
        def __iter__(self):
            raise AssertionError("dict iterator hook")

    state = snapshot()
    p = point(state)
    if location == "snapshot":
        p = dataclasses.replace(p, snapshot=HostileDict(state))
    elif location == "value":
        state["globals"] = [{"name": "x", "value": Hostile()}]
    elif location == "key":
        state[HostileString("extra")] = []
    elif location == "source":
        p = dataclasses.replace(p, source=Hostile())
    elif location == "payload":
        p = dataclasses.replace(p, payload=HostileDict())
    else:
        p = dataclasses.replace(p, kind=HostileString("LINE"))
    with pytest.raises(TraceEncodingError):
        TraceEncoder().encode(ReferenceTrace((p,)))


@pytest.mark.parametrize(
    "variant",
    [
        "ref",
        "duplicate_objects",
        "duplicate_frames",
        "stack",
        "parent",
        "order",
        "extra_field",
        "input_version",
        "live_cycle",
    ],
)
def test_invalid_snapshot_invariants_do_not_produce_encoded_success(variant):
    state = snapshot()
    if variant == "ref":
        state["globals"] = [{"name": "x", "value": {"$ref": "o9"}}]
    elif variant == "duplicate_objects":
        obj = {"id": "o1", "kind": "list", "items": []}
        state["objects"] = [obj, obj]
    elif variant == "duplicate_frames":
        state["frames"] *= 2
    elif variant == "stack":
        state["stack"] = ["f9"]
    elif variant == "parent":
        state["frames"][0]["parentId"] = "f2"
    elif variant == "order":
        state["globals"] = [{"name": "z", "value": scalar(1)}, {"name": "a", "value": scalar(1)}]
    elif variant == "extra_field":
        state["unknown"] = []
    elif variant == "input_version":
        state["version"] = 1
    else:
        state["globals"].append(state)
    with pytest.raises(TraceEncodingError):
        TraceEncoder().encode(sequence(state))


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "RUN_END"},
        {"kind": "LINE", "source": None},
        {"source": {"fileName": "library.py", "lineNumber": 1}},
        {"source": {"fileName": "<codetrace-user>", "lineNumber": 0}},
        {"source": {"fileName": "<codetrace-user>", "lineNumber": True}},
        {"payload": {"extra": 1}},
        {"frameId": "f9"},
        {"kind": "RETURN", "payload": {"returnValueKnown": True, "returnValue": {"$ref": "o9"}}},
        {"kind": "RETURN", "payload": {"returnValueKnown": True, "returnValue": scalar(None)}},
        {"kind": "RETURN", "payload": {"returnValueKnown": False, "returnValue": scalar(1)}},
        {"kind": "RETURN", "payload": {"returnValueKnown": 1, "returnValue": scalar(1)}},
        {"kind": "EXCEPTION", "payload": {}},
    ],
)
def test_invalid_event_core_metadata_and_return_reference_rejected(changes):
    p = dataclasses.replace(point(snapshot()), **changes)
    with pytest.raises(TraceEncodingError):
        TraceEncoder().encode(ReferenceTrace((p,)))


@pytest.mark.parametrize(
    "error",
    [
        ValueLimitError("REGISTRY_LIMIT"),
        GraphLimitError("COLLECTION_LIMIT", "test"),
        ReferenceLimitError(ValueLimitError("STRING_LIMIT").diagnostic),
    ],
)
def test_existing_values_serializer_tracing_limit_errors_are_not_reclassified(monkeypatch, error):
    from codetrace_runner import encoder as implementation

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(implementation, "validate_contract", fail)
    with pytest.raises(type(error)) as caught:
        TraceEncoder().encode(sequence(snapshot()))
    assert caught.value is error


@pytest.mark.parametrize("reference", [None, {}, (), ReferenceTrace([])])
def test_invalid_reference_api_input(reference):
    with pytest.raises(TraceEncodingError):
        TraceEncoder().encode(reference)


def test_test_decoder_does_not_mutate_published_records():
    from .helpers import seek

    reference = trace_source("a = [1]\na.append(2)\na.append(3)\n").trace
    result = TraceEncoder(checkpoint_interval=2).encode(reference)
    before = result_json(result)
    restored = seek(result.states, len(result.states) - 1)
    restored["globals"].clear()
    restored["objects"].clear()
    assert result_json(result) == before


def test_duplicate_frame_code_and_object_aliases_are_not_coalesced():
    reference = trace_source(
        "def f(n):\n    if n: return f(n-1)\n    return []\nx = f(2)\ny = x\n"
    ).trace
    result = TraceEncoder().encode(reference)
    restored = assert_differential(result, reference)
    assert any(state["stack"] == ["f1", "f2", "f3", "f4"] for state in restored)
    globals_ = {b["name"]: b["value"] for b in restored[-1]["globals"]}
    assert globals_["x"] == globals_["y"]
