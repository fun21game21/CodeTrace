"""Independent test-only forward apply/seek. No imports of Encoder diff code."""

import json

from codetrace_contracts import validate_contract
from codetrace_runner.trace_types import ReferenceTrace, ReferenceTracePoint


def copy_json(value):
    return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))


def scalar(value):
    if value is None:
        return {"kind": "scalar", "type": "none", "value": None}
    if type(value) is bool:
        return {"kind": "scalar", "type": "bool", "value": value}
    if type(value) is int:
        return {"kind": "scalar", "type": "int", "value": str(value)}
    if type(value) is float:
        return {"kind": "scalar", "type": "float", "value": repr(value)}
    return {"kind": "scalar", "type": "str", "value": value}


def frame(fid="f1", parent=None, kind="module", locals_=None):
    return {
        "id": fid,
        "parentId": parent,
        "kind": kind,
        "name": "module" if kind == "module" else "f",
        "qualifiedName": "module" if kind == "module" else "f",
        "definitionLine": 1,
        "callSite": None,
        "parameterNames": [],
        "localsSource": "globals" if kind == "module" else "own",
        "locals": locals_ or [],
    }


def snapshot(*, globals_=None, frames=None, stack=None, objects=None, roots=None):
    return {
        "globals": globals_ or [],
        "frames": [frame()] if frames is None else frames,
        "stack": ["f1"] if stack is None else stack,
        "objects": objects or [],
        "eventRoots": roots or [],
    }


def point(state, *, kind="LINE", line=1, payload=None):
    return ReferenceTracePoint(
        kind,
        state["stack"][-1],
        {"fileName": "<codetrace-user>", "lineNumber": line},
        payload or {},
        state,
    )


def sequence(*snapshots):
    return ReferenceTrace(tuple(point(state) for state in snapshots))


def refs(value):
    if isinstance(value, list):
        return set().union(*(refs(child) for child in value))
    if isinstance(value, dict):
        if "$ref" in value:
            return {value["$ref"]}
        return set().union(*(refs(child) for child in value.values()))
    return set()


def assert_state(state):
    validate_contract(state, "trace", "NormalizedState")
    table = {record["id"]: record for record in state["objects"]}
    pending = list(refs([state["globals"], state["frames"], state["eventRoots"]]))
    reached = set()
    while pending:
        oid = pending.pop()
        assert oid in table
        if oid not in reached:
            reached.add(oid)
            pending.extend(refs(table[oid]))
    assert reached == table.keys()


def apply_record(state, record):
    """Direct schema semantics, without any knowledge of how changes were chosen."""
    validate_contract(record, "trace", "StateRecord")
    if record["kind"] == "checkpoint":
        result = copy_json(record["state"])
    else:
        assert record["baseVersion"] == state["version"]
        result = copy_json(state)
        changes = record["changes"]
        for field in ("globals", "stack", "eventRoots"):
            if changes[field] is not None:
                result[field] = copy_json(changes[field])
        for field in ("frames", "objects"):
            records = {entry["id"]: entry for entry in result[field]}
            for removed in changes[field + "Remove"]:
                assert removed in records
                del records[removed]
            for upsert in changes[field + "Upsert"]:
                records[upsert["id"]] = copy_json(upsert)
            result[field] = sorted(records.values(), key=lambda entry: int(entry["id"][1:]))
        result["version"] = record["version"]
    assert_state(result)  # Only AFTER the whole transaction: intermediate refs may not resolve.
    return result


def seek(records, version):
    checkpoint = max(v for v in range(version + 1) if records[v]["kind"] == "checkpoint")
    state = apply_record(None, records[checkpoint])
    for v in range(checkpoint + 1, version + 1):
        assert records[v]["version"] == v
        state = apply_record(state, records[v])
    return state


def assert_differential(encoded, reference):
    assert len(encoded.events) == len(reference.points)
    assert [record["version"] for record in encoded.states] == list(range(len(encoded.states)))
    for v, record in enumerate(encoded.states):
        assert record["kind"] == ("checkpoint" if v % encoded.checkpointInterval == 0 else "delta")
        validate_contract(record, "trace", "StateRecord")
    # Sequential decoding and seeking are independently compared to the reference.
    decoded = []
    state = None
    for record in encoded.states:
        state = apply_record(state, record)
        decoded.append(state)
    for i, (event, observed) in enumerate(zip(encoded.events, reference.points, strict=True)):
        assert event.index == i
        assert (event.kind, event.frameId, event.source, event.payload) == (
            observed.kind,
            observed.frameId,
            observed.source,
            observed.payload,
        )
        expected = {"version": event.stateVersion, **observed.snapshot}
        assert decoded[event.stateVersion] == expected
        assert seek(encoded.states, event.stateVersion) == expected
        # Only this test adapter provides a real legal offset for wire compatibility.
        validate_contract({**event.as_dict(), "consoleOffset": 7}, "trace", "PythonTraceEvent")
    return decoded
