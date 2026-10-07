"""Console streams and same-process interactive input over the private channel."""

from collections.abc import Callable, Iterator
from typing import Literal, cast

from codetrace_contracts.types import ConsoleEntry, Diagnostic, ResourceLimits

from .canonical import canonical_size
from .encoder import TraceEncoderSession
from .io_protocol import ProtocolError, ProtocolReader, ProtocolWriter, TraceBatch


class ServiceAbort(BaseException):
    """Backend is already notified; its independent kill remains authoritative."""


class IOLimitError(RuntimeError):
    def __init__(self, reason: Literal["CONSOLE_LIMIT", "INPUT_REQUEST_LIMIT"]) -> None:
        super().__init__("Runner I/O limit exceeded.")
        self.reason = reason
        self.diagnostic: Diagnostic = {
            "category": "limit",
            "code": reason,
            "typeName": None,
            "message": "Runner I/O limit exceeded.",
            "source": None,
            "columnNumber": None,
            "traceback": [],
            "omittedTracebackFrames": 0,
        }


def text_bytes(text: str) -> int:
    return len(text.encode("utf-8", errors="surrogatepass"))


def chunks(text: str, max_bytes: int) -> Iterator[str]:
    start, size = 0, 0
    for index, char in enumerate(text):
        width = text_bytes(char)
        if width > max_bytes:
            raise ProtocolError("Output character exceeds configured chunk size")
        if size + width > max_bytes:
            yield text[start:index]
            start, size = index, 0
        size += width
    if start < len(text):
        yield text[start:]


class RunnerIO:
    def __init__(
        self,
        reader: ProtocolReader,
        writer: ProtocolWriter,
        batch: TraceBatch,
        session: TraceEncoderSession,
        limits: ResourceLimits,
        fail: Callable[[BaseException], None],
        active: Callable[[], bool],
    ) -> None:
        self.reader, self.writer, self.batch, self.session = reader, writer, batch, session
        self.limits = limits
        self._fail, self._active = fail, active
        self.count = 0
        self.consoleBytes = 0
        self.requests = 0
        self.stdout = ConsoleStream(self, "stdout")
        self.stderr = ConsoleStream(self, "stderr")

    def _guard(self) -> None:
        if not self._active():
            raise ServiceAbort()

    def emit(
        self, kind: Literal["stdout", "stderr", "input"], text: str, request_id: str | None = None
    ) -> None:
        self._guard()
        size = text_bytes(text)
        if self.consoleBytes + size > self.limits["console_bytes"]:
            raise IOLimitError("CONSOLE_LIMIT")
        entry: ConsoleEntry = {
            "index": self.count,
            "kind": kind,
            "text": text,
            "requestId": request_id,
        }
        if self.writer.message_size("output", {"entry": entry}) > self.writer.max_bytes:
            raise ProtocolError("Output message exceeds transport limit")
        self.session.accept_ordinary_bytes(canonical_size(entry) + 1)
        self.writer.send("output", {"entry": entry})
        self.consoleBytes += size
        self.count += 1

    def input(self, prompt: object = "", /) -> str:
        self._guard()
        # str is a user-requested action; errors/hooks here remain Python behavior.
        text = str(prompt)
        try:
            self.stdout.write(text)
            self.stdout.flush()
            if self.requests >= self.limits["input_requests"]:
                raise IOLimitError("INPUT_REQUEST_LIMIT")
            self.batch.flush()
            self.requests += 1
            request = f"q{self.requests}"
            self.writer.send(
                "input_request",
                {
                    "requestId": request,
                    "prompt": text,
                    "consoleOffset": self.count,
                },
            )
            payload = self.reader.receive("input_response")
            value = cast(str, payload["text"])
            if (
                payload["requestId"] != request
                or text_bytes(value) > self.limits["input_value_bytes"]
            ):
                raise ProtocolError("Invalid input response")
            self.emit("input", value + "\n", request)
            return value
        except ServiceAbort:
            raise
        except BaseException as error:
            self._fail(error)
            raise ServiceAbort() from None


class ConsoleStream:
    encoding = "utf-8"
    errors = "surrogatepass"

    def __init__(self, owner: RunnerIO, kind: Literal["stdout", "stderr"]) -> None:
        self._owner, self._kind = owner, kind

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("write() argument must be str")
        # Convert subclasses through the intrinsic str implementation, not user hooks.
        text = str.__str__(text)
        self._owner._guard()
        try:
            for chunk in chunks(text, self._owner.limits["output_chunk_bytes"]):
                self._owner.emit(self._kind, chunk)
        except BaseException as error:
            self._owner._fail(error)
            raise ServiceAbort() from None
        return len(text)

    def flush(self) -> None:
        self._owner._guard()
        try:
            self._owner.writer.flush()
        except BaseException as error:
            self._owner._fail(error)
            raise ServiceAbort() from None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True
