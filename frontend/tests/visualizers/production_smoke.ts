import assert from "node:assert/strict";
import { Worker } from "node:worker_threads";
import WebSocket from "ws";
import { RunController } from "../../src/runs/controller.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import type { Reply, WorkerPort } from "../../src/execution/protocol.ts";
import { adapt } from "../../src/visualizers/index.ts";
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
  const programs = [
    {
      name: "array",
      code: "a = [1, 2]\ni = 1\na[1] = 3\nprint(a)\n",
      config: {
        kind: "array",
        mode: "cells",
        array: { scope: "globals", name: "a" },
        indices: [{ scope: "globals", name: "i" }],
      },
    },
    {
      name: "tree",
      code: "class Node:\n    def __init__(self, value, left=None, right=None):\n        self.value = value\n        self.left = left\n        self.right = right\nroot = Node(2, Node(1), Node(3))\ncurrent = root.left\nvisited = [root.left]\nprint(root.value)\n",
      config: {
        kind: "tree",
        root: { scope: "globals", name: "root" },
        valueField: "value",
        leftField: "left",
        rightField: "right",
        current: { scope: "globals", name: "current" },
        visited: { scope: "globals", name: "visited" },
      },
    },
    {
      name: "graph",
      code: "g = {1: [2, 3], 2: [3], 3: [3]}\ncurrent = 2\nvisited = {1, 2}\nqueue = [3]\nstack = [1, 2]\nprint(g)\n",
      config: {
        kind: "graph",
        adjacency: { scope: "globals", name: "g" },
        current: { scope: "globals", name: "current" },
        visited: { scope: "globals", name: "visited" },
        queue: { scope: "globals", name: "queue" },
        stack: { scope: "globals", name: "stack" },
      },
    },
    {
      name: "call_stack",
      code: "def total(n):\n    if n == 0:\n        return 0\n    tail = total(n - 1)\n    return n + tail\nresult = total(2)\nprint(result)\n",
      config: { kind: "call_stack" },
    },
  ] as const;
  const results = [];
  const counts = () => ({
    starts,
    sockets,
    mainTraceGets,
    releases,
    workerLoads,
    workerCreates,
  });
  for (const program of programs) {
    run.setDraft(program.code);
    assert(run.run());
    await until(
      () =>
        run.getSnapshot().trace.phase === "READY" &&
        !run.getSnapshot().trace.releasePending &&
        viewer.getSnapshot().committedStep === 1,
    );
    const session = run.getSnapshot().trace.session!;
    assert.equal(session.sourceCode, program.code);
    assert.equal(run.getSnapshot().trace.handle, null);
    const before = counts();
    const final = await seek(session.totalSteps);
    assert.equal(final.completion?.outcome, "SUCCESS");
    assert(viewer.setVisualizerConfig(program.config));
    await until(
      () =>
        viewer.getSnapshot().pendingStep === null &&
        viewer.getSnapshot().visualizerConfig.kind === program.config.kind,
    );
    let model = adapt(
      viewer.getSnapshot().committedView!,
      viewer.getSnapshot().visualizerConfig,
    );
    assert(model.available && model.kind === program.name);
    if (model.kind === "array") {
      assert(program.config.kind === "array");
      assert.deepEqual(
        model.elements.map((e) => e.label),
        ["1", "3"],
      );
      let mutation = 0;
      for (let step = 1; step <= session.totalSteps; step++) {
        const v = await seek(step);
        if (v.event.source?.lineNumber === 4 && v.event.kind === "LINE") {
          mutation = step;
          assert.deepEqual(
            v.arrayPositionChanges.map((e) => e.indices),
            [[1]],
          );
          break;
        }
      }
      assert(mutation);
      const changed = adapt(
        viewer.getSnapshot().committedView!,
        program.config,
      );
      assert(
        changed.available &&
          changed.kind === "array" &&
          !changed.elements[0]!.changed &&
          changed.elements[1]!.changed,
      );
      await seek(1);
      await seek(mutation);
      assert.deepEqual(
        adapt(viewer.getSnapshot().committedView!, program.config),
        changed,
      );
      assert(viewer.setVisualizerConfig({ ...program.config, mode: "bars" }));
      await until(() => viewer.getSnapshot().pendingStep === null);
      model = adapt(
        viewer.getSnapshot().committedView!,
        viewer.getSnapshot().visualizerConfig,
      );
      assert(
        model.available &&
          model.kind === "array" &&
          model.effectiveMode === "bars",
      );
    } else if (model.kind === "tree") {
      assert.equal(model.nodes.length, 3);
      assert.equal(model.edges.length, 2);
      assert.equal(model.nodes.filter((n) => n.current && n.visited).length, 1);
    } else if (model.kind === "graph") {
      assert.equal(model.vertices.length, 3);
      assert.equal(model.edges.length, 4);
      assert.equal(model.edges.filter((e) => e.selfLoop).length, 1);
      assert.equal(model.queue.length, 1);
      assert.equal(model.stack.length, 2);
    } else {
      assert.equal(model.frames.length, 0);
      let returns = 0,
        deepest = 0;
      for (let step = 1; step <= session.totalSteps; step++) {
        const v = await seek(step);
        const m = adapt(v, program.config);
        assert(m.available && m.kind === "call_stack");
        deepest = Math.max(deepest, m.frames.length);
        if (m.frames.length === 4) {
          viewer.selectFrame("f2");
          await until(() => viewer.getSnapshot().pendingStep === null);
          const caller = adapt(
            viewer.getSnapshot().committedView!,
            program.config,
          );
          assert(
            caller.available &&
              caller.kind === "call_stack" &&
              caller.frames.find((f) => f.id === "f2")!.selected,
          );
        }
        if (v.event.kind === "RETURN") {
          returns++;
          assert(m.frames.some((f) => f.returning && f.returnLabel));
        }
      }
      assert.equal(deepest, 4);
      assert(returns >= 3);
    }
    viewer.next();
    await until(() => viewer.getSnapshot().pendingStep === null);
    await seek(2);
    viewer.previous();
    await until(() => viewer.getSnapshot().committedStep === 1);
    await seek(session.totalSteps - 1);
    viewer.setSpeed(20);
    viewer.play();
    await until(
      () => viewer.getSnapshot().committedStep === session.totalSteps,
    );
    assert.deepEqual(counts(), before);
    results.push({
      name: program.name,
      steps: session.totalSteps,
      counts: before,
      navigationNetwork: 0,
      navigationPythonExecutions: 0,
    });
    run.edit();
    assert.equal(viewer.getSnapshot().phase, "EMPTY");
    assert.equal(viewer.getSnapshot().visualizerConfig.kind, "none");
  }
  assert.deepEqual(counts(), {
    starts: 4,
    sockets: 4,
    mainTraceGets: 0,
    releases: 4,
    workerLoads: 4,
    workerCreates: 4,
  });
  console.log(
    JSON.stringify({
      productionPath:
        "real Docker → RECORDER → isolated Worker → VIEWER → VISUALIZERS",
      results,
    }),
  );
} finally {
  unbind();
  viewer.dispose();
  run.dispose();
  await Promise.all(workers.map((w) => w.terminate()));
}
