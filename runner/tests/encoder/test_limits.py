import pytest
from codetrace_contracts import default_limits, validate_contract
from codetrace_runner.canonical import canonical_json, canonical_size
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.encoder_errors import EncoderLimitError
from codetrace_runner.graph_types import FrameInput
from codetrace_runner.registry import ObjectRegistry
from codetrace_runner.serializer import GraphSerializer
from codetrace_runner.trace_types import ReferenceTrace

from .helpers import point, scalar, sequence, snapshot


@pytest.mark.parametrize("maximum", [1, 2, 3, 10])
def test_effective_event_boundary_reserves_exactly_one_run_end(maximum):
    limits = default_limits()
    limits["max_events"] = maximum
    p = point(snapshot())
    encoder = TraceEncoder(limits)
    assert len(encoder.encode(ReferenceTrace((p,) * (maximum - 1))).events) == maximum - 1
    with pytest.raises(EncoderLimitError) as error:
        encoder.encode(ReferenceTrace((p,) * maximum))
    assert error.value.reason == "EVENT_LIMIT"
    validate_contract(error.value.diagnostic, "trace", "Diagnostic")


def test_normative_99999_python_events_and_100000_rejected():
    p = point(snapshot())
    encoder = TraceEncoder()
    encoded = encoder.encode(ReferenceTrace((p,) * 99999))
    assert len(encoded.events) == 99999 and encoded.events[-1].index == 99998
    assert len(encoded.states) == 2
    assert all(e.stateVersion == 1 for e in encoded.events)
    with pytest.raises(EncoderLimitError) as error:
        encoder.encode(ReferenceTrace((p,) * 100000))
    assert error.value.reason == "EVENT_LIMIT"


def sized_snapshot(normalized_size):
    # Many supported, individually small strings; not a single oversized SERIALIZER scalar.
    state = snapshot(
        globals_=[{"name": f"v{i:03}", "value": scalar("x" * 16384)} for i in range(64)]
    )
    difference = normalized_size - canonical_size({"version": 1, **state})
    state["globals"][0]["value"]["value"] = "x" * (16384 + difference)
    assert 0 <= len(state["globals"][0]["value"]["value"]) <= 16384
    assert canonical_size({"version": 1, **state}) == normalized_size
    return state


@pytest.mark.parametrize("offset", [0, 1])
def test_normative_full_state_bytes_exact_and_one_above(offset):
    limit = default_limits()["state_bytes"]
    state = sized_snapshot(limit + offset)
    if offset:
        with pytest.raises(EncoderLimitError) as error:
            TraceEncoder().encode(sequence(state))
        assert error.value.reason == "STATE_BYTES_LIMIT"
    else:
        assert TraceEncoder().encode(sequence(state)).events[0].stateVersion == 1


def test_serializer_passes_snapshot_but_encoder_rejects_size_after_adding_version():
    limits = default_limits()
    state = sized_snapshot(limits["state_bytes"] + 1)
    globals_ = {b["name"]: b["value"]["value"] for b in state["globals"]}
    module = state["frames"][0]
    frame = FrameInput(
        module["id"],
        None,
        "module",
        module["name"],
        module["qualifiedName"],
        1,
        None,
        (),
        "globals",
        {},
    )
    registry = ObjectRegistry(limits)
    try:
        observed = GraphSerializer(registry, limits).serialize(globals_, [frame], ["f1"])
        assert observed == state
        assert canonical_size(observed) <= limits["state_bytes"]
        with pytest.raises(EncoderLimitError) as error:
            TraceEncoder(limits).encode(sequence(observed))
        assert error.value.reason == "STATE_BYTES_LIMIT"
    finally:
        registry.close()


def test_version_digit_growth_is_included_in_state_size():
    states = [snapshot(globals_=[{"name": "x", "value": scalar(i % 2)}]) for i in range(10)]
    size = canonical_size({"version": 9, **states[-1]})
    limits = default_limits()
    limits["state_bytes"] = size
    encoder = TraceEncoder(limits)
    assert encoder.encode(sequence(*states[:-1])).events[-1].stateVersion == 9
    with pytest.raises(EncoderLimitError) as error:
        encoder.encode(sequence(*states))
    assert error.value.reason == "STATE_BYTES_LIMIT"


def test_size_check_including_checkpoint_zero():
    limits = default_limits()
    limits["state_bytes"] = 1
    with pytest.raises(EncoderLimitError) as error:
        TraceEncoder(limits).encode(ReferenceTrace(()))
    assert error.value.reason == "STATE_BYTES_LIMIT"


def test_nochange_does_not_repeat_full_normalized_size_check(monkeypatch):
    from codetrace_runner import encoder as implementation

    real = implementation.canonical_size
    accepted_versions = []

    def count(value):
        if type(value) is dict and set(value) == {
            "version",
            "globals",
            "frames",
            "stack",
            "objects",
            "eventRoots",
        }:
            accepted_versions.append(value["version"])
        return real(value)

    monkeypatch.setattr(implementation, "canonical_size", count)
    state = snapshot()
    TraceEncoder().encode(sequence(state, state, state))
    assert accepted_versions == [0, 1]


@pytest.mark.parametrize("interval", [0, -1, True, False, 1.5, "2", [], {}])
def test_invalid_checkpoint_interval(interval):
    with pytest.raises(ValueError, match="checkpoint_interval"):
        TraceEncoder(checkpoint_interval=interval)


@pytest.mark.parametrize(
    "key",
    ["checkpoint_interval", "max_events", "state_bytes", "trace_bytes", "terminal_reserve_bytes"],
)
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_invalid_effective_encoder_limits(key, value):
    limits = default_limits()
    limits[key] = value
    with pytest.raises(ValueError):
        TraceEncoder(limits)


def test_effective_limits_are_copied_and_checkpoint_interval_is_fixed():
    limits = default_limits()
    limits["checkpoint_interval"] = 2
    limits["max_events"] = 3
    encoder = TraceEncoder(limits)
    limits["checkpoint_interval"] = 128
    limits["max_events"] = 1
    states = [snapshot(globals_=[{"name": "x", "value": scalar(i)}]) for i in range(2)]
    result = encoder.encode(sequence(*states))
    assert result.checkpointInterval == 2 and result.states[2]["kind"] == "checkpoint"
    with pytest.raises(AttributeError):
        encoder.checkpointInterval = 1


@pytest.mark.parametrize("extra", [0, -1])
def test_record_byte_budget_exact_and_one_below(extra):
    reference = sequence(snapshot(), snapshot(globals_=[{"name": "x", "value": scalar(1)}]))
    baseline = TraceEncoder().encode(reference)
    own_bytes = sum(canonical_size(r) for r in baseline.states) + sum(
        canonical_size(e.as_dict()) for e in baseline.events
    )
    limits = default_limits()
    limits["terminal_reserve_bytes"] = 123
    limits["trace_bytes"] = own_bytes + 123 + extra
    encoder = TraceEncoder(limits)
    if extra:
        with pytest.raises(EncoderLimitError) as error:
            encoder.encode(reference)
        assert error.value.reason == "TRACE_BYTES_LIMIT"
        assert error.value.diagnostic["category"] == "limit"
    else:
        result = encoder.encode(reference)
        assert result.accounting.recordBytes == own_bytes
        assert result.accounting.ordinaryBudgetBytes == own_bytes


def test_accounting_is_only_record_content_no_envelope_console_or_terminal():
    result = TraceEncoder().encode(sequence(snapshot()))
    assert result.accounting.stateRecordBytes == sum(len(canonical_json(r)) for r in result.states)
    assert result.accounting.eventCoreBytes == sum(
        len(canonical_json(e.as_dict())) for e in result.events
    )
    assert result.accounting.ordinaryBudgetBytes == 52428800 - 2097152
    assert all("consoleOffset" not in e.as_dict() for e in result.events)


@pytest.mark.parametrize("reserve", [100, 101])
def test_reserve_must_leave_an_ordinary_budget(reserve):
    limits = default_limits()
    limits["trace_bytes"] = 100
    limits["terminal_reserve_bytes"] = reserve
    with pytest.raises(ValueError, match="ordinary byte budget"):
        TraceEncoder(limits)


def test_budget_failure_does_not_poison_reusable_encoder_or_publish_prefix():
    short = sequence(snapshot())
    baseline = TraceEncoder().encode(short)
    limits = default_limits()
    limits["terminal_reserve_bytes"] = 1
    limits["trace_bytes"] = baseline.accounting.recordBytes + 1
    encoder = TraceEncoder(limits)
    with pytest.raises(EncoderLimitError):
        encoder.encode(sequence(snapshot(), snapshot()))
    assert encoder.encode(short).accounting.recordBytes == baseline.accounting.recordBytes
