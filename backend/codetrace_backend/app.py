"""Production local FastAPI entrypoint: python -m codetrace_backend.app."""

import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.responses import FileResponse, JSONResponse, Response

from .config import AppConfig, production_address
from .trace_recorder import ResultAccessError
from .transport.http import TraceResponse
from .transport.lifecycle import Runtime
from .transport.security import LocalOriginMiddleware
from .transport.websocket import RunSocket


def create_app(config: AppConfig | None = None, *, runtime: Runtime | None = None) -> FastAPI:
    effective = AppConfig.from_environment() if config is None else config
    resources = Runtime(effective) if runtime is None else runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await resources.startup()
            yield
        finally:
            await resources.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOriginMiddleware, config=effective)
    app.state.runtime = resources

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(await resources.refresh(), headers={"Cache-Control": "no-store"})

    @app.websocket("/api/runs")
    async def runs(websocket: WebSocket) -> None:
        await RunSocket(websocket, resources).serve()

    @app.get("/api/runs/{run_id}/trace")
    async def trace(run_id: str) -> Response:
        try:
            return TraceResponse(resources.service.get_trace(run_id))
        except ResultAccessError as error:
            return JSONResponse({"code": error.code}, status_code=error.status)

    @app.post("/api/runs/{run_id}/release")
    async def release(run_id: str) -> Response:
        try:
            return Response(status_code=await resources.service.release(run_id))
        except ResultAccessError as error:
            return JSONResponse({"code": error.code}, status_code=error.status)

    @app.get("/{path:path}")
    async def frontend(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        directory = effective.frontend_dist.resolve()
        target = (directory / path).resolve()
        if not target.is_relative_to(directory):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if target.is_file():
            return FileResponse(target)
        if Path(path).suffix:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index = directory / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"code": "FRONTEND_BUILD_REQUIRED"}, status_code=503)

    return app


def main() -> None:
    config = AppConfig.from_environment()
    host, port = production_address()
    # Reserve the fixed port BEFORE lifespan cleanup: a second backend must not
    # delete the first backend's managed containers or temporary result.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(config),
                host=host,
                port=port,
                workers=1,
                proxy_headers=False,
                ws="websockets-sansio",
                ws_max_size=config.limits["ws_message_bytes"],
                ws_max_queue=16,
                ws_per_message_deflate=False,
                timeout_graceful_shutdown=5,
            )
        )
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            # Uvicorn restores and replays SIGINT after its awaited shutdown.
            # Ctrl+C is a normal exit for the local desktop entrypoint.
            pass


if __name__ == "__main__":
    main()
