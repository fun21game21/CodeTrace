"""Exact Host/Origin checks, including duplicate and absent headers."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import HOST, AppConfig


def exact_header(scope: Scope, name: bytes) -> str | None:
    values = [value for key, value in scope.get("headers", []) if key.lower() == name]
    return values[0].decode("latin-1") if len(values) == 1 else None


class LocalOriginMiddleware:
    def __init__(self, app: ASGIApp, config: AppConfig) -> None:
        self.app, self.config = app, config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind in {"http", "websocket"}:
            valid = exact_header(scope, b"host") == HOST
            if kind == "websocket" or scope.get("method") == "POST":
                valid = valid and exact_header(scope, b"origin") in self.config.origins
            if not valid:
                if kind == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"code": "LOCAL_ORIGIN_REQUIRED"}, status_code=403)(
                        scope, receive, send
                    )
                return
        await self.app(scope, receive, send)
