import assert from "node:assert/strict";
import { test } from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import {
  begun,
  finish,
  health,
  REQUEST,
  RUN,
  settled,
  setup,
  validTrace,
  waiting,
} from "./helpers.ts";

const CODE = '  # exact\r\nx = "🙂"\r\n';
test("health ready enables Run and keeps editable draft", async () => {
  const h = setup(CODE);
  await h.controller.refreshHealth();
  assert(h.controller.canRun());
  assert.equal(h.controller.getSnapshot().editor.draftCode, CODE);
  h.controller.dispose();
});
test("health unavailable disables Run without hiding editor", async () => {
  const h = setup();
  h.setHttp(async () => ({ status: 200, json: async () => health(false) }));
  await h.controller.refreshHealth();
  assert(!h.controller.canRun());
  h.controller.setDraft("x=1");
  assert.equal(h.controller.getSnapshot().editor.draftCode, "x=1");
  h.controller.dispose();
});
test("health network failure becomes finite error", async () => {
  const h = setup();
  h.setHttp(async () => {
    throw Error();
  });
  await h.controller.refreshHealth();
  assert.equal(h.controller.getSnapshot().healthPhase, "FAILED");
  h.controller.dispose();
});
test("health rejects malformed metadata", async () => {
  const h = setup();
  h.setHttp(async () => ({
    status: 200,
    json: async () => ({ ...health(), pythonVersion: "3.14" }),
  }));
  await h.controller.refreshHealth();
  assert.equal(h.controller.getSnapshot().healthPhase, "FAILED");
  h.controller.dispose();
});
test("Run opens fresh socket and waits socket_ready", async () => {
  const h = setup(CODE);
  await h.controller.refreshHealth();
  h.controller.run();
  assert.equal(h.sockets.length, 1);
  assert.equal(h.sockets[0]!.sent.length, 0);
  assert(!h.controller.canRun());
  h.controller.dispose();
});
test("start uses seq1, requestId and exact frozen candidate once", async () => {
  const h = setup(CODE);
  await h.controller.refreshHealth();
  h.controller.run();
  h.controller.setDraft("different draft");
  const s = h.sockets[0]!;
  s.ready();
  const m = JSON.parse(s.sent[0]!);
  assert.equal(m.seq, 1);
  assert.equal(m.runId, null);
  assert.equal(m.payload.clientRequestId, REQUEST);
  assert.equal(m.payload.code, CODE);
  assert.equal(h.controller.getSnapshot().editor.mode, "editing");
  assert.equal(h.controller.getSnapshot().editor.draftCode, "different draft");
  s.ready();
  assert.equal(s.sent.length, 1);
  assert(h.controller.getSnapshot().transportError);
  h.controller.dispose();
});
test("accepted freezes source and read-only mode; later draft writes ignored", async () => {
  const h = await begun(CODE);
  h.controller.setDraft("changed");
  const s = h.controller.getSnapshot();
  assert.equal(s.editor.runSourceCode, CODE);
  assert.equal(s.editor.mode, "run_source");
  assert.equal(s.active.sourceCode, CODE);
  assert(!h.controller.canRun());
  assert(!h.controller.canEdit());
  h.controller.dispose();
});
test("rejected start creates no fake ActiveRun", async () => {
  const h = setup(CODE);
  await h.controller.refreshHealth();
  h.controller.run();
  const s = h.sockets[0]!;
  s.ready();
  s.emit(
    "run_rejected",
    { clientRequestId: REQUEST, code: "RUN_BUSY", message: "busy" },
    null,
  );
  assert.equal(h.controller.getSnapshot().active.runId, null);
  assert.equal(h.controller.getSnapshot().editor.mode, "editing");
  assert(h.controller.canRun());
  h.controller.dispose();
});
test("wrong accepted clientRequestId is transport error", async () => {
  const h = setup();
  await h.controller.refreshHealth();
  h.controller.run();
  const s = h.sockets[0]!;
  s.ready();
  s.emit("run_accepted", {
    clientRequestId: RUN,
    acceptedAt: "2026-10-05T20:00:00Z",
    status: "STARTING",
  });
  assert(h.controller.getSnapshot().transportError);
  assert.equal(h.controller.getSnapshot().active.runId, null);
  h.controller.dispose();
});
for (const status of [
  "RUNNING",
  "WAITING_INPUT",
  "FINALIZING",
  "SUCCEEDED",
  "FAILED",
  "LIMIT_EXCEEDED",
  "INTERRUPTED",
])
  test(`backend status ${status} applied without guessing outcome`, async () => {
    const h = await begun();
    if (status !== "RUNNING") h.socket.status("RUNNING");
    if (
      ["SUCCEEDED", "FAILED", "LIMIT_EXCEEDED", "INTERRUPTED"].includes(status)
    )
      h.socket.status("FINALIZING");
    h.socket.status(status);
    assert.equal(h.controller.getSnapshot().active.status, status);
    assert.equal(h.controller.getSnapshot().active.completion, null);
    h.controller.dispose();
  });
test("invalid lifecycle jump is transport error", async () => {
  const h = await begun();
  h.socket.status("SUCCEEDED");
  assert(h.controller.getSnapshot().transportError);
  h.controller.dispose();
});
test("Console appends exact ordered text and never interprets HTML", async () => {
  const h = await begun();
  h.socket.status("RUNNING");
  for (const [index, text] of ["<script>bad</script>", "🙂\n"].entries())
    h.socket.emit("console", {
      entry: { index, kind: "stdout", text, requestId: null },
    });
  assert.deepEqual(
    h.controller.getSnapshot().active.liveConsole.map((e) => e.text),
    ["<script>bad</script>", "🙂\n"],
  );
  assert(Object.isFrozen(h.controller.getSnapshot().active.liveConsole));
  h.controller.dispose();
});
test("Console rejects noncontiguous index", async () => {
  const h = await begun();
  h.socket.status("RUNNING");
  h.socket.emit("console", {
    entry: { index: 2, kind: "stdout", text: "x", requestId: null },
  });
  assert(h.controller.getSnapshot().transportError);
  h.controller.dispose();
});
test("Input request preserves prompt and deadline", async () => {
  const h = await begun();
  waiting(h.socket);
  const p = h.controller.getSnapshot().active.pendingInput!;
  assert.equal(p.prompt, "Ответ: ");
  assert.equal(p.deadlineAt, "2026-10-05T20:02:00Z");
  h.controller.dispose();
});
for (const text of ["", "   ", "  привет 🙂  ", "é", "😀".repeat(2048)])
  test(`exact Input (${text.length} UTF-16 units) and independent seq`, async () => {
    const h = await begun();
    waiting(h.socket);
    h.controller.setInput(text);
    assert(h.controller.submitInput());
    const m = JSON.parse(h.socket.sent[1]!);
    assert.equal(m.seq, 2);
    assert.equal(m.payload.text, text);
    assert.equal(m.payload.requestId, "q1");
    assert.equal(m.runId, RUN);
    assert.equal(h.controller.getSnapshot().active.liveConsole.length, 0);
    h.controller.dispose();
  });
for (const text of ["a\nb", "a\rb", "😀".repeat(2049)])
  test(`invalid input rejected locally (${text.length} units)`, async () => {
    const h = await begun();
    waiting(h.socket);
    h.controller.setInput(text);
    assert(!h.controller.submitInput());
    assert.equal(h.socket.sent.length, 1);
    assert(h.controller.getSnapshot().active.pendingInput!.error);
    h.controller.dispose();
  });
test("duplicate Submit prevented until ACK", async () => {
  const h = await begun();
  waiting(h.socket);
  assert(h.controller.submitInput());
  assert(!h.controller.submitInput());
  assert.equal(h.socket.sent.length, 2);
  h.controller.dispose();
});
test("input accepted clears panel, awaits independent status", async () => {
  const h = await begun();
  waiting(h.socket);
  h.controller.submitInput();
  h.socket.emit("input_accepted", { requestId: "q1" });
  assert.equal(h.controller.getSnapshot().active.pendingInput, null);
  assert.equal(h.controller.getSnapshot().active.status, "WAITING_INPUT");
  h.socket.status("RUNNING");
  assert.equal(h.controller.getSnapshot().active.status, "RUNNING");
  h.controller.dispose();
});
test("input rejection keeps text and original deadline for correction", async () => {
  const h = await begun();
  waiting(h.socket);
  h.controller.setInput("answer");
  h.controller.submitInput();
  const before = h.controller.getSnapshot().active.pendingInput!.deadlineAt;
  h.socket.emit("input_rejected", {
    requestId: "q1",
    code: "INVALID_INPUT",
    message: "invalid",
  });
  const p = h.controller.getSnapshot().active.pendingInput!;
  assert.equal(p.text, "answer");
  assert.equal(p.deadlineAt, before);
  assert(!p.submitting);
  h.controller.setInput("fixed");
  assert(h.controller.submitInput());
  assert.equal(JSON.parse(h.socket.sent[2]!).seq, 3);
  h.controller.dispose();
});
test("RUNNING before input ACK and next WAITING status remain valid", async () => {
  const h = await begun();
  waiting(h.socket);
  h.controller.submitInput();
  h.socket.status("RUNNING");
  h.socket.status("WAITING_INPUT");
  h.socket.emit("input_accepted", { requestId: "q1" });
  h.socket.emit("input_request", {
    requestId: "q2",
    prompt: "next",
    consoleOffset: 0,
    deadlineAt: "2026-10-05T20:03:00Z",
  });
  assert.equal(h.controller.getSnapshot().active.pendingInput!.requestId, "q2");
  h.controller.dispose();
});
for (const bad of [
  "not-json",
  JSON.stringify({
    apiVersion: 2,
    seq: 3,
    type: "status",
    runId: RUN,
    payload: { status: "RUNNING" },
  }),
  JSON.stringify({
    apiVersion: 1,
    seq: 99,
    type: "status",
    runId: RUN,
    payload: { status: "RUNNING" },
  }),
  JSON.stringify({
    apiVersion: 1,
    seq: 4,
    type: "status",
    runId: REQUEST,
    payload: { status: "RUNNING" },
  }),
])
  test(`malformed/version/seq/runId server message ${bad.slice(0, 30)}`, async () => {
    const h = await begun();
    h.socket.message(bad);
    assert(h.controller.getSnapshot().transportError);
    assert(h.controller.getSnapshot().active.transportLost);
    assert.equal(h.sockets.length, 1);
    h.controller.dispose();
  });
test("socket loss preserves immutable known source and never reconnects", async () => {
  const h = await begun(CODE);
  h.socket.closeHandler();
  await settled();
  assert.equal(h.sockets.length, 1);
  assert.equal(h.controller.getSnapshot().active.runId, RUN);
  assert.equal(h.controller.getSnapshot().editor.runSourceCode, CODE);
  assert(h.controller.canRetryDownload());
  h.controller.dispose();
});
test("real TRACE load → READY → release only after ownership validation", async () => {
  const h = await begun(CODE);
  finish(h.socket, CODE);
  await settled();
  const s = h.controller.getSnapshot();
  assert.equal(s.active.completion!.outcome, "SUCCESS");
  assert.equal(s.trace.phase, "READY");
  assert(s.trace.handle instanceof TraceCore);
  assert.equal(s.trace.handle.sourceCode, CODE);
  assert.equal(s.trace.handle.inspect().materializedStateCount, 0);
  assert.equal(s.trace.releasePending, false);
  assert.deepEqual(
    h.calls.slice(1).map((c) => c.method),
    ["GET", "POST"],
  );
  h.socket.closeHandler();
  assert.equal(h.controller.getSnapshot().transportError, null);
  h.controller.dispose();
});
for (const corruption of [
  "runId",
  "sourceCode",
  "schemaVersion",
  "RUN_END",
  "JSON",
  "HTTP",
])
  test(`failed download ${corruption} keeps result and never releases`, async () => {
    const h = await begun(CODE);
    h.setHttp(async () => ({
      status: corruption === "HTTP" ? 500 : 200,
      json: async () => {
        if (corruption === "JSON") throw Error();
        const t = validTrace(CODE);
        if (corruption === "runId") t.runId = REQUEST;
        if (corruption === "sourceCode") t.sourceCode = "wrong";
        if (corruption === "schemaVersion")
          (t as unknown as { schemaVersion: string }).schemaVersion = "9";
        if (corruption === "RUN_END") t.events.pop();
        return t;
      },
    }));
    finish(h.socket, CODE);
    await settled();
    assert.equal(h.controller.getSnapshot().trace.phase, "LOAD_FAILED");
    assert.equal(h.controller.getSnapshot().active.runId, RUN);
    assert.equal(h.controller.getSnapshot().editor.runSourceCode, CODE);
    assert.equal(h.calls.filter((c) => c.method === "POST").length, 0);
    h.controller.dispose();
  });
test("Retry GET succeeds using real TRACE without another socket/start", async () => {
  const h = await begun(CODE);
  h.setHttp(async () => ({ status: 409, json: async () => null }));
  finish(h.socket, CODE);
  await settled();
  h.setHttp(async (_url, method) => ({
    status: method === "POST" ? 204 : 200,
    json: async () => validTrace(CODE),
  }));
  await h.controller.download();
  assert.equal(h.controller.getSnapshot().trace.phase, "READY");
  assert.equal(h.sockets.length, 1);
  assert.equal(h.socket.sent.length, 1);
  h.controller.dispose();
});
test("release failure keeps TraceCore and explicit retry frees ownership", async () => {
  const h = await begun();
  h.setHttp(async (_url, method) => ({
    status: method === "POST" ? 500 : 200,
    json: async () => validTrace(),
  }));
  finish(h.socket);
  await settled();
  const handle = h.controller.getSnapshot().trace.handle;
  assert(handle);
  assert(h.controller.getSnapshot().trace.releasePending);
  assert(h.controller.getSnapshot().trace.releaseError);
  h.setHttp(async () => ({ status: 204, json: async () => null }));
  await h.controller.retryRelease();
  assert.equal(h.controller.getSnapshot().trace.handle, handle);
  assert(!h.controller.getSnapshot().trace.releasePending);
  h.controller.dispose();
});
test("Edit copies Run source to draft and clears trace", async () => {
  const h = await begun(CODE);
  finish(h.socket, CODE);
  await settled();
  h.controller.edit();
  assert.equal(h.controller.canRetryDownload(), false);
  assert.equal(h.controller.getSnapshot().editor.mode, "editing");
  assert.equal(h.controller.getSnapshot().editor.draftCode, CODE);
  assert.equal(h.controller.getSnapshot().trace.handle, null);
  assert(h.controller.canRun());
  h.controller.dispose();
});
test("new Run resets server/browser seq and does not clear old trace until accepted", async () => {
  const h = await begun(CODE);
  finish(h.socket, CODE);
  await settled();
  const old = h.controller.getSnapshot().trace;
  h.controller.edit();
  h.controller.setDraft("new");
  assert(h.controller.run());
  const second = h.sockets[1]!;
  assert.notEqual(second, h.socket);
  assert.equal(h.controller.getSnapshot().active.runId, RUN);
  assert.equal(h.controller.getSnapshot().trace.phase, "EMPTY");
  second.ready();
  assert.equal(JSON.parse(second.sent[0]!).seq, 1);
  second.accept();
  assert.equal(h.controller.getSnapshot().active.sourceCode, "new");
  assert.equal(h.controller.getSnapshot().trace.handle, null);
  assert(old.handle);
  h.controller.dispose();
});
test("retained release failure remains retryable after Edit", async () => {
  const h = await begun();
  h.setHttp(async (_url, method) => ({
    status: method === "POST" ? 500 : 200,
    json: async () => validTrace(),
  }));
  finish(h.socket);
  await settled();
  h.controller.edit();
  assert(!h.controller.canRun());
  assert(h.controller.getSnapshot().trace.releasePending);
  h.setHttp(async () => ({ status: 204, json: async () => null }));
  await h.controller.retryRelease();
  assert(h.controller.canRun());
  h.controller.dispose();
});
test("trace unavailable saves Completion without fake TraceCore", async () => {
  const h = await begun();
  finish(h.socket, "", false);
  await settled();
  assert.equal(h.controller.getSnapshot().trace.phase, "EMPTY");
  assert.equal(h.controller.getSnapshot().trace.handle, null);
  assert.equal(h.calls.length, 1);
  assert.equal(
    h.controller.getSnapshot().active.completion!.outcome,
    "SUCCESS",
  );
  h.controller.dispose();
});
test("run_finished before terminal causes transport error, no GET", async () => {
  const h = await begun();
  const t = validTrace();
  h.socket.emit("run_finished", {
    completion: t.completion,
    traceAvailable: true,
    traceUrl: `/api/runs/${RUN}/trace`,
    traceBytes: 1,
    eventCount: 3,
  });
  assert(h.controller.getSnapshot().transportError);
  assert.equal(h.calls.length, 1);
  h.controller.dispose();
});
test("unsafe result URL rejected before HTTP request", async () => {
  const h = await begun();
  h.socket.status("RUNNING");
  h.socket.status("FINALIZING");
  h.socket.status("SUCCEEDED");
  h.socket.emit("run_finished", {
    completion: validTrace().completion,
    traceAvailable: true,
    traceUrl: "https://evil.example/trace",
    traceBytes: 1,
    eventCount: 3,
  });
  assert(h.controller.getSnapshot().transportError);
  assert.equal(h.calls.length, 1);
  h.controller.dispose();
});
test("explicit result check after loss loads sealed result without reconnect", async () => {
  const h = await begun(CODE);
  h.socket.closeHandler();
  await h.controller.download();
  assert.equal(h.controller.getSnapshot().trace.phase, "READY");
  assert(h.controller.canEdit());
  assert.equal(h.sockets.length, 1);
  assert.equal(h.controller.getSnapshot().active.status, "STARTING");
  h.controller.dispose();
});
test("disposed controller ignores late download and never releases", async () => {
  const h = await begun();
  let resolve: (value: {
    status: number;
    json(): Promise<unknown>;
  }) => void = () => {};
  h.setHttp(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  finish(h.socket);
  h.controller.dispose();
  resolve({ status: 200, json: async () => validTrace() });
  await settled();
  assert.equal(h.calls.filter((c) => c.method === "POST").length, 0);
});
