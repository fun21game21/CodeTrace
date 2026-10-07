import io
import json

import pytest
from codetrace_contracts import default_limits, validate_contract
from codetrace_runner.io_protocol import ProtocolError, ProtocolReader, ProtocolWriter, TraceBatch
from codetrace_runner.runner import Runner, runtime_supported, validate_run_id

from .helpers import RUN_ID, envelope, execute, start, wire


class Fragmented(io.BytesIO):
    def __init__(self, data, width):
        super().__init__(data)
        self.width = width

    def read1(self, size):
        return self.read(min(size, self.width))


@pytest.mark.parametrize("width", [1, 2, 3, 7, 13, 64, 4096])
def test_fragmented_two_messages(width):
    reader = ProtocolReader(
        Fragmented(
            wire(start("x=1"))
            + wire(envelope("input_response", {"requestId": "q1", "text": "😀"}, 2)),
            width,
        ),
        RUN_ID,
        2097152,
    )
    assert reader.receive("start")["code"] == "x=1"
    assert reader.receive("input_response")["text"] == "😀"


@pytest.mark.parametrize(
    "raw",
    [b"", b"{}", b"{}\n", b"{\n", b"\xff\n", b"null\n", b"[]\n", b"NaN\n", b'{"x":1,"x":2}\n'],
)
def test_invalid_lines(raw):
    with pytest.raises(ProtocolError):
        ProtocolReader(io.BytesIO(raw), RUN_ID, 100).receive("start")


@pytest.mark.parametrize(
    "overrides",
    [
        {"seq": 0},
        {"seq": 2},
        {"seq": True},
        {"protocolVersion": 2},
        {"runId": "22345678-1234-4234-8234-123456789abc"},
        {"extra": 1},
        {"type": "ready"},
    ],
)
def test_wrong_envelope(overrides):
    message = start("pass")
    message.update(overrides)
    with pytest.raises(ProtocolError):
        ProtocolReader(io.BytesIO(wire(message)), RUN_ID, 2097152).receive("start")


@pytest.mark.parametrize("length,terminated", [(101, True), (101, False), (10000, True)])
def test_oversize(length, terminated):
    data = b"a" * length + (b"\n" if terminated else b"")
    with pytest.raises(ProtocolError):
        ProtocolReader(Fragmented(data, 3), RUN_ID, 100).receive("start")


def test_exact_reader_limit():
    data = wire(start(""))
    assert ProtocolReader(io.BytesIO(data), RUN_ID, len(data) - 1).receive("start")["code"] == ""


@pytest.mark.parametrize("bad", ["", "not-uuid", "12345678123442348234123456789abc", "None"])
def test_run_id(bad):
    with pytest.raises(ProtocolError):
        validate_run_id(bad)


def test_ready_seq_runtime():
    assert runtime_supported()
    _, messages, status = execute("x=1")
    assert status == 0
    assert messages[0]["type"] == "ready"
    assert messages[0]["payload"] == {"protocolVersion": 1, "pythonVersion": "3.13.16"}


@pytest.mark.parametrize("change", ["code", "filename", "interval", "limits"])
def test_invalid_start(change):
    message = start("x=1")
    if change == "code":
        message["payload"]["limits"]["code_bytes"] = 1
    if change == "filename":
        message["payload"]["sourceFileName"] = "other"
    if change == "interval":
        message["payload"]["checkpointInterval"] = 2
    if change == "limits":
        message["payload"]["limits"]["max_events"] = 0
    _, messages, status = execute("", raw=wire(message))
    assert status == 1 and messages[-1]["type"] == "fatal"


@pytest.mark.parametrize(
    "second",
    [
        start("pass"),
        envelope("input_response", {"requestId": "q1", "text": "x"}, 1),
        envelope("input_response", {"requestId": "q2", "text": "x"}, 2),
    ],
)
def test_second_start_or_response_rejected(second):
    _, messages, status = execute("input()", raw=wire(start("input()")) + wire(second))
    assert status == 1 and messages[-1]["type"] == "fatal"


def test_runtime_gate(monkeypatch):
    import codetrace_runner.runner as module

    monkeypatch.setattr(module.sys, "version_info", (3, 14, 7))
    out = io.BytesIO()
    runner = Runner(io.BytesIO(), out, RUN_ID)
    assert runner.run() == 2
    message = json.loads(out.getvalue())
    validate_contract(message, "runner")
    assert message["type"] == "fatal"
    assert b"ready" not in out.getvalue()


def test_writer_bound_and_flush():
    out = io.BytesIO()
    writer = ProtocolWriter(out, RUN_ID, 100)
    with pytest.raises(ProtocolError):
        writer.send("output", {"entry": "x" * 100})
    assert not out.getvalue() and writer._seq == 1


def test_large_single_and_bounded_batch():
    out = io.BytesIO()
    writer = ProtocolWriter(out, RUN_ID, 10000)
    batch = TraceBatch(writer, 200)
    for _ in range(20):
        batch.add("state", {"text": "x" * 500})
    batch.flush()
    assert batch.peakBytes < 1000
    assert len(out.getvalue().splitlines()) == 20
    assert batch._records == []
    with pytest.raises(ProtocolError):
        batch.add("state", {"text": "x" * 10000})


def test_hard_batch_limit_includes_envelope():
    out = io.BytesIO()
    writer = ProtocolWriter(out, RUN_ID, 500)
    batch = TraceBatch(writer, 65536)
    for _ in range(20):
        batch.add("state", {"text": "x" * 130})
    batch.flush()
    assert all(len(line) <= 500 for line in out.getvalue().splitlines())


def test_eof_before_start():
    _, messages, status = execute("", raw=b"")
    assert status == 1
    assert [m["type"] for m in messages] == ["ready", "fatal"]


def test_lone_surrogate_code_size():
    limits = default_limits()
    limits["code_bytes"] = 1
    _, messages, _ = execute("\ud800", limits=limits)
    assert messages[-1]["type"] == "fatal"


@pytest.mark.parametrize("mode", ["nogil-build", "gil-disabled", "jit", "implementation"])
def test_runtime_build_gate(mode, monkeypatch):
    from types import SimpleNamespace

    import codetrace_runner.runner as module

    if mode in ("nogil-build", "jit"):
        original = module.sysconfig.get_config_var
        name = "Py_GIL_DISABLED" if mode == "nogil-build" else "_PY_JIT"
        monkeypatch.setattr(
            module.sysconfig, "get_config_var", lambda key: 1 if key == name else original(key)
        )
    if mode == "gil-disabled":
        monkeypatch.setattr(module.sys, "_is_gil_enabled", lambda: False)
    if mode == "implementation":
        monkeypatch.setattr(module.sys, "implementation", SimpleNamespace(name="pypy"))
    assert not runtime_supported()


def test_start_size_uses_effective_limit():
    message = start("pass")
    message["payload"]["limits"]["runner_message_bytes"] = 500
    _, messages, status = execute("", raw=wire(message))
    assert status == 1 and messages[-1]["type"] == "fatal"


def test_second_start_buffered_without_input_rejected():
    _, messages, status = execute("", raw=wire(start("pass")) + wire(start("pass")))
    assert status == 1 and messages[-1]["type"] == "fatal"


def test_unsolicited_response_buffered_without_input_rejected():
    _, messages, status = execute(
        "",
        raw=wire(start("pass"))
        + wire(envelope("input_response", {"requestId": "q1", "text": ""}, 2)),
    )
    assert status == 1 and messages[-1]["type"] == "fatal"
