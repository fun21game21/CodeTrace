"""RUNNER_IO production process. Run with CPython 3.13.16 and CODETRACE_RUN_ID."""

import ast
import builtins
import os
import sys
import sysconfig
import warnings
from typing import BinaryIO, cast
from uuid import UUID

from codetrace_contracts import default_limits
from codetrace_contracts.config import PROTOCOL_VERSION, PYTHON_VERSION, SOURCE_FILE_NAME
from codetrace_contracts.types import Diagnostic, ResourceLimits

from .encoder import TraceEncoder, TraceEncoderSession
from .encoder_errors import EncoderLimitError
from .errors import ValueLimitError
from .graph_errors import GraphLimitError
from .io_protocol import ProtocolError, ProtocolReader, ProtocolWriter, TraceBatch
from .registry import ObjectRegistry
from .runner_diagnostics import UnsupportedFeature, neutral, syntax, uncaught
from .runner_io import IOLimitError, RunnerIO, text_bytes
from .serializer import GraphSerializer
from .trace_engine import ReferenceLimitError, TraceEngine, TraceEngineError
from .trace_types import ReferenceTracePoint


def runtime_supported() -> bool:
    return (
        sys.implementation.name == "cpython"
        and tuple(sys.version_info[:3]) == (3, 13, 16)
        and sys.version_info.releaselevel == "final"
        and not sysconfig.get_config_var("Py_GIL_DISABLED")
        and not sysconfig.get_config_var("_PY_JIT")
        and getattr(sys, "_is_gil_enabled", lambda: False)()
    )


def preflight(source: str) -> None:
    tree = ast.parse(source, filename=SOURCE_FILE_NAME, mode="exec")
    forbidden = (
        ast.Yield,
        ast.YieldFrom,
        ast.GeneratorExp,
        ast.AsyncFunctionDef,
        ast.Await,
        ast.AsyncFor,
        ast.AsyncWith,
    )
    for node in ast.walk(tree):
        if isinstance(node, forbidden) or (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "compile"}
        ):
            raise UnsupportedFeature(getattr(node, "lineno", None))


class Runner:
    """A single start. No recorder, watchdog, Docker or complete trace storage."""

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, run_id: str) -> None:
        validate_run_id(run_id)
        limits = default_limits()
        self.reader = ProtocolReader(stdin, run_id, limits["runner_message_bytes"])
        self.writer = ProtocolWriter(stdout, run_id, limits["runner_message_bytes"])
        self.batch = TraceBatch(self.writer)
        self._terminal = False
        self.session: TraceEncoderSession | None = None
        self.io: RunnerIO | None = None
        self._unsupported: Diagnostic | None = None

    def fail(self, error: BaseException) -> None:
        if self._terminal:
            return
        self._terminal = True
        try:
            self.batch.flush()
        except BaseException:
            # A failed transport cannot promise delivery of a pending prefix.
            pass
        try:
            if type(error) in (
                ValueLimitError,
                GraphLimitError,
                EncoderLimitError,
                IOLimitError,
                ReferenceLimitError,
            ):
                limited = cast(
                    ValueLimitError
                    | GraphLimitError
                    | EncoderLimitError
                    | IOLimitError
                    | ReferenceLimitError,
                    error,
                )
                self.writer.send(
                    "abort_request",
                    {
                        "reason": limited.reason,
                        "diagnostic": limited.diagnostic,
                    },
                )
            else:
                diagnostic = neutral(
                    "RUNNER_PROTOCOL_ERROR" if type(error) is ProtocolError else "RUNNER_FAILURE"
                )
                self.writer.send("fatal", {"reason": "RUNNER_FAILURE", "diagnostic": diagnostic})
        except BaseException:
            pass

    def _point(self, point: ReferenceTracePoint) -> None:
        if self._terminal or self.session is None or self.io is None:
            raise TraceEngineError("Runner stream is unavailable")
        offset = self.io.count
        record, core = self.session.consume(point, console_offset=offset)
        if record is not None:
            self.batch.add("state", record)
        self.batch.add("event", {**core.as_dict(), "consoleOffset": offset})

    def _import(
        self,
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if level != 0 or name not in ("math", "collections"):
            error = UnsupportedFeature()
            self._unsupported = error.diagnostic
            raise error
        return builtins.__import__(name, globals, locals, fromlist, level)

    def _finish(self, diagnostic: Diagnostic | None, version: int) -> None:
        self.batch.flush()
        self.writer.send(
            "finished",
            {
                "outcome": "SUCCESS" if diagnostic is None else "USER_ERROR",
                "reason": "NONE" if diagnostic is None else diagnostic["code"],
                "primaryDiagnostic": diagnostic,
                "finalStateVersion": version,
            },
        )
        self._terminal = True

    def run(self) -> int:
        if not runtime_supported():
            self.fail(RuntimeError("Unsupported Runner runtime"))
            return 2
        try:
            self.writer.send(
                "ready", {"pythonVersion": PYTHON_VERSION, "protocolVersion": PROTOCOL_VERSION}
            )
            payload = self.reader.receive("start")
            limits = cast(ResourceLimits, payload["limits"])
            source = cast(str, payload["code"])
            interval = cast(int, payload["checkpointInterval"])
            if (
                interval != limits["checkpoint_interval"]
                or text_bytes(source) > limits["code_bytes"]
            ):
                raise ProtocolError("Invalid effective start configuration")
            if self.reader.lastLineBytes > limits["runner_message_bytes"]:
                raise ProtocolError("Start exceeds effective message size")
            self.reader.max_bytes = limits["runner_message_bytes"]
            self.writer.max_bytes = limits["runner_message_bytes"]
            self.session = TraceEncoder(limits, checkpoint_interval=interval).session()
            self.batch.add("state", self.session.start())
            self.session.accept_ordinary_bytes(1)
            # Initial checkpoint is committed even on pre-execution user errors.
            try:
                # Compiler warnings must not print source material on native stderr.
                with warnings.catch_warnings(action="ignore"):
                    preflight(source)
                    code = compile(source, SOURCE_FILE_NAME, "exec", dont_inherit=True, optimize=0)
            except SyntaxError as error:
                self._finish(syntax(error, source), 0)
                return 0
            except UnsupportedFeature as error:
                self._finish(error.diagnostic, 0)
                return 0
            self.io = RunnerIO(
                self.reader,
                self.writer,
                self.batch,
                self.session,
                limits,
                self.fail,
                lambda: not self._terminal,
            )
            user_builtins = vars(builtins).copy()
            for name in ("open", "breakpoint", "exec", "eval", "compile"):
                user_builtins.pop(name)
            user_builtins["input"] = self.io.input
            user_builtins["__import__"] = self._import
            namespace: dict[str, object] = {
                "__name__": "__main__",
                "__file__": SOURCE_FILE_NAME,
                "__builtins__": user_builtins,
            }
            registry = ObjectRegistry(limits)
            serializer = GraphSerializer(registry, limits)
            normalized = source.replace("\r\n", "\n").replace("\r", "\n")
            line_count = len(normalized.split("\n")) if source else 0
            engine = TraceEngine(
                code,
                line_count,
                namespace,
                serializer,
                point_sink=self._point,
                collect_points=False,
                failure_observer=self.fail,
            )
            callback = engine.callback
            previous_trace, stdout, stderr = sys.gettrace(), sys.stdout, sys.stderr
            diagnostic: Diagnostic | None = None
            try:
                sys.stdout = cast(object, self.io.stdout)
                sys.stderr = cast(object, self.io.stderr)
                sys.settrace(callback)
                try:
                    exec(code, namespace)
                except BaseException as error:
                    if not self._terminal:
                        diagnostic = (
                            error.diagnostic
                            if type(error) is UnsupportedFeature
                            else uncaught(
                                error, BaseException.__dict__["__traceback__"].__get__(error)
                            )
                        )
                if self._terminal:
                    return 1
                self.reader.ensure_no_pending_control()
                engine.result()  # Sticky failure and empty live stack, no collected points.
                if (
                    sys.gettrace() is not callback
                    or sys.stdout is not self.io.stdout
                    or sys.stderr is not self.io.stderr
                ):
                    raise TraceEngineError("User tracing or streams were replaced")
                sys.settrace(previous_trace)
                record, version = self.session.accept_final_snapshot(
                    serializer.serialize(namespace)
                )
                if record is not None:
                    self.batch.add("state", record)
                self._finish(self._unsupported or diagnostic, version)
                return 0
            finally:
                sys.settrace(previous_trace)
                sys.stdout, sys.stderr = stdout, stderr
                engine.close()
                registry.close()
                namespace.clear()
        except BaseException as error:
            self.fail(error)
            return 1


def validate_run_id(run_id: str) -> None:
    try:
        parsed = UUID(run_id)
    except (ValueError, AttributeError):
        raise ProtocolError("Invalid Runner identity") from None
    if str(parsed) != run_id.lower() or len(run_id) != 36:
        raise ProtocolError("Invalid Runner identity")


def main() -> int:
    run_id = os.environ.get("CODETRACE_RUN_ID", "")
    try:
        runner = Runner(sys.stdin.buffer, sys.stdout.buffer, run_id)
    except BaseException:
        # No valid identity means no valid wire envelope can be constructed.
        sys.stderr.write("CodeTrace Runner startup failed.\n")
        return 2
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
