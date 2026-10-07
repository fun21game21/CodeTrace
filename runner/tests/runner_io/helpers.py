"""Test backend and collecting transports, never part of production Runner."""

import io
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from codetrace_contracts import default_limits, validate_contract
from codetrace_runner.runner import Runner

RUN_ID = "12345678-1234-4234-8234-123456789abc"
ROOT = Path(__file__).resolve().parents[3]


def envelope(kind, payload, seq=1, **overrides):
    return {
        "protocolVersion": 1,
        "runId": RUN_ID,
        "seq": seq,
        "type": kind,
        "payload": payload,
        **overrides,
    }


def start(code, limits=None):
    limits = default_limits() if limits is None else limits
    return envelope(
        "start",
        {
            "code": code,
            "limits": limits,
            "sourceFileName": "<codetrace-user>",
            "checkpointInterval": limits["checkpoint_interval"],
        },
    )


def wire(message):
    return json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"


def validate_messages(messages):
    assert [m["seq"] for m in messages] == list(range(1, len(messages) + 1))
    for message in messages:
        validate_contract(message, "runner")
        assert message["runId"] == RUN_ID
        if message["type"] == "trace_chunk":
            for item in message["payload"]["records"]:
                validate_contract(
                    item["record"],
                    "trace",
                    "StateRecord" if item["kind"] == "state" else "PythonTraceEvent",
                )


def execute(code, *, responses=(), limits=None, raw=None):
    source = wire(start(code, limits))
    for i, text in enumerate(responses, 2):
        source += wire(envelope("input_response", {"requestId": f"q{i - 1}", "text": text}, i))
    if raw is not None:
        source = raw
    output = io.BytesIO()
    runner = Runner(io.BytesIO(source), output, RUN_ID)
    status = runner.run()
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    validate_messages(messages)
    return runner, messages, status


def records(messages, kind):
    return [
        r["record"]
        for m in messages
        if m["type"] == "trace_chunk"
        for r in m["payload"]["records"]
        if r["kind"] == kind
    ]


def console(messages):
    return [m["payload"]["entry"] for m in messages if m["type"] == "output"]


def subprocess_run(code, responses=(), *, initial=None, eof=False, python=None):
    environment = os.environ.copy()
    environment["CODETRACE_RUN_ID"] = RUN_ID
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "runner"), str(ROOT / "contracts/python"))
    )
    process = subprocess.Popen(
        [python or sys.executable, "-m", "codetrace_runner.runner"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        cwd=ROOT,
    )
    messages = []
    pending = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            assert selector.select(10), "Runner failed to produce expected protocol message"
            data = os.read(process.stdout.fileno(), 4096)
            if not data:
                assert not pending
                break
            pending.extend(data)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                message = json.loads(line)
                messages.append(message)
                if message["type"] == "ready":
                    process.stdin.write(initial if initial is not None else wire(start(code)))
                    process.stdin.flush()
                if message["type"] == "input_request":
                    if eof:
                        process.stdin.close()
                    else:
                        n = int(message["payload"]["requestId"][1:])
                        response = envelope(
                            "input_response",
                            {"requestId": f"q{n}", "text": responses[n - 1]},
                            n + 1,
                        )
                        process.stdin.write(wire(response))
                        process.stdin.flush()
                if message["type"] in ("abort_request", "fatal"):
                    process.terminate()
        process.wait(timeout=5)
        native = process.stderr.read()
        assert native == b""
        validate_messages(messages)
        return messages, process.returncode
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
