"""Test-only CPython oracle. TRACING snapshots + ENCODER independent decoder; never TRACE."""

import argparse
import copy
import json
import sys
from pathlib import Path

from codetrace_contracts import default_limits, validate_contract
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.reference import trace_source

from runner.tests.encoder.helpers import (
    assert_differential,
    frame,
    refs,
    scalar,
    seek,
    sequence,
    snapshot,
)
from runner.tests.runner_io.helpers import console, execute, records

ROOT = Path(__file__).resolve().parents[3]
BASE = json.loads((ROOT / "contracts/fixtures/valid/trace-minimal-success.json").read_text())
PROGRAMS = {
    "empty": "",
    "pass": "pass",
    "assignment": "x=1\nx=2\ny=True\nz=None",
    "unchanged": "for i in [0,0,0,0]: pass",
    "loop": "for i in range(4):\n x=i",
    "list": "a=[1]\na.append(2)\na[0]=3",
    "nested": "inner=[1]\nouter=[inner]\ninner.append(2)",
    "function": "def f(x):\n y=x+1\n return y\nr=f(3)",
    "recursion": "def f(n):\n if n<2: return 1\n return n*f(n-1)\nr=f(4)",
    "handled": "try:\n 1/0\nexcept ZeroDivisionError:\n x=1",
    "propagated": "def f():\n raise ValueError('boom')\nf()",
    "class": "class A:\n x=1\n def m(self): return 2\na=A()\nr=a.m()",
    "return_root": "def f():\n return [1,2]\nf()\nx=1",
    "alias": "a=[]\nb=a\na.append(1)",
    "self_cycle": "a=[]\na.append(a)\na.append(1)",
    "mutual_cycle": "a=[]\nb=[a]\na.append(b)\nb.append(7)",
    "all_types": "from collections import deque\na=(1,True,None)\nb={1:'int','1':'str'}\n"
    "c={1,2}\nd=deque([1,2],maxlen=3)\nd.append(3)\nd.append(4)",
    "specials": "__proto__=10\nconstructor=20\nprototype=30\nЯ=40\n"
    "big=123456789012345678901234567890\nneg=-0.0\nnan=float('nan')\n"
    "positive=float('inf')\nnegative=float('-inf')",
}


def base_document(code, interval):
    doc = copy.deepcopy(BASE)
    doc.update(sourceCode=code, checkpointInterval=interval, limits=default_limits(), console=[])
    doc["limits"]["checkpoint_interval"] = interval
    return doc


def finish(doc, version):
    doc["events"].append(
        dict(
            index=len(doc["events"]),
            kind="RUN_END",
            stateVersion=version,
            frameId=None,
            source=None,
            consoleOffset=len(doc["console"]),
            payload={},
        )
    )
    return doc


def encoded_document(reference, code, interval):
    encoded = TraceEncoder(checkpoint_interval=interval).encode(reference)
    assert_differential(encoded, reference)
    doc = base_document(code, interval)
    doc["states"] = list(encoded.states)
    doc["events"] = [{**e.as_dict(), "consoleOffset": 0} for e in encoded.events]
    return finish(doc, len(doc["states"]) - 1)


def reference_document(code, interval):
    execution = trace_source(code)
    encoder = TraceEncoder(checkpoint_interval=interval)
    encoded = encoder.encode(execution.trace)
    assert_differential(encoded, execution.trace)
    session = encoder.session()
    consumed_records = [session.start()]
    for index, point in enumerate(execution.trace.points):
        record, event = session.consume(point)
        assert event == encoded.events[index]
        if record is not None:
            consumed_records.append(record)
    assert consumed_records == list(encoded.states)
    doc = base_document(code, interval)
    doc["states"] = list(encoded.states)
    doc["events"] = [{**e.as_dict(), "consoleOffset": 0} for e in encoded.events]
    final = copy.deepcopy(execution.trace.points[-1].snapshot)
    final.update(frames=[], stack=[], eventRoots=[])
    objects = {o["id"]: o for o in final["objects"]}
    reached, pending = set(), list(refs(final["globals"]))
    while pending:
        oid = pending.pop()
        if oid not in reached:
            reached.add(oid)
            pending.extend(refs(objects[oid]))
    final["objects"] = [o for o in final["objects"] if o["id"] in reached]
    record, version = session.accept_final_snapshot(final)
    if record is not None:
        doc["states"].append(record)
    if execution.exception_escaped:
        _, messages, _ = execute(code, limits=doc["limits"])
        terminal = messages[-1]["payload"]
        doc["completion"].update(
            outcome=terminal["outcome"],
            reason=terminal["reason"],
            primaryDiagnostic=terminal["primaryDiagnostic"],
        )
    return finish(doc, version)


def runner_document(code, interval, responses=()):
    doc = base_document(code, interval)
    _, messages, status = execute(code, limits=doc["limits"], responses=responses)
    assert status == 0 and messages[-1]["type"] == "finished"
    terminal = messages[-1]["payload"]
    doc.update(
        states=records(messages, "state"),
        events=records(messages, "event"),
        console=console(messages),
    )
    doc["completion"].update(
        outcome=terminal["outcome"],
        reason=terminal["reason"],
        primaryDiagnostic=terminal["primaryDiagnostic"],
        stateAccuracy="final" if doc["events"] else "unavailable",
    )
    return finish(doc, terminal["finalStateVersion"])


def expected_steps(doc):
    """Pure Python oracle, including default frame scope and display metadata."""
    validate_contract(doc, "trace")
    result = []
    for event in doc["events"]:
        state = seek(doc["states"], event["stateVersion"])
        by_id = {f["id"]: f for f in state["frames"]}
        selected = by_id[state["stack"][-1]] if state["stack"] else None
        end = event["kind"] == "RUN_END"
        diagnostic = doc["completion"]["primaryDiagnostic"] if end else None
        source = diagnostic["source"] if diagnostic else None if end else event["source"]
        line = source["lineNumber"] if source else None
        mode = (
            "none"
            if line is None
            else "error"
            if end
            else "before_line"
            if event["kind"] == "LINE"
            else "event"
        )
        result.append(
            dict(
                state=state,
                event=event,
                globals=state["globals"],
                selectedFrameId=selected["id"] if selected else None,
                selectedLocals=(
                    state["globals"]
                    if selected["localsSource"] == "globals"
                    else selected["locals"]
                )
                if selected
                else [],
                callStack=[by_id[fid] for fid in state["stack"]],
                consoleEntries=doc["console"][: event["consoleOffset"]],
                highlight=dict(lineNumber=line, mode=mode),
                eventDiagnostic=event["payload"]["diagnostic"]
                if event["kind"] == "EXCEPTION"
                else None,
                completion=doc["completion"] if end else None,
            )
        )
    return result


def changes_reference():
    def bindings(**values):
        return [{"name": name, "value": value} for name, value in sorted(values.items())]

    one = {"id": "o1", "kind": "list", "items": [scalar(1)]}
    two = {"id": "o2", "kind": "list", "items": [{"$ref": "o1"}]}
    b = bindings(a={"$ref": "o1"}, b={"$ref": "o1"}, outer={"$ref": "o2"}, x=scalar(2))
    states = [
        snapshot(globals_=bindings(x=scalar(1))),
        snapshot(globals_=bindings(x=scalar(2))),
        snapshot(globals_=bindings(x=scalar(2), y=scalar(3))),
        snapshot(globals_=bindings(x=scalar(2))),
        snapshot(globals_=b, objects=[one, two]),
        snapshot(globals_=b, objects=[{**one, "items": [scalar(2)]}, two]),
        snapshot(globals_=b, objects=[{**one, "items": [{"$ref": "o2"}]}, two]),
    ]
    states.append(copy.deepcopy(states[-1]))  # metadata-only event, no new version
    states += [snapshot(globals_=bindings(x=scalar(2))), snapshot(globals_=b, objects=[one, two])]
    return sequence(*states)


def frames_reference():
    globals_ = [{"name": "g", "value": scalar(1)}]
    module = frame()
    f2 = frame("f2", "f1", "function", [{"name": "x", "value": scalar(1)}])
    f3 = frame("f3", "f2", "function", [{"name": "x", "value": scalar(1)}])
    return sequence(
        snapshot(globals_=globals_),
        snapshot(globals_=globals_, frames=[module, f2], stack=["f1", "f2"]),
        snapshot(
            globals_=globals_,
            frames=[module, {**f2, "locals": [{"name": "x", "value": scalar(2)}]}],
            stack=["f1", "f2"],
        ),
        snapshot(globals_=globals_, frames=[module, f2, f3], stack=["f1", "f2", "f3"]),
        snapshot(globals_=globals_, frames=[module, f2], stack=["f1", "f2"]),
        snapshot(globals_=globals_),
    )


def generate(destination):
    assert tuple(sys.version_info[:3]) == (3, 13, 16)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []

    def save(name, interval, doc, oracle):
        expected = expected_steps(doc)
        filename = f"{name}-{interval}.json"
        payload = dict(name=name, oracle=oracle, trace=doc, expected=expected)
        (destination / filename).write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")
        manifest.append(
            dict(
                file=filename, scenario=name, interval=interval, steps=len(expected), oracle=oracle
            )
        )

    for interval in (1, 2, 128):
        for name, source in PROGRAMS.items():
            save(
                name,
                interval,
                reference_document(source, interval),
                "TRACING snapshots + ENCODER independent decoder",
            )
        for name, source, responses in [
            ("syntax", "if :", ()),
            ("console", "print('A',end='')\nprint('B')\nprint('C')", ()),
            ("input", "a=input('Q:')\nprint(a)\nb=input('Second:')\nprint(b)", ("  😀  ", "")),
        ]:
            doc = runner_document(source, interval, responses)
            if name == "console":
                doc["console"].append(
                    dict(index=len(doc["console"]), kind="stderr", text="E", requestId=None)
                )
                doc["events"][-1]["consoleOffset"] = len(doc["console"])
            save(
                name,
                interval,
                doc,
                "RUNNER_IO wire + ENCODER independent decoder"
                + ("; synthetic stderr" if name == "console" else ""),
            )
        for name, reference in [
            ("binding_changes", changes_reference()),
            ("frame_changes", frames_reference()),
            (
                "boundaries",
                sequence(
                    *[snapshot(globals_=[{"name": "x", "value": scalar(v)}]) for v in range(1, 258)]
                ),
            ),
        ]:
            save(
                name,
                interval,
                encoded_document(reference, "pass", interval),
                "Synthetic reference snapshots + ENCODER independent decoder",
            )
        partial = reference_document("x=1\nx=2", interval)
        partial["states"] = partial["states"][:-1]
        partial["events"] = partial["events"][:-1]
        partial["completion"].update(
            outcome="LIMIT_EXCEEDED",
            reason="ACTIVE_TIMEOUT",
            traceComplete=False,
            stateAccuracy="last_observed",
            exitCode=None,
        )
        partial["completion"]["primaryDiagnostic"] = dict(
            category="limit",
            code="ACTIVE_TIMEOUT",
            typeName=None,
            message="Active execution timeout",
            source=None,
            columnNumber=None,
            traceback=[],
            omittedTracebackFrames=0,
        )
        save(
            "partial",
            interval,
            finish(partial, len(partial["states"]) - 1),
            "Synthetic accepted prefix + ENCODER independent decoder",
        )
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                fixtures=len(manifest),
                steps=sum(m["steps"] for m in manifest),
                destination=str(destination),
            )
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "fixtures")
    generate(parser.parse_args().output)
