import os
import subprocess
import sys

import pytest

from .helpers import ROOT, subprocess_run


@pytest.mark.parametrize(
    "source,responses,reason",
    [
        ("x=1", [], "NONE"),
        ("", [], "NONE"),
        ("print('hello')", [], "NONE"),
        ("from collections import _sys\nprint('error',file=_sys.stderr)", [], "NONE"),
        ("x=", [], "SYNTAX_ERROR"),
        ("x=1\n1/0", [], "UNCAUGHT_EXCEPTION"),
        ("x=input('Имя: ')", ["Имя"], "NONE"),
        ("a=input('A:')\nb=input('B:')", ["", " spaces "], "NONE"),
        ("print(input())", ["😀"], "NONE"),
        ("values=[]\nvalues.append(input())", ["x"], "NONE"),
        ('print(\'{"fake":"protocol"}\')', [], "NONE"),
        ("import os", [], "UNSUPPORTED_FEATURE"),
        ("x=(i for i in [])", [], "UNSUPPORTED_FEATURE"),
        ("try:\n 1/0\nexcept ZeroDivisionError:\n print('ok')", [], "NONE"),
    ],
)
def test_real_process(source, responses, reason):
    messages, status = subprocess_run(source, responses)
    assert status == 0 and messages[0]["type"] == "ready"
    assert messages[-1]["type"] == "finished"
    assert messages[-1]["payload"]["reason"] == reason
    assert messages[-2]["type"] == "trace_chunk"
    if "_sys.stderr" in source:
        assert any(
            m["payload"]["entry"]["kind"] == "stderr" for m in messages if m["type"] == "output"
        )


@pytest.mark.parametrize("data", [b"bad\n", b"{}\n", b"\xff\n"])
def test_real_bad_control(data):
    messages, _ = subprocess_run("", initial=data)
    assert messages[-1]["type"] == "fatal"


def test_real_input_eof():
    messages, _ = subprocess_run("x=input()\nprint('MUST NOT RUN')", eof=True)
    assert messages[-1]["type"] == "fatal"
    assert not any(m["type"] == "output" for m in messages)


def test_real_abort_catch_cannot_delay_notification():
    from codetrace_contracts import default_limits

    from .helpers import start, wire

    limits = default_limits()
    limits["string_bytes"] = 2
    source = "try:\n x='abc'\n x=1\nexcept BaseException:\n while True:\n  pass"
    messages, _ = subprocess_run(source, initial=wire(start(source, limits)))
    assert messages[-1]["type"] == "abort_request"
    assert messages[-1]["payload"]["reason"] == "STRING_LIMIT"


def test_missing_identity_safe_native_stderr():
    env = os.environ.copy()
    env.pop("CODETRACE_RUN_ID", None)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "runner"), str(ROOT / "contracts/python")))
    result = subprocess.run(
        [sys.executable, "-m", "codetrace_runner.runner"], env=env, capture_output=True, timeout=5
    )
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == b"CodeTrace Runner startup failed.\n"


def test_compiler_warning_never_leaks_native_source():
    messages, status = subprocess_run("secret = (1 is 1)")
    assert status == 0 and messages[-1]["payload"]["outcome"] == "SUCCESS"
