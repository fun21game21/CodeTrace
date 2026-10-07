/** Explicit, locally hosted test entry. Not reachable from production main.tsx. */
import { useLayoutEffect, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { browserTraceLoader } from "../src/execution/browser.ts";
import { WorkerSession } from "../src/execution/worker_session.ts";
import { App } from "../src/app/App.tsx";
import { RunController } from "../src/runs/controller.ts";
import type { SocketPort, RunDependencies } from "../src/runs/types.ts";
import { TraceCore } from "../src/trace/index.ts";
import { ViewerController } from "../src/viewer/controller.ts";
import { DirectTraceSelectionExecutor } from "../src/viewer/executor.ts";
import type { SelectedStepView } from "../src/trace/index.ts";
import {
  decision,
  sequences,
  stats,
  SEED,
} from "../tests/performance/metrics.ts";
import type {
  Completion,
  ResourceLimits,
} from "../../contracts/typescript/index.ts";
import "../src/app/styles.css";
interface Manifest {
  name: string;
  runId: string;
  sourceCode: string;
  eventCount: number;
  stateRecordCount: number;
  bytes: number;
  sha256: string;
  checkpointInterval: number;
  checkpointSteps: number[];
  maxStateBytes: number;
  maxObjects: number;
  maxFrames: number;
  limits: ResourceLimits;
  completion: Completion;
}
interface Sample {
  workerSelectionMs?: number;
  freezeMs?: number;
  step: number;
  frame?: string | null;
  commandAt: number;
  selectionStart: number;
  selectionEnd: number;
  publicationAt: number;
  commitAt: number;
  renderAt: number;
  selectionMs: number;
  publicationMs: number;
  commitMs: number;
  firstRenderMs: number;
  endToEndMs: number;
}
const mode =
  new URLSearchParams(location.search).get("mode") === "worker"
    ? "worker"
    : "direct";
const status = document.getElementById("benchmark-status")!;
const raf = () =>
  new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
const task = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
let active: Partial<Sample> | null = null,
  rendered: ((view: SelectedStepView) => void) | null = null;
const supported = PerformanceObserver.supportedEntryTypes.includes("longtask");
const tasks: { startTime: number; duration: number; name: string }[] = [];
const observer = supported
  ? new PerformanceObserver((list) =>
      tasks.push(
        ...list.getEntries().map((e) => ({
          startTime: e.startTime,
          duration: e.duration,
          name: e.name,
        })),
      ),
    )
  : null;
observer?.observe({ type: "longtask" });
function Probe({ viewer }: { viewer: ViewerController }) {
  const s = useSyncExternalStore(viewer.subscribe, viewer.getSnapshot);
  useLayoutEffect(() => {
    if (!s.committedView) return;
    const view = s.committedView;
    if (active) active.commitAt = performance.now();
    requestAnimationFrame(() => {
      if (active) active.renderAt = performance.now();
      rendered?.(view);
    });
  }, [s.committedView]);
  return null;
}
const heap = () =>
  (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory
    ?.usedJSHeapSize ?? null;
const root = createRoot(document.getElementById("root")!);
const loadOriginal = TraceCore.load;
let loadMetrics = { fetchMs: 0, jsonParseMs: 0, traceCoreLoadMs: 0 };
// Test-only wrappers measure the unchanged implementation, with no input retained.
TraceCore.load = function (input: unknown) {
  const start = performance.now();
  try {
    return loadOriginal(input);
  } finally {
    loadMetrics.traceCoreLoadMs += performance.now() - start;
  }
};
const windowTasks = (start: number, end: number) =>
  tasks.filter(
    (t) =>
      t.startTime < end && t.startTime + t.duration > start && t.duration > 50,
  );
async function runScenario(item: Manifest) {
  status.textContent =
    "PERFORMANCE " + mode + " · " + item.name + " · preparing Monaco";
  let message: (data: unknown) => void = () => {},
    seq = 1,
    counts = { starts: 0, gets: 0, releases: 0 },
    readyAt = 0,
    loadStart = 0;
  const emit = (
    type: string,
    payload: unknown,
    runId: string | null = item.runId,
  ) =>
    message(
      JSON.stringify({ apiVersion: 1, seq: seq++, type, runId, payload }),
    );
  const socket: SocketPort = {
    onMessage: (h) => {
      message = h;
      setTimeout(
        () =>
          emit("socket_ready", { apiVersion: 1, limits: item.limits }, null),
        0,
      );
    },
    onClose: () => {},
    onError: () => {},
    close: () => {},
    send: (raw) => {
      const request = JSON.parse(raw) as {
        payload: { clientRequestId: string };
      };
      counts.starts++;
      setTimeout(() => {
        emit("run_accepted", {
          clientRequestId: request.payload.clientRequestId,
          acceptedAt: item.completion.acceptedAt,
          status: "STARTING",
        });
        for (const state of ["STARTING", "RUNNING", "FINALIZING", "SUCCEEDED"])
          emit("status", { status: state });
        loadStart = performance.now();
        emit("run_finished", {
          completion: item.completion,
          traceAvailable: true,
          traceUrl: `/api/runs/${item.runId}/trace`,
          traceBytes: item.bytes,
          eventCount: item.eventCount,
        });
      }, 0);
    },
  };
  const dependencies: RunDependencies = {
    uuid: () => crypto.randomUUID(),
    socket: () => socket,
    fetch: async (url, options) => {
      if (url === "/api/health")
        return {
          status: 200,
          json: async () => ({
            apiVersion: 1,
            available: true,
            reason: null,
            pythonVersion: "3.13.16",
            limits: item.limits,
          }),
        };
      if (options) {
        counts.releases++;
        const response = await fetch(url, { method: "POST" });
        return response;
      }
      counts.gets++;
      const start = performance.now();
      const response = await fetch(url, { cache: "no-store" });
      return {
        status: response.status,
        json: async () => {
          const raw = await response.text();
          loadMetrics.fetchMs = performance.now() - start;
          const parse = performance.now();
          const parsed: unknown = JSON.parse(raw);
          loadMetrics.jsonParseMs = performance.now() - parse;
          return parsed;
        },
      };
    },
  };
  if (mode === "worker") {
    const loader = browserTraceLoader(true);
    dependencies.traceLoader = {
      dispose: () => loader.dispose(),
      load: async (request) => {
        const session = await loader.load(request);
        if (!(session instanceof WorkerSession))
          throw Error("Expected real WorkerSession");
        loadMetrics = { ...session.loadTiming! };
        const select = session.executor.select;
        session.executor.select = async (request) => {
          if (active) active.selectionStart = performance.now();
          try {
            return await select(request);
          } finally {
            if (active) {
              active.selectionEnd = performance.now();
              active.workerSelectionMs = session.lastSelectionMs;
              active.freezeMs = session.lastFreezeMs;
            }
          }
        };
        return session;
      },
    };
  }
  const run = new RunController(dependencies, item.sourceCode);
  const viewer = new ViewerController({
    executor: (core) => {
      const executor = new DirectTraceSelectionExecutor(core);
      return {
        select: async (request) => {
          if (active) active.selectionStart = performance.now();
          try {
            return await executor.select(request);
          } finally {
            if (active) active.selectionEnd = performance.now();
          }
        },
      };
    },
  });
  let first: ((v: SelectedStepView) => void) | null = null;
  viewer.subscribe(() => {
    const s = viewer.getSnapshot();
    if (s.committedView && s.pendingStep === null) {
      if (active) active.publicationAt = performance.now();
      first?.(s.committedView);
    }
  });
  run.subscribe(() => {
    if (run.getSnapshot().trace.phase === "READY" && !readyAt)
      readyAt = performance.now();
  });
  root.render(
    <>
      <App controller={run} viewer={viewer} />
      <Probe viewer={viewer} />
    </>,
  );
  // Monaco startup is explicitly outside every measured trace window.
  await new Promise((resolve) => setTimeout(resolve, 1500));
  await raf();
  await raf();
  const heapBefore = heap();
  loadMetrics = { fetchMs: 0, jsonParseMs: 0, traceCoreLoadMs: 0 };
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(Error("Trace load timeout")),
      90000,
    );
    first = () => {
      clearTimeout(timeout);
      first = null;
      resolve();
    };
    if (!run.run()) {
      clearTimeout(timeout);
      reject(Error("Run unavailable"));
    }
  });
  await raf();
  await task();
  const loadEnd = performance.now(),
    heapAfter = heap();
  const load = {
    ...loadMetrics,
    traceReadyMs: readyAt - loadStart,
    initialViewMs: loadEnd - loadStart,
    longTasks: windowTasks(loadStart, loadEnd),
    windowStart: loadStart,
    windowEnd: loadEnd,
    heapBefore,
    heapAfter,
  };
  const seqs = sequences(item.eventCount, item.checkpointSteps);
  async function select(step: number, frame?: string | null): Promise<Sample> {
    await task();
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(Error("Selection timeout")),
        15000,
      );
      active = {
        step,
        ...(frame === undefined ? {} : { frame }),
        commandAt: performance.now(),
      };
      rendered = (view) => {
        if (view.step !== step) return;
        clearTimeout(timeout);
        rendered = null;
        const s = active as Sample;
        active = null;
        if (
          [
            s.commandAt,
            s.selectionStart,
            s.selectionEnd,
            s.publicationAt,
            s.commitAt,
            s.renderAt,
          ].some((n) => !Number.isFinite(n))
        )
          return reject(Error("Missing measurement stage"));
        resolve({
          ...s,
          selectionMs: s.selectionEnd - s.selectionStart,
          publicationMs: s.publicationAt - s.selectionEnd,
          commitMs: s.commitAt - s.publicationAt,
          firstRenderMs: s.renderAt - s.commitAt,
          endToEndMs: s.renderAt - s.commandAt,
        });
      };
      const accepted =
        frame === undefined ? viewer.seek(step) : viewer.selectFrame(frame);
      if (!accepted) {
        clearTimeout(timeout);
        rendered = null;
        active = null;
        reject(Error("Command rejected"));
      }
    });
  }
  for (const step of seqs.warmup) await select(step);
  const categories: Record<string, unknown> = {},
    p95s: number[] = [],
    syncTimes: number[] = [];
  for (const [category, targets] of Object.entries(seqs)) {
    if (category === "warmup") continue;
    status.textContent =
      "PERFORMANCE " + mode + " · " + item.name + " · " + category;
    const start = performance.now(),
      samples: Sample[] = [];
    for (const step of targets) samples.push(await select(step));
    await task();
    const end = performance.now(),
      longTasks = windowTasks(start, end);
    const summary = stats(samples.map((s) => s.endToEndMs));
    p95s.push(summary.p95);
    syncTimes.push(
      ...samples.map((s) =>
        mode === "worker" ? (s.freezeMs ?? 0) : s.selectionMs,
      ),
    );
    categories[category] = {
      summary,
      selection: stats(samples.map((s) => s.selectionMs)),
      publication: stats(samples.map((s) => s.publicationMs)),
      commit: stats(samples.map((s) => s.commitMs)),
      firstRender: stats(samples.map((s) => s.firstRenderMs)),
      longTasks,
      samples,
    };
  }
  if (item.name === "deep-frames") {
    const step = Math.floor(item.eventCount / 2);
    await select(step);
    const samples: Sample[] = [],
      start = performance.now();
    for (let i = 0; i < 100; i++)
      samples.push(await select(step, "f" + (1 + (i % 128))));
    await task();
    const summary = stats(samples.map((s) => s.endToEndMs));
    p95s.push(summary.p95);
    syncTimes.push(
      ...samples.map((s) =>
        mode === "worker" ? (s.freezeMs ?? 0) : s.selectionMs,
      ),
    );
    categories.frame = {
      summary,
      selection: stats(samples.map((s) => s.selectionMs)),
      longTasks: windowTasks(start, performance.now()),
      samples,
    };
  }
  const allLongTasks = windowTasks(loadStart, performance.now());
  const resourceGets = performance
    .getEntriesByType("resource")
    .filter((e) => e.name.endsWith(`/api/runs/${item.runId}/trace`)).length;
  const serverNetwork = (await (await fetch("/network.json")).json()) as Record<
    string,
    { gets: number; releases: number }
  >;
  const result = {
    serverNetwork: serverNetwork[item.runId],
    ...item,
    resourceGets,
    mainTraceHandlePresent: run.getSnapshot().trace.handle !== null,
    workerSessionPresent: run.getSnapshot().trace.session !== null,
    limits: undefined,
    completion: undefined,
    load,
    categories,
    warmupCount: 20,
    counts,
    longTaskMax: Math.max(0, ...allLongTasks.map((t) => t.duration)),
    syncFallbackMax: supported
      ? null
      : Math.max(
          mode === "direct" ? load.jsonParseMs : 0,
          mode === "direct" ? load.traceCoreLoadMs : 0,
          ...syncTimes,
        ),
    seekP95: Math.max(...p95s),
    heapAfterNavigation: heap(),
  };
  run.edit();
  viewer.dispose();
  run.dispose();
  root.render(null);
  await raf();
  return result;
}
async function main() {
  const manifest = (await (await fetch("/manifest.json")).json()) as Manifest[];
  const environment = (await (
    await fetch("/environment.json")
  ).json()) as Record<string, unknown>;
  const results = [];
  for (const item of manifest) {
    const result = await runScenario(item);
    results.push(result);
    await fetch("/progress", { method: "POST", body: JSON.stringify(result) });
  }
  const report = {
    version: 1,
    mode,
    date: new Date().toISOString(),
    environment: {
      ...environment,
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
      production: true,
      longTasksApi: supported,
      deviceMemory:
        (navigator as Navigator & { deviceMemory?: number }).deviceMemory ??
        null,
    },
    seed: SEED,
    results,
    decision: decision(
      results.map((r) => ({
        name: r.name,
        longTaskMax: r.syncFallbackMax ?? r.longTaskMax,
        seekP95: r.seekP95,
      })),
    ),
    method:
      "nearest-rank p95; confirmed Viewer command → publication → React layout effect → first rAF; no forced GC; synthetic lifecycle, real HTTP/parse/TRACE/VIEWER/Monaco",
  };
  observer?.disconnect();
  TraceCore.load = loadOriginal;
  await fetch("/report", { method: "POST", body: JSON.stringify(report) });
  status.textContent =
    "PERFORMANCE " +
    mode.toUpperCase() +
    " COMPLETE · workerRequired=" +
    report.decision.workerRequired;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(
    {
      decision: report.decision,
      scenarios: results.map((r) => ({
        name: r.name,
        load: r.load.traceReadyMs,
        longTaskMax: r.longTaskMax,
        p95: r.seekP95,
      })),
    },
    null,
    2,
  );
  document.body.append(pre);
}
void main().catch(async (error) => {
  status.textContent = "PERFORMANCE FAILED: " + String(error);
  await fetch("/failure", {
    method: "POST",
    body: JSON.stringify({ error: String(error) }),
  });
});
