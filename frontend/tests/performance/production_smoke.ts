import assert from "node:assert/strict";
import { Worker } from "node:worker_threads";
import WebSocket from "ws";
import { RunController } from "../../src/runs/controller.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import type { Reply, WorkerPort } from "../../src/execution/protocol.ts";
import type { SocketPort } from "../../src/runs/types.ts";
const origin = "http://127.0.0.1:8000";
let starts = 0,
  sockets = 0,
  mainTraceGets = 0,
  releases = 0,
  workerLoads = 0,
  workerCreates = 0;
const workers: Worker[] = [];
const loader = new WorkerTraceLoader((): WorkerPort => {
  const worker = new Worker(new URL("./node_worker.ts", import.meta.url));
  workers.push(worker);
  workerCreates++;
  return {
    postMessage(message) {
      if (message.type === "load") {
        workerLoads++;
        message = {
          ...message,
          request: {
            ...message.request,
            traceUrl: origin + message.request.traceUrl,
          },
        };
      }
      worker.postMessage(message);
    },
    addEventListener(
      type: string,
      handler: ((e: { data: Reply }) => void) | (() => void),
    ) {
      if (type === "message")
        worker.on(type, (data) =>
          (handler as (e: { data: Reply }) => void)({ data }),
        );
      else worker.on(type, handler as () => void);
    },
    terminate() {
      void worker.terminate();
    },
  };
});
const run = new RunController({
  traceLoader: loader,
  uuid: () => crypto.randomUUID(),
  fetch: async (path, options) => {
    if (path.endsWith("/trace")) mainTraceGets++;
    if (options) releases++;
    return fetch(origin + path, {
      ...options,
      headers: options ? { Origin: origin } : {},
      signal: AbortSignal.timeout(20000),
    });
  },
  socket(): SocketPort {
    sockets++;
    const socket = new WebSocket("ws://127.0.0.1:8000/api/runs", {
      origin,
      perMessageDeflate: false,
    });
    return {
      onMessage: (h) => socket.on("message", (raw) => h(raw.toString())),
      onClose: (h) => socket.on("close", h),
      onError: (h) => socket.on("error", h),
      send: (raw) => {
        if (JSON.parse(raw).type === "start") starts++;
        socket.send(raw);
      },
      close: () => socket.close(),
    };
  },
});
const viewer = new ViewerController(),
  unbind = bindRunViewer(run, viewer);
async function until(predicate: () => boolean) {
  const end = Date.now() + 40000;
  while (!predicate()) {
    assert.equal(run.getSnapshot().transportError, null);
    assert.notEqual(run.getSnapshot().trace.phase, "LOAD_FAILED");
    if (Date.now() > end) throw Error("PERFORMANCE production deadline");
    await new Promise((r) => setTimeout(r, 10));
  }
}
async function seek(step: number) {
  viewer.seek(step);
  await until(
    () =>
      viewer.getSnapshot().pendingStep === null &&
      viewer.getSnapshot().committedStep === step,
  );
  return viewer.getSnapshot().committedView!;
}
try {
  await run.refreshHealth();
  assert(run.getSnapshot().health?.available);
  const source =
    'print("start")\ndef total(n):\n    print("level", n)\n    if n == 0:\n        return 0\n    tail = total(n - 1)\n    return n + tail\nresult = total(2)\nprint("done", result)\n';
  run.setDraft(source);
  assert(run.run());
  await until(
    () =>
      run.getSnapshot().trace.phase === "READY" &&
      !run.getSnapshot().trace.releasePending &&
      viewer.getSnapshot().committedStep === 1,
  );
  const state = run.getSnapshot();
  assert.equal(state.trace.handle, null);
  const session = state.trace.session!;
  assert(session);
  assert.equal(session.sourceCode, source);
  const counts = () => ({
      starts,
      sockets,
      mainTraceGets,
      releases,
      workerLoads,
      workerCreates,
    }),
    before = counts();
  assert.deepEqual(before, {
    starts: 1,
    sockets: 1,
    mainTraceGets: 0,
    releases: 1,
    workerLoads: 1,
    workerCreates: 1,
  });
  viewer.next();
  await until(() => viewer.getSnapshot().committedStep === 2);
  viewer.previous();
  await until(() => viewer.getSnapshot().committedStep === 1);
  viewer.setStepDraft(String(session.totalSteps));
  viewer.commitStepDraft();
  await until(() => viewer.getSnapshot().committedStep === session.totalSteps);
  const end = viewer.getSnapshot().committedView!;
  assert.equal(end.completion!.outcome, "SUCCESS");
  assert.deepEqual(end.globals.find((b) => b.name === "result")!.value, {
    kind: "scalar",
    type: "int",
    value: "3",
  });
  viewer.sliderCommit(2);
  await until(() => viewer.getSnapshot().committedStep === 2);
  assert.equal(viewer.getSnapshot().committedView!.consoleEntries.length, 0);
  await seek(9);
  viewer.selectFrame("f2");
  await until(() => viewer.getSnapshot().selectedFrameId === "f2");
  assert.equal((await seek(10)).selectedFrameId, "f2");
  assert.equal((await seek(22)).selectedFrameId, "f1");
  await seek(session.totalSteps - 1);
  viewer.setSpeed(20);
  viewer.play();
  await until(() => viewer.getSnapshot().committedStep === session.totalSteps);
  assert(!viewer.getSnapshot().playing);
  assert.deepEqual(counts(), before);
  run.edit();
  assert.equal(run.getSnapshot().trace.session, null);
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  console.log(
    JSON.stringify({
      productionPath:
        "real Docker → RECORDER → isolated Worker fetch/parse/TRACE → VIEWER",
      steps: session.totalSteps,
      ...before,
      navigationNetwork: 0,
      navigationPythonExecutions: 0,
    }),
  );
} finally {
  unbind();
  viewer.dispose();
  run.dispose();
  await Promise.all(workers.map((w) => w.terminate()));
}
