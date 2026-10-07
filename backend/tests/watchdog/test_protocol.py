import asyncio

import pytest
from codetrace_backend.runs.protocol import ProtocolViolation, Reader, encode

from .helpers import RUN_ID


@pytest.mark.parametrize("width", [1, 2, 7, 4096])
def test_fragmented_reader(width):
    async def scenario():
        stream = asyncio.StreamReader()
        value = {
            "protocolVersion": 1,
            "runId": RUN_ID,
            "seq": 1,
            "type": "ready",
            "payload": {"pythonVersion": "3.13.16", "protocolVersion": 1},
        }
        raw = encode(value) + b"\n"

        async def feed():
            for index in range(0, len(raw), width):
                stream.feed_data(raw[index : index + width])
                await asyncio.sleep(0)
            stream.feed_eof()

        task = asyncio.create_task(feed())
        reader = Reader(stream, RUN_ID, 1000)
        assert await reader.next() == value
        assert await reader.next() is None
        await task

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw", [b"x" * 101, b"x" * 101 + b"\n", b"{", b"\xff\n", b'{"x":NaN}\n', b'{"x":1,"x":2}\n']
)
def test_invalid_or_oversized_reader(raw):
    async def scenario():
        stream = asyncio.StreamReader()
        stream.feed_data(raw)
        stream.feed_eof()
        reader = Reader(stream, RUN_ID, 100)
        with pytest.raises(ProtocolViolation):
            await reader.next()
        assert len(reader.buffer) <= 101

    asyncio.run(scenario())


def test_input_echo_has_input_cap_not_write_chunk_cap():
    from codetrace_backend.runs.protocol import Prefix
    from codetrace_contracts import default_limits

    limits = default_limits()
    entry = {"index": 0, "kind": "input", "text": "x" * 8192 + "\n", "requestId": "q1"}
    assert Prefix().output(entry, limits).consoleBytes == 8193
    entry["text"] += "x"
    with pytest.raises(ProtocolViolation):
        Prefix().output(entry, limits)


def test_ordinary_state_can_exceed_terminal_reserve():
    from codetrace_backend.runs.protocol import Prefix
    from codetrace_contracts import default_limits

    from .helpers import checkpoint

    limits = default_limits()
    limits["terminal_reserve_bytes"] = 1
    prefix = Prefix().chunk([checkpoint()], limits)
    assert prefix.ordinaryBytes > 1 and prefix.pendingStateBytes == 0
