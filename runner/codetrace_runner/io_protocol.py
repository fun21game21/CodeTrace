"""Bounded private Backend/Runner NDJSON; no user output or persistent logs."""

import json
from typing import BinaryIO, cast

from codetrace_contracts import ContractValidationError, validate_contract
from codetrace_contracts.config import BATCH_TARGET_BYTES, PROTOCOL_VERSION

from .canonical import canonical_json, canonical_size


class ProtocolError(RuntimeError):
    """Neutral control-channel failure, with no rejected data in its message."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON field")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ProtocolError("Non-JSON constant")


class ProtocolReader:
    def __init__(self, stream: BinaryIO, run_id: str, max_bytes: int) -> None:
        self._stream = stream
        self.run_id = run_id
        self.max_bytes = max_bytes
        self._buffer = bytearray()
        self._seq = 1
        self.lastLineBytes = 0

    def ensure_no_pending_control(self) -> None:
        if self._buffer:
            raise ProtocolError("Unsolicited control data after execution")

    def receive(self, expected_type: str) -> dict[str, object]:
        while True:
            end = self._buffer.find(b"\n")
            if end >= 0:
                if end > self.max_bytes:
                    raise ProtocolError("Control message too large")
                self.lastLineBytes = end
                raw = bytes(self._buffer[:end])
                del self._buffer[: end + 1]
                break
            if len(self._buffer) > self.max_bytes:
                raise ProtocolError("Control message too large")
            # BufferedReader.read1 does not wait to fill the entire chunk on a pipe.
            read = getattr(self._stream, "read1", self._stream.read)
            chunk = read(min(4096, self.max_bytes + 1 - len(self._buffer)))
            if not chunk:
                raise ProtocolError("Control channel closed or incomplete")
            self._buffer.extend(chunk)
        try:
            value: object = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
            validate_contract(value, "runner")
        except (UnicodeError, ValueError, RecursionError, ContractValidationError):
            raise ProtocolError("Invalid control message") from None
        message = cast(dict[str, object], value)
        if (
            message["protocolVersion"] != PROTOCOL_VERSION
            or type(message["seq"]) is not int
            or message["seq"] != self._seq
            or message["runId"] != self.run_id
            or message["type"] != expected_type
        ):
            raise ProtocolError("Unexpected control envelope")
        self._seq += 1
        return cast(dict[str, object], message["payload"])


class ProtocolWriter:
    def __init__(self, stream: BinaryIO, run_id: str, max_bytes: int) -> None:
        self._stream = stream  # Private original stream, independent of sys.stdout.
        self.run_id = run_id
        self.max_bytes = max_bytes
        self._seq = 1

    def _envelope(self, kind: str, payload: object) -> dict[str, object]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "runId": self.run_id,
            "seq": self._seq,
            "type": kind,
            "payload": payload,
        }

    def message_size(self, kind: str, payload: object) -> int:
        return canonical_size(self._envelope(kind, payload))

    def send(self, kind: str, payload: object) -> None:
        raw = canonical_json(self._envelope(kind, payload)).encode("ascii")
        if len(raw) > self.max_bytes:
            raise ProtocolError("Runner message too large")
        data = raw + b"\n"
        while data:
            written = self._stream.write(data)
            if written is None or written <= 0:
                raise ProtocolError("Transport write failed")
            data = data[written:]
        self._stream.flush()
        self._seq += 1

    def flush(self) -> None:
        self._stream.flush()


class TraceBatch:
    def __init__(self, writer: ProtocolWriter, target_bytes: int = BATCH_TARGET_BYTES) -> None:
        self.writer = writer
        self.target_bytes = target_bytes
        self._records: list[dict[str, object]] = []
        self._bytes = 0
        self.peakBytes = 0

    def add(self, kind: str, record: object) -> None:
        wrapped = {"kind": kind, "record": record}
        size = canonical_size(wrapped)
        # Empty envelope overhead uses the current sequence digit count.
        overhead = self.writer.message_size("trace_chunk", {"records": []})
        if self._records and (
            self._bytes + size + 1 > self.target_bytes
            or overhead + self._bytes + size + 1 > self.writer.max_bytes
        ):
            self.flush()
            overhead = self.writer.message_size("trace_chunk", {"records": []})
        if overhead + size > self.writer.max_bytes:
            raise ProtocolError("Single trace record exceeds message limit")
        self._bytes += size + (1 if self._records else 0)
        self._records.append(wrapped)
        self.peakBytes = max(self.peakBytes, self._bytes)
        if self._bytes >= self.target_bytes:
            self.flush()

    def flush(self) -> None:
        if self._records:
            self.writer.send("trace_chunk", {"records": self._records})
            self._records.clear()
            self._bytes = 0
