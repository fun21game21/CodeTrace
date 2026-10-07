import { writeFileSync } from "node:fs";
import { ExamplesController } from "../../src/examples/controller.ts";
import { examplesAcceptance, additionalAcceptance } from "./acceptance.ts";
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
} from "../../src/history/index.ts";
import type { SocketPort } from "../../src/runs/types.ts";
const origin = "http://127.0.0.1:8000";
let starts = 0,
  sockets = 0,
  mainTraceGets = 0,
  releases = 0,
  workerLoads = 0,
  workerCreates = 0,
  workersDisposed = 0,
  acceptedCount = 0;
const workers: Worker[] = [];
const acceptedRunIds: string[] = [];
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
      workersDisposed++;
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
const store = new IndexedDbHistoryStore(new IDBFactory());
const history = new HistoryController(store, () => !run.canLoadDraft());
const unbindHistory = bindRunHistory(run, history);
run.subscribeLifecycle((e) => {
  if (e.type === "accepted") {
    acceptedCount++;
    acceptedRunIds.push(e.run.runId);
  }
});
const counts = () => ({
  accepted: acceptedCount,
  workersDisposed,
  workersCreated: workerCreates,
  traceGets: workerLoads,
  starts,
  sockets,
  mainTraceGets,
  releases,
  workerLoads,
  workerCreates,
});
const examples = new ExamplesController(run, viewer);
const disconnectExamples = examples.connect();
const results: Record<string, unknown>[] = [];
try {
  await history.initialize();
  await run.refreshHealth();
  assert(run.getSnapshot().health?.available);
  const ports = { run, viewer, history, examples, counts };
  await examplesAcceptance(ports, (row) => results.push(row));
  await additionalAcceptance(ports, (row) => results.push(row));
  for (const entry of history.getSnapshot().entries)
    assert(await history.delete(entry.id));
  const report = {
    productionPath:
      "actual FastAPI/Docker/Worker/Viewer; fake IDB Node port; native browser acceptance is a separate required gate",
    results,
    acceptedRunIds,
    counts: counts(),
    historyRemaining: history.getSnapshot().entries.length,
  };
  writeFileSync(
    process.env.CODETRACE_EXAMPLES_REPORT ??
      new URL(
        "../../benchmarks/results/examples-production.json",
        import.meta.url,
      ),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  disconnectExamples();
  unbindHistory();
  history.dispose();
  unbind();
  viewer.dispose();
  run.dispose();
  await Promise.all(workers.map((w) => w.terminate()));
}
