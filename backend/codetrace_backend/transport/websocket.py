"""One bounded nonblocking browser channel per Run; WATCHDOG/RECORDER own execution."""

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from anyio import CancelScope
from codetrace_contracts import validate_ws
from codetrace_contracts.types import WsMessage
from fastapi import WebSocket, WebSocketDisconnect

from ..runs import LifecycleEvent, RunRejected, SlotLease
from ..runs.protocol import constant, encode, pairs, text_bytes
from ..trace_recorder import PublicationHandle, RecorderError
from .lifecycle import Runtime


class BrowserProtocolError(ValueError):
    pass


class RunSocket:
    def __init__(self, websocket: WebSocket, runtime: Runtime, *, queue_size: int = 256) -> None:
        self.websocket, self.runtime = websocket, runtime
        self.queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=queue_size)
        self.failed = asyncio.Event()
        self.browser_seq = self.server_seq = 1
        self.run_id: str | None = None
        self.request_id: str | None = None
        self.lease: SlotLease | None = None
        self.publication: asyncio.Task[None] | None = None
        self.started = self.protocol_fault = self.closed = False
        self.input_acks: set[str] = set()
        self.input_inflight = False
        self.deferred_inputs: list[dict[str, object]] = []

    def enqueue(self, kind: str, payload: object, *, run_id: str | None = None) -> None:
        if self.closed or self.failed.is_set():
            return
        message = {
            "apiVersion": 1,
            "seq": self.server_seq,
            "type": kind,
            "runId": self.run_id if run_id is None else run_id,
            "payload": payload,
        }
        validate_ws(message)
        try:
            self.queue.put_nowait(message)
            self.server_seq += 1
        except asyncio.QueueFull:
            # Preserve RECORDER observation even when the browser cannot keep up.
            self.failed.set()

    def observe(self, event: LifecycleEvent) -> None:
        self.runtime.service.observe(event)
        if event.kind == "accepted":
            self.run_id = event.runId
            accepted = cast(dict[str, object], event.payload)
            self.enqueue(
                "run_accepted",
                {
                    "clientRequestId": self.request_id,
                    "acceptedAt": accepted["acceptedAt"],
                    "status": "STARTING",
                },
            )
            self.enqueue("status", {"status": "STARTING"})
        elif event.kind == "status":
            self.enqueue("status", {"status": event.payload})
        elif event.kind == "console":
            self.enqueue("console", {"entry": event.payload})
        elif event.kind == "input_requested":
            payload = cast(dict[str, object], event.payload).copy()
            assert self.lease is not None
            clock = self.runtime.manager.clock
            deadline = (
                self.lease.session.timing.since
                + self.runtime.config.limits["input_idle_timeout_ms"] / 1000
            )
            utc = datetime.fromisoformat(clock.utc().replace("Z", "+00:00"))
            payload["deadlineAt"] = (
                (utc + timedelta(seconds=max(0, deadline - clock.now())))
                .isoformat()
                .replace("+00:00", "Z")
            )
            if self.input_inflight:
                self.deferred_inputs.append(payload)
            else:
                self.enqueue("input_request", payload)
        elif event.kind == "input_accepted":
            self.ack(cast(str, event.payload))

    def ack(self, request_id: str) -> None:
        if request_id not in self.input_acks:
            self.input_acks.add(request_id)
            self.enqueue("input_accepted", {"requestId": request_id})

    def decode(self, raw: str) -> WsMessage:
        try:
            if text_bytes(raw) > self.runtime.config.limits["ws_message_bytes"]:
                raise ValueError
            value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
            message = validate_ws(value)
            if message["seq"] != self.browser_seq:
                raise ValueError
            if message["type"] not in {"start", "input_response"}:
                raise ValueError
            if message["type"] == "start":
                if self.started or message["runId"] is not None:
                    raise ValueError
            elif self.run_id is None or message["runId"] != self.run_id:
                raise ValueError
            self.browser_seq += 1
            return message
        except (ValueError, TypeError, RecursionError):
            raise BrowserProtocolError("Invalid browser protocol") from None

    async def publish(self, handle: PublicationHandle) -> None:
        summary = await handle.await_result()
        self.enqueue("run_finished", summary.as_dict())

    async def receive(self) -> None:
        while True:
            event = await self.websocket.receive()
            if event["type"] == "websocket.disconnect":
                if event.get("code") == 1012:
                    await self.runtime.manager.shutdown()
                return
            raw = event.get("text")
            try:
                if not isinstance(raw, str):
                    raise BrowserProtocolError()
                message = self.decode(raw)
            except BrowserProtocolError:
                self.protocol_fault = True
                self.enqueue(
                    "protocol_error",
                    {"code": "CLIENT_PROTOCOL_ERROR", "message": "Invalid browser protocol"},
                )
                if self.run_id is not None:
                    self.runtime.manager.client_protocol_error(self.run_id)
                with suppress(TimeoutError):
                    async with asyncio.timeout(
                        self.runtime.config.limits["cleanup_command_timeout_ms"] / 1000
                    ):
                        await self.queue.join()
                return
            if message["type"] == "start":
                self.started = True
                self.request_id = message["payload"]["clientRequestId"]
                code = message["payload"]["code"]
                try:
                    if text_bytes(code) > self.runtime.config.limits["code_bytes"]:
                        raise RunRejected("CODE_TOO_LARGE")
                    self.lease, handle = await self.runtime.start(code, str(uuid4()), self.observe)
                    self.publication = asyncio.create_task(self.publish(handle))
                except (RunRejected, RecorderError) as error:
                    reason = error.code if isinstance(error, RunRejected) else error.reason
                    self.enqueue(
                        "run_rejected",
                        {"clientRequestId": self.request_id, "code": reason, "message": reason},
                    )
                    async with asyncio.timeout(
                        self.runtime.config.limits["cleanup_command_timeout_ms"] / 1000
                    ):
                        await self.queue.join()
                    return
            elif message["type"] == "input_response":
                assert self.run_id is not None
                payload = message["payload"]
                self.input_inflight = True
                try:
                    result = await self.runtime.manager.submit_input(
                        self.run_id, payload["requestId"], payload["text"]
                    )
                    if result.accepted:
                        self.ack(payload["requestId"])
                    else:
                        self.enqueue(
                            "input_rejected",
                            {
                                "requestId": payload["requestId"],
                                "code": result.code,
                                "message": result.code,
                            },
                        )
                finally:
                    self.input_inflight = False
                    for pending in self.deferred_inputs:
                        self.enqueue("input_request", pending)
                    self.deferred_inputs.clear()

    async def send(self) -> None:
        while True:
            message = await self.queue.get()
            try:
                async with asyncio.timeout(
                    self.runtime.config.limits["cleanup_command_timeout_ms"] / 1000
                ):
                    await self.websocket.send_text(encode(message).decode("ascii"))
            finally:
                self.queue.task_done()
            if message["type"] == "run_finished":
                return

    async def serve(self) -> None:
        await self.websocket.accept()
        self.enqueue("socket_ready", {"apiVersion": 1, "limits": self.runtime.config.limits.copy()})
        tasks = [
            asyncio.create_task(self.receive()),
            asyncio.create_task(self.send()),
            asyncio.create_task(self.failed.wait()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, OSError, TimeoutError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            self.closed = True
            if self.run_id is not None and not self.protocol_fault:
                self.runtime.manager.connection_lost(self.run_id)
            if self.publication is not None:
                tasks.append(self.publication)
            for task in tasks:
                task.cancel()
            with CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await self.websocket.close(code=1000 if not self.protocol_fault else 1008)
