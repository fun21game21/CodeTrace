import assert from "node:assert/strict";
import WebSocket from "ws";
import { RunController } from "../../src/runs/controller.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import type { SocketPort } from "../../src/runs/types.ts";
const ORIGIN = "http://127.0.0.1:8000";
let starts = 0,
  sockets = 0,
  gets = 0,
  releases = 0,
  requests = 0;
const run = new RunController({
  uuid: () => crypto.randomUUID(),
  fetch: async (path, options) => {
    requests++;
    if (path.endsWith("/trace")) gets++;
    if (options?.method === "POST") releases++;
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
      send: (raw) => {
        if (JSON.parse(raw).type === "start") starts++;
        ws.send(raw);
      },
      close: () => ws.close(),
    };
  },
});
const viewer = new ViewerController(),
  unbind = bindRunViewer(run, viewer);
async function until(predicate: () => boolean) {
  const deadline = Date.now() + 40000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    assert.equal(run.getSnapshot().transportError, null);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw Error("VIEWER production deadline");
}
async function flush() {
  for (let i = 0; i < 16; i++) await Promise.resolve();
}
try {
  await run.refreshHealth();
  assert(run.getSnapshot().health!.available);
  const code =
    'print("start")\ndef total(n):\n    print("level", n)\n    if n == 0:\n        return 0\n    tail = total(n - 1)\n    return n + tail\nresult = total(2)\nprint("done", result)\n';
  run.setDraft(code);
  assert(run.run());
  await until(
    () =>
      run.getSnapshot().trace.phase === "READY" &&
      !run.getSnapshot().trace.releasePending,
  );
  await flush();
  const c = run.getSnapshot().trace.handle!,
    count = { starts, sockets, gets, releases, requests };
  assert.equal(c.sourceCode, code);
  assert.equal(viewer.getSnapshot().committedStep, 1);
  assert.equal(viewer.getSnapshot().committedView!.consoleEntries.length, 0);
  viewer.next();
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, 2);
  viewer.previous();
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, 1);
  viewer.setStepDraft(String(c.totalSteps));
  assert(viewer.commitStepDraft());
  await flush();
  let v = viewer.getSnapshot().committedView!;
  assert.equal(v.event.kind, "RUN_END");
  assert.equal(v.completion!.outcome, "SUCCESS");
  assert(
    v.consoleEntries
      .map((e) => e.text)
      .join("")
      .includes("done 3\n"),
  );
  const result = v.globals.find((b) => b.name === "result")!;
  assert.deepEqual(result.value, { kind: "scalar", type: "int", value: "3" });
  viewer.previous();
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, c.totalSteps - 1);
  viewer.seek(1);
  await flush();
  assert.equal(viewer.getSnapshot().committedView!.consoleEntries.length, 0);
  let outputStep = 0,
    frameStep = 0,
    caller: string | null = null,
    disappearance = 0;
  for (let step = 1; step <= c.totalSteps; step++) {
    const item = c.selectStep({ step, requestId: step });
    if (!outputStep && item.consoleEntries.length) outputStep = step;
    if (!frameStep && item.callStack.length >= 3) {
      frameStep = step;
      caller = item.callStack[1]!.id;
    }
    if (
      frameStep &&
      step > frameStep &&
      !disappearance &&
      !item.callStack.some((f) => f.id === caller) &&
      item.callStack.length
    )
      disappearance = step;
  }
  assert(frameStep && outputStep && caller && disappearance);
  viewer.sliderCommit(outputStep);
  await flush();
  assert(viewer.getSnapshot().committedView!.consoleEntries.length > 0);
  viewer.previous();
  await flush();
  assert.equal(viewer.getSnapshot().committedView!.consoleEntries.length, 0);
  viewer.seek(frameStep);
  await flush();
  viewer.selectFrame(caller);
  await flush();
  v = viewer.getSnapshot().committedView!;
  assert.equal(v.selectedFrameId, caller);
  assert.deepEqual(v.selectedLocals.find((b) => b.name === "n")!.value, {
    kind: "scalar",
    type: "int",
    value: "2",
  });
  viewer.next();
  await flush();
  assert.equal(viewer.getSnapshot().selectedFrameId, caller);
  viewer.seek(disappearance);
  await flush();
  v = viewer.getSnapshot().committedView!;
  assert.equal(v.selectedFrameId, v.callStack.at(-1)!.id);
  assert.deepEqual({ starts, sockets, gets, releases, requests }, count);
  assert.deepEqual(count, {
    starts: 1,
    sockets: 1,
    gets: 1,
    releases: 1,
    requests: 3,
  });
  assert.equal(run.getSnapshot().editor.runSourceCode, code);
  assert.equal(run.getSnapshot().editor.mode, "run_source");
  run.edit();
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  assert.equal(run.getSnapshot().editor.draftCode, code);
  console.log(
    JSON.stringify({
      productionPath:
        "RUNS production → actual Docker Run → RECORDER → TRACE → VIEWER Viewer",
      steps: c.totalSteps,
      navigation: [
        "Step1",
        "Next",
        "Previous",
        "direct RUN_END",
        "slider",
        "Console rewind",
        "caller selection",
        "persistence",
        "fallback",
        "Edit",
      ],
      extraNetworkRequests: 0,
      ...count,
    }),
  );
} finally {
  unbind();
  viewer.dispose();
  run.dispose();
}
