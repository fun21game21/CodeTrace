import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { defaultLimits } from "../../../contracts/typescript/index.ts";
import type {
  RunFinishedPayload,
  TraceDocument,
} from "../../../contracts/typescript/index.ts";
import { RunController } from "../../src/runs/controller.ts";
import type { SocketPort, HttpResult } from "../../src/runs/types.ts";

export const REQUEST = "dd53c9a6-1c0e-4a60-b075-c95b00a23854";
export const RUN = "8ba106dd-35b6-4945-a5db-e1d3120c743d";
export function validTrace(code = ""): TraceDocument {
  const trace = JSON.parse(
    readFileSync(
      new URL(
        "../../../contracts/fixtures/valid/trace-minimal-success.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as TraceDocument;
  trace.sourceCode = code;
  return trace;
}
export const health = (available = true) => ({
  apiVersion: 1,
  available,
  reason: available ? null : "DOCKER_UNAVAILABLE",
  pythonVersion: "3.13.16",
  limits: defaultLimits(),
});
export class Socket implements SocketPort {
  sent: string[] = [];
  closed = false;
  seq = 1;
  message: (data: unknown) => void = () => {};
  closeHandler: () => void = () => {};
  errorHandler: () => void = () => {};
  onMessage(handler: (data: unknown) => void) {
    this.message = handler;
  }
  onClose(handler: () => void) {
    this.closeHandler = handler;
  }
  onError(handler: () => void) {
    this.errorHandler = handler;
  }
  send(text: string) {
    assert.equal(this.closed, false);
    this.sent.push(text);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, payload: unknown, runId: string | null = RUN) {
    this.message(
      JSON.stringify({ apiVersion: 1, seq: this.seq++, type, runId, payload }),
    );
  }
  ready() {
    this.emit("socket_ready", { apiVersion: 1, limits: defaultLimits() }, null);
  }
  accept() {
    this.emit("run_accepted", {
      clientRequestId: REQUEST,
      acceptedAt: "2026-10-05T20:00:00Z",
      status: "STARTING",
    });
  }
  status(status: string) {
    this.emit("status", { status });
  }
}
export function setup(code = "") {
  const sockets: Socket[] = [],
    calls: { url: string; method: string }[] = [];
  let http: (url: string, method: string) => Promise<HttpResult> = async (
    url,
    method,
  ) => ({
    status: method === "POST" ? 204 : 200,
    json: async () => (url === "/api/health" ? health() : validTrace(code)),
  });
  const controller = new RunController(
    {
      uuid: () => REQUEST,
      socket: () => {
        const socket = new Socket();
        sockets.push(socket);
        return socket;
      },
      fetch: async (url, options) => {
        const method = options?.method ?? "GET";
        calls.push({ url, method });
        return http(url, method);
      },
    },
    code,
  );
  return {
    controller,
    sockets,
    calls,
    setHttp(handler: typeof http) {
      http = handler;
    },
  };
}
export type Harness = ReturnType<typeof setup>;
export async function begun(code = "") {
  const h = setup(code);
  await h.controller.refreshHealth();
  assert(h.controller.run());
  const socket = h.sockets[0]!;
  socket.ready();
  socket.accept();
  socket.status("STARTING");
  return { ...h, socket };
}
export function waiting(socket: Socket, requestId = "q1") {
  socket.status("RUNNING");
  socket.status("WAITING_INPUT");
  socket.emit("input_request", {
    requestId,
    prompt: "Ответ: ",
    consoleOffset: 0,
    deadlineAt: "2026-10-05T20:02:00Z",
  });
}
export function finish(socket: Socket, code = "", traceAvailable = true) {
  socket.status("RUNNING");
  socket.status("FINALIZING");
  socket.status("SUCCEEDED");
  const trace = validTrace(code);
  const payload: RunFinishedPayload = {
    completion: trace.completion,
    traceAvailable,
    traceUrl: traceAvailable ? `/api/runs/${RUN}/trace` : null,
    traceBytes: traceAvailable ? JSON.stringify(trace).length : null,
    eventCount: traceAvailable ? trace.events.length : null,
  };
  socket.emit("run_finished", payload);
}
export async function settled() {
  for (let i = 0; i < 8; i++)
    await new Promise<void>((resolve) => setImmediate(resolve));
}
