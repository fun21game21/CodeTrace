import assert from "node:assert/strict";
import { request } from "node:http";
import WebSocket from "ws";
import { RunController } from "../../src/runs/controller.ts";
import { TraceCore } from "../../src/trace/index.ts";
import type { SocketPort } from "../../src/runs/types.ts";
const ORIGIN = "http://127.0.0.1:8000";
let sockets = 0,
  starts = 0,
  releases = 0,
  gets = 0;
const controller = new RunController({
  uuid: () => crypto.randomUUID(),
  fetch: async (path, options) => {
    if (options?.method === "POST") releases++;
    if (path.endsWith("/trace")) gets++;
    return fetch(ORIGIN + path, {
      ...options,
      headers: options?.method === "POST" ? { Origin: ORIGIN } : {},
      signal: AbortSignal.timeout(20000),
    });
  },
  socket(): SocketPort {
    sockets++;
    const ws = new WebSocket("ws://127.0.0.1:8000/api/runs", {
      origin: ORIGIN,
      perMessageDeflate: false,
      maxPayload: 524288,
    });
    return {
      onMessage: (handler) =>
        ws.on("message", (data) => handler(data.toString())),
      onClose: (handler) => ws.on("close", handler),
      onError: (handler) => ws.on("error", handler),
      send: (text) => {
        if (JSON.parse(text).type === "start") starts++;
        ws.send(text);
      },
      close: () => ws.close(),
    };
  },
});
async function until(predicate: () => boolean) {
  const deadline = Date.now() + 40000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    assert.equal(controller.getSnapshot().transportError, null);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw Error("Production smoke deadline");
}
try {
  const root = await fetch(ORIGIN + "/");
  assert.equal(root.status, 200);
  const html = await root.text();
  assert(html.includes("<title>CodeTrace"));
  const asset = /src="([^"]+\.js)"/.exec(html)![1]!;
  assert.equal((await fetch(ORIGIN + asset)).status, 200);
  const absent = await fetch(ORIGIN + "/api/not-found");
  assert.equal(absent.status, 404);
  assert(!absent.headers.get("content-type")!.includes("html"));
  const foreignHostStatus = await new Promise<number | undefined>(
    (resolve, reject) => {
      const req = request(
        ORIGIN + "/api/health",
        { headers: { Host: "foreign.example" }, timeout: 5000 },
        (response) => {
          response.resume();
          resolve(response.statusCode);
        },
      );
      req.on("error", reject);
      req.on("timeout", () => req.destroy(new Error("Host check timeout")));
      req.end();
    },
  );
  assert.equal(foreignHostStatus, 403);
  assert.equal(
    (
      await fetch(ORIGIN + "/api/runs/missing/release", {
        method: "POST",
        headers: { Origin: "http://foreign.example" },
      })
    ).status,
    403,
  );
  for (const origin of [undefined, "null", "http://foreign.example"]) {
    await new Promise<void>((resolve, reject) => {
      const socket = new WebSocket(
        "ws://127.0.0.1:8000/api/runs",
        origin === undefined ? {} : { origin },
      );
      socket.on("error", () => {});
      socket.once("open", () => {
        socket.close();
        reject(new Error("Foreign WS origin accepted"));
      });
      socket.once("unexpected-response", (_request, response) => {
        assert.equal(response.statusCode, 403);
        response.resume();
        socket.terminate();
        resolve();
      });
    });
  }
  await controller.refreshHealth();
  assert(controller.getSnapshot().health!.available);
  const code =
    'a = input("A: ")\nb = input("B: ")\nvalues = [a, b]\nprint(a, b)\n';
  controller.setDraft(code);
  assert(controller.run());
  let answered = 0;
  await until(() => {
    const s = controller.getSnapshot();
    const input = s.active.pendingInput;
    if (input && !input.submitting) {
      controller.setInput(answered === 0 ? "  one 🙂  " : "");
      assert(controller.submitInput());
      answered++;
    }
    return s.trace.phase === "READY" && !s.trace.releasePending;
  });
  let state = controller.getSnapshot();
  assert.equal(answered, 2);
  assert(state.trace.handle instanceof TraceCore);
  assert.equal(state.trace.handle.sourceCode, code);
  assert.equal(state.active.completion!.outcome, "SUCCESS");
  assert.equal(state.trace.handle.inspect().materializedStateCount, 0);
  const firstId = state.active.runId;
  assert.equal(
    state.active.liveConsole.filter((e) => e.kind === "input").length,
    2,
  );
  assert.equal(
    (await fetch(ORIGIN + `/api/runs/${firstId}/trace`)).status,
    404,
  );
  controller.edit();
  controller.setDraft("x = 42\nprint(x)\n");
  assert(controller.run());
  await until(
    () =>
      controller.getSnapshot().trace.phase === "READY" &&
      !controller.getSnapshot().trace.releasePending,
  );
  state = controller.getSnapshot();
  assert.equal(state.active.completion!.reason, "NONE");
  assert.notEqual(state.active.runId, firstId);
  assert.equal(sockets, 2);
  assert.equal(starts, 2);
  assert.equal(gets, 2);
  assert.equal(releases, 2);
  console.log(
    JSON.stringify({
      productionPath:
        "FastAPI → WS → Docker → RECORDER → exact HTTP → actual TRACE → release",
      runs: 2,
      inputs: 2,
      sockets,
      starts,
      gets,
      releases,
      slotFreeProvedBySecondRun: true,
    }),
  );
} finally {
  controller.dispose();
}
