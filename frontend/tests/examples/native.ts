/** Explicit acceptance entry; excluded from normal production build. */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../../src/app/App.tsx";
import { RunController } from "../../src/runs/controller.ts";
import { browserDependencies } from "../../src/runs/browser.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { ExamplesController } from "../../src/examples/controller.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import {
  HistoryController,
  IndexedDbHistoryStore,
  bindRunHistory,
} from "../../src/history/index.ts";
import {
  examplesAcceptance,
  additionalAcceptance,
  check,
} from "./acceptance.ts";
import "../../src/app/styles.css";
const counters = {
  starts: 0,
  accepted: 0,
  traceGets: 0,
  mainTraceGets: 0,
  releases: 0,
  workersCreated: 0,
  workersDisposed: 0,
};
const beforeDatabases = await indexedDB.databases();
const evidence: Record<string, unknown> = {
  origin: location.origin,
  initialDatabases: beforeDatabases.map((d) => d.name),
  freshHistory: !beforeDatabases.some((d) => d.name === "codetrace-history"),
  initialLocalStorageKeys: localStorage.length,
  initialServiceWorkers: (await navigator.serviceWorker.getRegistrations())
    .length,
  examples: [],
  paths: [],
  status: "INITIALIZING",
};
const base = browserDependencies();
const loader = new WorkerTraceLoader(() => {
  counters.workersCreated++;
  const worker = new Worker(
    new URL("../../src/execution/trace.worker.ts", import.meta.url),
    { type: "module", name: "codetrace-trace" },
  );
  const post = worker.postMessage.bind(worker),
    terminate = worker.terminate.bind(worker);
  worker.postMessage = (message) => {
    if (message.type === "load") counters.traceGets++;
    post(message);
  };
  worker.terminate = () => {
    counters.workersDisposed++;
    terminate();
  };
  return worker;
});
const run = new RunController({
  ...base,
  traceLoader: loader,
  fetch(path, options) {
    if (path.endsWith("/trace")) counters.mainTraceGets++;
    if (path.endsWith("/release")) counters.releases++;
    return base.fetch(path, options);
  },
  socket() {
    const socket = base.socket();
    return {
      ...socket,
      send(raw) {
        if (JSON.parse(raw).type === "start") counters.starts++;
        socket.send(raw);
      },
    };
  },
});
const viewer = new ViewerController(),
  examples = new ExamplesController(run, viewer);
const store = new IndexedDbHistoryStore(),
  history = new HistoryController(store, () => !run.canLoadDraft());
const unbindHistory = bindRunHistory(run, history);
const acceptedIds = new Set<string>();
run.subscribeLifecycle((event) => {
  if (event.type === "accepted") {
    counters.accepted++;
    acceptedIds.add(event.run.sourceCode);
  }
});
const root = createRoot(document.getElementById("root")!);
root.render(createElement(App, { controller: run, viewer, history, examples }));
function show() {
  document.getElementById("native-evidence")!.textContent = JSON.stringify(
    {
      ...evidence,
      counts: counters,
      runStatus: run.getSnapshot().active.status,
      tracePhase: run.getSnapshot().trace.phase,
      historyCount: history.getSnapshot().entries.length,
    },
    null,
    2,
  );
}
const ids = ["examples-check", "paths-check", "cleanup-check"];
function disabled(value: boolean) {
  for (const id of ids)
    (document.getElementById(id) as HTMLButtonElement).disabled = value;
}
let safe = false;
await history.initialize();
try {
  check(history.getSnapshot().phase === "READY", "native IDB ready");
  check(
    history.getSnapshot().entries.length === 0,
    "refuse nonempty unowned History",
  );
  safe = true;
  evidence.status = "READY";
  disabled(false);
} catch (error) {
  evidence.status = "FAILED";
  evidence.error = String(error);
}
show();
const ports = {
  run,
  viewer,
  history,
  examples,
  counts: () => ({ ...counters }),
};
async function execute(action: () => Promise<void>) {
  if (!safe) return;
  disabled(true);
  evidence.status = "RUNNING";
  show();
  try {
    await action();
    evidence.status = "PASS";
  } catch (error) {
    evidence.status = "FAILED";
    evidence.error = String(error);
  } finally {
    show();
    disabled(false);
  }
}
document.getElementById("examples-check")!.onclick = () =>
  void execute(() =>
    examplesAcceptance(ports, (row) => {
      (evidence.examples as unknown[]).push(row);
      show();
    }),
  );
document.getElementById("paths-check")!.onclick = () =>
  void execute(() =>
    additionalAcceptance(ports, (row) => {
      (evidence.paths as unknown[]).push(row);
      show();
    }),
  );
document.getElementById("cleanup-check")!.onclick = () =>
  void execute(async () => {
    check(run.canLoadDraft(), "no active Run before cleanup");
    run.edit();
    for (const entry of history.getSnapshot().entries) {
      check(acceptedIds.has(entry.code), "delete owned source only");
      check(await history.delete(entry.id), "native deletion committed");
    }
    check((await store.list()).length === 0, "native owned records removed");
    evidence.historyCleanup = 0;
  });
document.getElementById("download-evidence")!.onclick = () => {
  const blob = new Blob(
      [document.getElementById("native-evidence")!.textContent!],
      { type: "application/json" },
    ),
    url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "codetrace-examples-native-evidence.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
window.addEventListener(
  "pagehide",
  () => {
    run.dispose();
    unbindHistory();
    history.dispose();
    viewer.dispose();
  },
  { once: true },
);
