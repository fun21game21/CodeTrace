"""Exact sealed-byte streaming with loop-confined reader disposal."""

from collections.abc import AsyncIterator

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from ..trace_recorder import TraceAccess


class TraceResponse(StreamingResponse):
    def __init__(self, access: TraceAccess) -> None:
        self.access = access
        super().__init__(
            self.chunks(),
            media_type=access.contentType,
            headers={"Content-Length": str(access.traceBytes), "Cache-Control": "no-store"},
        )

    async def chunks(self) -> AsyncIterator[bytes]:
        # Reads and close stay on the ResultService event loop, never a threadpool.
        while part := self.access.read():
            yield part

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also covers cancellation before the iterator's first read.
            self.access.close()
