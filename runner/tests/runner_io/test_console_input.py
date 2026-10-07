import io

import pytest
from codetrace_contracts import default_limits
from codetrace_runner.encoder import TraceEncoder
from codetrace_runner.io_protocol import ProtocolReader, ProtocolWriter, TraceBatch
from codetrace_runner.runner_io import RunnerIO, chunks, text_bytes

from .helpers import RUN_ID, console, envelope, execute, records, start, wire


@pytest.mark.parametrize(
    "text",
    [
        "",
        "abc",
        "a\nb\n",
        "no newline",
        "😀",
        "Я漢😀",
        "a" * 4096,
        "a" * 4097,
        "Я" * 2048 + "😀",
        "\ud800",
        '{"fake":"protocol"}',
        '<script>alert("x")</script>',
    ],
)
def test_write_text_count_and_preservation(text):
    _, messages, status = execute(f"count = print({text!r}, end='')")
    assert status == 0
    entries = console(messages)
    assert "".join(e["text"] for e in entries) == text
    assert all(text_bytes(e["text"]) <= 4096 for e in entries)
    assert [e["index"] for e in entries] == list(range(len(entries)))
    assert all(e["requestId"] is None for e in entries)


def make_io(limits=None):
    limits = limits or default_limits()
    writer = ProtocolWriter(io.BytesIO(), RUN_ID, limits["runner_message_bytes"])
    session = TraceEncoder(limits).session()
    session.start()
    io_ = RunnerIO(
        ProtocolReader(io.BytesIO(), RUN_ID, 2097152),
        writer,
        TraceBatch(writer),
        session,
        limits,
        lambda error: None,
        lambda: True,
    )
    return io_


@pytest.mark.parametrize("text", ["", "abc", "😀", "漢\n😀", "a" * 4097])
def test_direct_write_returns_characters(text):
    io_ = make_io()
    assert io_.stdout.write(text) == len(text)
    before = io_.count
    io_.stdout.flush()
    assert io_.count == before
    assert not io_.stdout.isatty() and io_.stdout.writable()


@pytest.mark.parametrize("value", [None, 1, b"bytes"])
def test_write_wrong_type(value):
    with pytest.raises(TypeError):
        make_io().stdout.write(value)


def test_shared_stdout_stderr_order():
    io_ = make_io()
    io_.stdout.write("A")
    io_.stderr.write("B")
    io_.stdout.write("C\n")
    assert io_.count == 3 and io_.consoleBytes == 4
    assert not hasattr(io_, "entries")


@pytest.mark.parametrize("size,expected", [(65536, "finished"), (65537, "abort_request")])
def test_console_limit(size, expected):
    _, messages, _ = execute(f"print('a'*{size}, end='')")
    assert messages[-1]["type"] == expected
    if expected == "abort_request":
        assert messages[-1]["payload"]["reason"] == "CONSOLE_LIMIT"
    assert sum(text_bytes(e["text"]) for e in console(messages)) <= 65536


@pytest.mark.parametrize(
    "prompt,response",
    [("", ""), ("A:", "  spaces  "), ("Имя: ", "😀Я"), ("\ud800", "\ud800"), ("?", "x" * 8192)],
)
def test_input_text(prompt, response):
    _, messages, status = execute(f"value = input({prompt!r})", responses=[response])
    assert status == 0
    request = next(m for m in messages if m["type"] == "input_request")
    entries = console(messages)
    assert "".join(e["text"] for e in entries if e["kind"] == "stdout") == prompt
    assert entries[-1] == {
        "index": len(entries) - 1,
        "kind": "input",
        "text": response + "\n",
        "requestId": "q1",
    }
    assert request["payload"] == {
        "requestId": "q1",
        "prompt": prompt,
        "consoleOffset": len(entries) - 1,
    }
    request_index = messages.index(request)
    assert messages[request_index - 1]["type"] == "trace_chunk"
    assert all(e["consoleOffset"] <= len(entries) for e in records(messages, "event"))


@pytest.mark.parametrize(
    "source",
    [
        "print(input())",
        "values=[]\nvalues.append(input())",
        "a=input('A:')\nb=input('B:')\nprint(a,b)",
    ],
)
def test_input_not_bound_to_assignment(source):
    responses = ["x", " y "] if "b=input" in source else ["x"]
    _, messages, status = execute(source, responses=responses)
    assert status == 0
    requests = [m["payload"]["requestId"] for m in messages if m["type"] == "input_request"]
    assert requests == [f"q{i + 1}" for i in range(len(responses))]


@pytest.mark.parametrize("text", ["x" * 8193, "a\nb", "a\rb"])
def test_bad_input_text(text):
    _, messages, status = execute("input()", responses=[text])
    assert status == 1 and messages[-1]["type"] == "fatal"
    assert not any(e["kind"] == "input" for e in console(messages))


@pytest.mark.parametrize("request_id", ["q2", "q999"])
def test_wrong_request(request_id):
    data = wire(start("input()")) + wire(
        envelope("input_response", {"requestId": request_id, "text": ""}, 2)
    )
    _, messages, status = execute("", raw=data)
    assert status == 1 and messages[-1]["type"] == "fatal"


def test_stale_q1_at_q2():
    data = wire(start("input()\ninput()"))
    for seq in (2, 3):
        data += wire(envelope("input_response", {"requestId": "q1", "text": ""}, seq))
    _, messages, _ = execute("", raw=data)
    assert messages[-1]["type"] == "fatal"
    assert len([e for e in console(messages) if e["kind"] == "input"]) == 1


@pytest.mark.parametrize("responses", [[], ["first"]])
def test_input_eof(responses):
    _, messages, status = execute("input()\ninput()", responses=responses)
    assert status == 1 and messages[-1]["type"] == "fatal"
    assert len([e for e in console(messages) if e["kind"] == "input"]) == len(responses)


def test_request_limit():
    _, messages, _ = execute("for _ in range(33):\n input()", responses=[""] * 32)
    assert messages[-1]["type"] == "abort_request"
    assert messages[-1]["payload"]["reason"] == "INPUT_REQUEST_LIMIT"
    assert len([m for m in messages if m["type"] == "input_request"]) == 32


@pytest.mark.parametrize(
    "source",
    [
        "print('A')\nx=1",
        "x=1\nprint('A')\nprint('B')\nx=2",
        "print('A',end='')\nprint('B',end='')",
        "print('A');x=1\nx=2",
    ],
)
def test_real_offsets(source):
    _, messages, _ = execute(source)
    events = records(messages, "event")
    assert [e["consoleOffset"] for e in events] == sorted(e["consoleOffset"] for e in events)
    assert events[-1]["consoleOffset"] == len(console(messages)) > 0
    # Temporal publication order: every referenced ConsoleEntry already committed.
    count = 0
    for message in messages:
        if message["type"] == "output":
            count += 1
        if message["type"] == "trace_chunk":
            assert all(
                r["record"]["consoleOffset"] <= count
                for r in message["payload"]["records"]
                if r["kind"] == "event"
            )


@pytest.mark.parametrize("action", ["print(A())", "input(A())"])
def test_explicit_user_str_traced(action):
    source = "class A:\n def __str__(self):\n  return 'label'\n" + action
    _, messages, status = execute(source, responses=["answer"] if "input" in action else [])
    assert status == 0
    assert any(
        f["name"] == "__str__"
        for r in records(messages, "state")
        for f in (
            r["state"]["frames"] if r["kind"] == "checkpoint" else r["changes"]["framesUpsert"]
        )
    )


@pytest.mark.parametrize("cap", [1, 2, 3, 4, 7, 4096])
def test_chunk_boundary_algorithm(cap):
    text = "abЯ漢" if cap >= 3 else "ab" if cap == 1 else "abЯ"
    result = list(chunks(text, cap))
    assert "".join(result) == text
    assert all(0 < text_bytes(chunk) <= cap for chunk in result)
