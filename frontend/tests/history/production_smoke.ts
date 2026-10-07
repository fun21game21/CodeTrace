import assert from "node:assert/strict";
import { Worker } from "node:worker_threads";
import WebSocket from "ws";
import { RunController } from "../../src/runs/controller.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import type { Reply, WorkerPort } from "../../src/execution/protocol.ts";
import { IDBFactory } from "fake-indexeddb";
import {
  IndexedDbHistoryStore,
  HistoryController,
  bindRunHistory,
  openHistoryEntry,
} from "../../src/history/index.ts";
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
  const worker = new Worker(
    new URL("../performance/node_worker.ts", import.meta.url),
  );
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
    if (Date.now() > end) throw Error("VISUALIZERS production deadline");
    await new Promise((r) => setTimeout(r, 10));
  }
}

const store = new IndexedDbHistoryStore(new IDBFactory());
const history = new HistoryController(store, () => !run.canLoadDraft());
const unbindHistory = bindRunHistory(run, history);
const counts = () => ({
  starts,
  sockets,
  mainTraceGets,
  releases,
  workerLoads,
  workerCreates,
});
try {
  await history.initialize();
  await run.refreshHealth();
  assert(run.getSnapshot().health?.available);
  const codeA = "a = [1, 2]\n",
    codeB = "a = [1, 2] \n",
    inputCode = 'n = input("Ответ: ")\nprint(n)\n';
  let original: import("../../src/history/types.ts").HistoryEntry | null = null;
  const results = [];
  for (const [index, code] of [codeA, codeA, codeB, inputCode].entries()) {
    run.setDraft(code);
    assert(run.run());
    if (code === inputCode) {
      await until(
        () =>
          run.getSnapshot().active.status === "WAITING_INPUT" &&
          history
            .getSnapshot()
            .entries.some(
              (e) => e.code === code && e.status === "WAITING_INPUT",
            ),
      );
      assert.equal(openHistoryEntry(run, history, original!.id), false);
      assert.equal(run.getSnapshot().active.sourceCode, code);
      run.setInput("  native-independent smoke  ");
      assert(run.submitInput());
    }
    await until(
      () =>
        run.getSnapshot().trace.phase === "READY" &&
        !run.getSnapshot().trace.releasePending &&
        viewer.getSnapshot().committedStep === 1 &&
        history
          .getSnapshot()
          .entries.some((e) => e.code === code && e.status === "SUCCEEDED"),
    );
    const entry = history.getSnapshot().entries.find((e) => e.code === code)!;
    assert.deepEqual(Object.keys(entry).sort(), [
      "code",
      "createdAt",
      "id",
      "lastRunAt",
      "status",
    ]);
    assert.equal(entry.code, code);
    if (index === 0) original = entry;
    if (index === 1) {
      assert.equal(entry.id, original!.id);
      assert.equal(entry.createdAt, original!.createdAt);
      assert.notEqual(entry.lastRunAt, original!.lastRunAt);
      assert.equal(history.getSnapshot().entries.length, 1);
    }
    if (index === 2) assert.equal(history.getSnapshot().entries.length, 2);
    const before = counts();
    viewer.setVisualizerConfig({ kind: "call_stack" });
    await until(() => viewer.getSnapshot().pendingStep === null);
    assert(openHistoryEntry(run, history, entry.id));
    assert.equal(run.getSnapshot().editor.draftCode, code);
    assert.equal(run.getSnapshot().editor.mode, "editing");
    assert.equal(viewer.getSnapshot().phase, "EMPTY");
    assert.equal(viewer.getSnapshot().visualizerConfig.kind, "none");
    assert.equal(run.getSnapshot().trace.session, null);
    assert.deepEqual(counts(), before);
    results.push({
      sourceVersion: index === 1 ? "rerun" : index,
      entryId: entry.id,
      status: entry.status,
      historyOpenNetwork: 0,
      historyOpenPython: 0,
      workerDisposed: true,
    });
  }
  assert.equal(history.getSnapshot().entries.length, 3);
  const before = counts();
  assert(await history.delete(original!.id));
  assert.equal(history.getSnapshot().entries.length, 2);
  assert.deepEqual(counts(), before);
  assert.deepEqual(counts(), {
    starts: 4,
    sockets: 4,
    mainTraceGets: 0,
    releases: 4,
    workerLoads: 4,
    workerCreates: 4,
  });
  const report = {
    productionPath:
      "real Docker → RECORDER → Worker → VIEWER/VISUALIZERS + HISTORY lifecycle (fake IDB here; separate native-browser proof)",
    results,
    counts: counts(),
    deleteNetwork: 0,
  };
  console.log(JSON.stringify(report));
} finally {
  unbindHistory();
  history.dispose();
  unbind();
  viewer.dispose();
  run.dispose();
  await Promise.all(workers.map((w) => w.terminate()));
}
