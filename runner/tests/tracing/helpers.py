import dataclasses

from codetrace_contracts import validate_contract
from codetrace_runner.trace_types import ReferenceTracePoint


def bindings(items):
    return {item["name"]: item["value"] for item in items}


def frame(point, frame_id=None):
    return next(f for f in point.snapshot["frames"] if f["id"] == (frame_id or point.frameId))


def named(points, name, kind=None):
    return [p for p in points if frame(p)["name"] == name and (kind is None or p.kind == kind)]


def record(point, value):
    return next(o for o in point.snapshot["objects"] if o["id"] == value["$ref"])


def json_tree(value):
    # Exact-type allowlist excludes frames, exceptions, traceback, user objects,
    # dict/list subclasses and non-JSON leaves, at arbitrary nesting depth.
    if type(value) is dict:
        assert all(type(k) is str for k in value)
        for child in value.values():
            json_tree(child)
    elif type(value) in (list, tuple):
        for child in value:
            json_tree(child)
    else:
        assert type(value) in (str, int, bool, type(None))


def refs(value):
    if type(value) is dict:
        if "$ref" in value:
            return {value["$ref"]}
        return set().union(*(refs(v) for v in value.values()))
    if type(value) is list:
        return set().union(*(refs(v) for v in value))
    return set()


def assert_trace_invariants(trace):
    active = []
    ids = []
    for point in trace.points:
        assert type(point) is ReferenceTracePoint
        json_tree(point.snapshot)
        json_tree(point.payload)
        json_tree(point.source)
        dto = dataclasses.asdict(point)
        json_tree(dto)
        assert set(dto) == {"kind", "frameId", "source", "payload", "snapshot"}
        snapshot = point.snapshot
        assert set(snapshot) == {"globals", "frames", "stack", "objects", "eventRoots"}
        # Only test adapters assign versions/index/console offsets for CONTRACTS validation.
        validate_contract({"version": 1, **snapshot}, "trace", "NormalizedState")
        validate_contract(
            {
                "index": 0,
                "stateVersion": 1,
                "consoleOffset": 0,
                **{k: v for k, v in dto.items() if k != "snapshot"},
            },
            "trace",
            "PythonTraceEvent",
        )
        if point.kind == "CALL":
            ids.append(point.frameId)
            active.append(point.frameId)
        assert snapshot["stack"] == active
        assert point.frameId == active[-1]
        by_id = {f["id"]: f for f in snapshot["frames"]}
        assert set(by_id) == set(active)
        for i, fid in enumerate(active):
            assert by_id[fid]["parentId"] == (active[i - 1] if i else None)
        objects = {o["id"]: o for o in snapshot["objects"]}
        assert refs(snapshot) <= objects.keys()
        reached = set()
        pending = list(refs([snapshot["globals"], snapshot["frames"], snapshot["eventRoots"]]))
        while pending:
            oid = pending.pop()
            if oid not in reached:
                reached.add(oid)
                pending.extend(refs(objects[oid]))
        assert reached == objects.keys()
        if point.kind == "RETURN":
            active.pop()
            if point.payload["returnValueKnown"]:
                assert snapshot["eventRoots"] == [point.payload["returnValue"]]
            else:
                assert point.payload["returnValue"] is None
                assert snapshot["eventRoots"] == []
        else:
            assert snapshot["eventRoots"] == []
    assert active == []
    assert ids == [f"f{i}" for i in range(1, len(ids) + 1)]
