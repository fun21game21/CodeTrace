/** Shared acceptance over actual production controllers; Node and native browser ports. */
import { EXAMPLES } from "../../src/examples/catalog.ts";
import type { ExamplesController } from "../../src/examples/controller.ts";
import type { RunController } from "../../src/runs/controller.ts";
import type { ViewerController } from "../../src/viewer/controller.ts";
import type { HistoryController } from "../../src/history/controller.ts";
import { openHistoryEntry } from "../../src/history/binding.ts";
import { adapt } from "../../src/visualizers/index.ts";
import type { SelectedStepView } from "../../src/trace/index.ts";
import type {
  VisualizerConfig,
  TreeViewModel,
  GraphViewModel,
} from "../../src/visualizers/types.ts";
export function check(value: unknown, message: string): asserts value {
  if (!value) throw Error(message);
}
export function equal(a: unknown, b: unknown, message: string) {
  check(
    JSON.stringify(a) === JSON.stringify(b),
    message + `: ${JSON.stringify(a)} != ${JSON.stringify(b)}`,
  );
}
export async function until(predicate: () => boolean) {
  const end = Date.now() + 45000;
  while (!predicate()) {
    check(Date.now() < end, "EXAMPLES acceptance deadline");
    await new Promise((r) => setTimeout(r, 10));
  }
}
export interface Ports {
  run: RunController;
  viewer: ViewerController;
  examples: ExamplesController;
  history: HistoryController;
  counts: () => Record<string, number>;
}
const text = (v: SelectedStepView) =>
  v.consoleEntries.map((e) => e.text).join("");
const scalar = (v: SelectedStepView, name: string) => {
  const value = v.globals.find((b) => b.name === name)?.value;
  return value && "kind" in value ? value.value : null;
};
const EXPECTED = [
  "Сумма: 15\n",
  "[9, 7, 2, 5]\n",
  "[1, 2, 4, 5, 8]\n",
  "24\n",
  "120\n",
  "Посещено: 5\n",
  "[1, 2, 3, 4]\n",
];
export async function select(viewer: ViewerController, step: number) {
  check(viewer.seek(step), "seek accepted");
  await until(
    () =>
      viewer.getSnapshot().committedStep === step &&
      viewer.getSnapshot().pendingStep === null,
  );
  return viewer.getSnapshot().committedView!;
}
export async function ready(p: Ports) {
  await until(
    () =>
      p.run.getSnapshot().trace.phase === "READY" &&
      !p.run.getSnapshot().trace.releasePending &&
      p.viewer.getSnapshot().committedStep === 1 &&
      p.viewer.getSnapshot().pendingStep === null &&
      p.history
        .getSnapshot()
        .entries.some(
          (e) =>
            e.code === p.run.getSnapshot().active.sourceCode &&
            e.status === p.run.getSnapshot().active.status,
        ),
  );
  check(
    p.run.getSnapshot().trace.handle === null &&
      p.run.getSnapshot().trace.session !== null,
    "single Worker trace owner",
  );
  check(
    p.run.getSnapshot().active.completion?.cleanupConfirmed,
    "container cleanup before trace",
  );
}
function semanticChecks(
  id: string,
  views: SelectedStepView[],
  config: VisualizerConfig,
) {
  const models = views.map((v) => adapt(v, config)),
    last = views.at(-1)!;
  for (const [i, v] of views.entries())
    equal(adapt(v, config), models[i], "pure deterministic adapter");
  if (id === "loop") {
    check(
      views.filter(
        (v) => v.event.kind === "LINE" && v.event.source?.lineNumber === 4,
      ).length === 5,
      "repeated loop LINE",
    );
    equal(
      [
        ...new Set(
          views.map((v) => scalar(v, "total")).filter((v) => v !== null),
        ),
      ],
      ["0", "1", "3", "6", "10", "15"],
      "observed totals",
    );
    check(
      models.every((m) => m.available && m.kind === "none"),
      "no fake loop visualizer",
    );
  }
  if (id === "list-mutation" || id === "sorting") {
    const arrays = models.filter((m) => m.available && m.kind === "array");
    const final = arrays.at(-1)!;
    check(final?.kind === "array", "final array");
    equal(
      final.elements.map((e) => e.label),
      id === "sorting" ? ["1", "2", "4", "5", "8"] : ["9", "7", "2", "5"],
      "final array values",
    );
    check(
      arrays.every(
        (m) => m.effectiveMode === (id === "sorting" ? "bars" : "cells"),
      ),
      "mode",
    );
    if (id === "list-mutation") {
      const mutation = arrays.find(
        (m) => m.elements.map((e) => e.label).join(",") === "4,9,7,2",
      )!;
      equal(
        mutation.elements.filter((e) => e.changed).map((e) => e.index),
        [1],
        "exact mutation index",
      );
      check(
        arrays.some((m) => m.elements.length === 5 && m.elements[4]!.changed),
        "append",
      );
    } else
      check(
        arrays.some(
          (m) => m.markerIndices.map((i) => i.name).join(",") === "i,j",
        ),
        "explicit i/j markers",
      );
  }
  if (id === "function" || id === "recursive-factorial") {
    check(
      views.some((v) => v.callStack.some((f) => f.kind === "module")),
      "module frame",
    );
    check(
      views.some(
        (v) =>
          v.event.kind === "CALL" && v.callStack.at(-1)?.kind === "function",
      ),
      "function CALL",
    );
    const returning = views.filter(
      (v) =>
        v.event.kind === "RETURN" && v.callStack.at(-1)?.kind === "function",
    );
    check(
      returning.length === (id === "function" ? 1 : 5),
      "RETURN outgoing frames",
    );
    equal(
      scalar(last, "result"),
      id === "function" ? "24" : "120",
      "function result",
    );
    equal(last.callStack, [], "RUN_END empty stack");
    if (id === "function")
      check(
        views.some((v) =>
          ["area", "height", "width"].every((name) =>
            v.callStack.at(-1)?.locals.some((b) => b.name === name),
          ),
        ),
        "function locals",
      );
    else {
      const deepest = views.reduce((a, b) =>
        a.callStack.length > b.callStack.length ? a : b,
      );
      check(deepest.callStack.length === 6, "recursion depth");
      check(
        new Set(deepest.callStack.map((f) => f.id)).size === 6,
        "unique frame IDs",
      );
    }
  }
  if (id === "binary-tree-dfs") {
    const trees = models.filter(
      (m): m is TreeViewModel =>
        m.available && m.kind === "tree" && m.nodes.length === 5,
    );
    check(trees.length > 0, "complete tree");
    const final = trees.at(-1)!;
    equal(final.edges.length, 4, "four tree edges");
    equal(final.visitedIds.length, 5, "all five visited");
    equal(
      [
        ...new Set(
          trees.flatMap((m) =>
            m.nodes.filter((n) => n.current).map((n) => n.label),
          ),
        ),
      ],
      ["1", "2", "4", "5", "3"],
      "DFS marker order",
    );
    const positions = trees[0]!.nodes.map(({ id, x, y }) => ({ id, x, y }));
    for (const m of trees)
      equal(
        m.nodes.map(({ id, x, y }) => ({ id, x, y })),
        positions,
        "stable tree topology layout",
      );
  }
  if (id === "graph-bfs") {
    const graphs = models.filter(
      (m): m is GraphViewModel =>
        m.available && m.kind === "graph" && m.vertices.length === 4,
    );
    const final = graphs.at(-1)!;
    check(final, "complete graph");
    equal(
      final.vertices.map((v) => v.key),
      [1, 2, 3, 4].map((n) => ({ type: "int", value: String(n) })),
      "typed vertices",
    );
    equal(final.edges.length, 4, "four directed edges");
    equal(
      [
        ...new Set(
          graphs.flatMap((m) =>
            m.vertices.filter((n) => n.current).map((n) => n.key.value),
          ),
        ),
      ],
      ["1", "2", "3", "4"],
      "BFS marker order",
    );
    check(
      graphs.some(
        (m) => m.queue.map((id) => JSON.parse(id)[1]).join(",") === "2,3",
      ),
      "recorded queue order",
    );
    check(
      final.vertices.every((v) => v.visited) && final.queue.length === 0,
      "visited and final queue",
    );
  }
}
export async function examplesAcceptance(
  p: Ports,
  report: (row: Record<string, unknown>) => void,
) {
  for (const [index, example] of EXAMPLES.entries()) {
    const before = p.counts(),
      historyBefore = p.history.getSnapshot().entries.length;
    check(p.examples.select(example.id), "select example");
    equal(p.counts(), before, "selection no transport/Worker");
    equal(
      p.history.getSnapshot().entries.length,
      historyBefore,
      "selection no History",
    );
    check(p.run.run(), "ordinary Run");
    await ready(p);
    equal(p.run.getSnapshot().active.completion!.outcome, "SUCCESS", "success");
    equal(
      p.viewer.getSnapshot().visualizerConfig,
      example.visualizerConfig,
      "applied preset",
    );
    const n = p.viewer.getSnapshot().totalSteps,
      nav = p.counts();
    const final = await select(p.viewer, n);
    equal(text(final), EXPECTED[index], "exact console");
    check(p.viewer.previous(), "Previous");
    await until(() => p.viewer.getSnapshot().pendingStep === null);
    check(p.viewer.next(), "Next");
    await until(() => p.viewer.getSnapshot().pendingStep === null);
    equal(
      adapt(p.viewer.getSnapshot().committedView!, example.visualizerConfig),
      adapt(final, example.visualizerConfig),
      "reverse/forward deterministic",
    );
    const views: SelectedStepView[] = [];
    for (let s = 1; s <= n; s++) views.push(await select(p.viewer, s));
    semanticChecks(example.id, views, example.visualizerConfig);
    equal(text(await select(p.viewer, 1)), "", "future Console hidden");
    if (example.id === "recursive-factorial") {
      const deepest = views.find((v) => v.callStack.length === 6)!;
      await select(p.viewer, deepest.step);
      const caller = deepest.callStack[1]!.id;
      check(p.viewer.selectFrame(caller), "caller select");
      await until(() => p.viewer.getSnapshot().pendingStep === null);
      equal(p.viewer.getSnapshot().selectedFrameId, caller, "recorded caller");
    }
    equal(p.counts(), nav, "navigation no network/Python/Worker creation");
    const entry = p.history
      .getSnapshot()
      .entries.find((e) => e.code === example.sourceCode)!;
    check(entry, "History exact source");
    equal(
      Object.keys(entry).sort(),
      ["code", "createdAt", "id", "lastRunAt", "status"],
      "only five fields",
    );
    const delta = Object.fromEntries(
      Object.entries(p.counts()).map(([k, v]) => [k, v - before[k]!]),
    );
    for (const key of [
      "starts",
      "accepted",
      "traceGets",
      "releases",
      "workersCreated",
    ])
      equal(delta[key], 1, key + " per example");
    equal(delta.mainTraceGets, 0, "no main trace GET");
    report({
      id: example.id,
      title: example.title,
      runId: p.run.getSnapshot().active.runId,
      status: "SUCCESS",
      eventCount: n,
      visualizer: example.visualizerConfig.kind,
      counts: delta,
      navigationNetwork: 0,
      historyExact: true,
    });
    check(openHistoryEntry(p.run, p.history, entry.id), "HistoryOpen");
    equal(p.viewer.getSnapshot().phase, "EMPTY", "Viewer detached");
    equal(p.examples.pendingConfig, null, "History resets preset");
    const afterOpen = p.counts();
    equal(afterOpen.starts, nav.starts, "Open no start");
    equal(afterOpen.traceGets, nav.traceGets, "Open no GET");
    equal(afterOpen.releases, nav.releases, "Open no release");
    equal(
      afterOpen.workersDisposed,
      afterOpen.workersCreated,
      "Worker disposed",
    );
  }
}
export async function additionalAcceptance(
  p: Ports,
  report: (row: Record<string, unknown>) => void,
) {
  const input =
    'first = input("Первое: ")\nsecond = input("Второе: ")\nprint(first, second)\n';
  p.run.loadDraft(input);
  check(p.run.run(), "two inputs Run");
  for (const [i, response] of ["one", "  два 🙂  "].entries()) {
    await until(
      () => p.run.getSnapshot().active.pendingInput?.requestId === `q${i + 1}`,
    );
    check(!p.examples.select("loop"), "Examples blocked during Input");
    p.run.setInput(response);
    check(p.run.submitInput(), "input response");
  }
  await ready(p);
  let final = await select(p.viewer, p.viewer.getSnapshot().totalSteps);
  equal(
    final.consoleEntries
      .filter((e) => e.kind === "input")
      .map((e) => [e.requestId, e.text]),
    [
      ["q1", "one\n"],
      ["q2", "  два 🙂  \n"],
    ],
    "exact input echoes",
  );
  equal(
    text(final),
    "Первое: one\nВторое:   два 🙂  \none   два 🙂  \n",
    "prompt/output ordering",
  );
  equal(
    p.history.getSnapshot().entries.find((e) => e.code === input)!.code,
    input,
    "responses not history",
  );
  report({
    case: "two-input",
    status: "PASS",
    runId: p.run.getSnapshot().active.runId,
    requests: 2,
  });
  p.run.edit();
  for (const [name, code, reason] of [
    ["syntax", "def broken(:\n", "SYNTAX_ERROR"],
    ["runtime", "x = 1 / 0\n", "UNCAUGHT_EXCEPTION"],
    ["active-timeout", "sum(range(10**12))\n", "ACTIVE_TIMEOUT"],
    ["console-limit", 'print("x" * 70000)\n', "CONSOLE_LIMIT"],
  ]) {
    p.run.loadDraft(code!);
    const before = p.counts();
    check(p.run.run(), "error/limit Run");
    if (name === "active-timeout") {
      // The tiny batch before a long C call has not been flushed: WATCHDOG/RECORDER
      // correctly publishes a no-trace result after the independent watchdog kill.
      await until(
        () =>
          p.run.getSnapshot().active.sourceCode === code &&
          p.run.getSnapshot().active.completion?.reason === "ACTIVE_TIMEOUT" &&
          p.run.getSnapshot().trace.loadError !== null,
      );
      const state = p.run.getSnapshot();
      equal(state.trace.phase, "EMPTY", "no fabricated trace");
      equal(p.viewer.getSnapshot().phase, "EMPTY", "no fabricated Viewer");
      check(state.active.completion!.cleanupConfirmed, "timeout cleanup");
      equal(state.editor.runSourceCode, code, "source remains available");
      check(p.run.canEdit(), "no-trace Edit usable");
      for (const key of [
        "traceGets",
        "mainTraceGets",
        "releases",
        "workersCreated",
      ])
        equal(p.counts()[key], before[key], "no-trace: no " + key);
    } else {
      await ready(p);
      final = await select(p.viewer, p.viewer.getSnapshot().totalSteps);
      equal(final.event.kind, "RUN_END", "last event");
      equal(final.completion!.reason, reason, "terminal reason");
      check(final.completion!.primaryDiagnostic, "diagnostic");
      if (final.totalSteps > 1) {
        const early = await select(p.viewer, 1);
        equal(early.completion, null, "no future terminal diagnostic");
      }
    }
    report({
      case: name,
      status: "PASS",
      runId: p.run.getSnapshot().active.runId,
      reason,
      traceAvailable: p.run.getSnapshot().trace.phase === "READY",
    });
    p.run.edit();
    p.run.loadDraft(EXAMPLES[0]!.sourceCode);
    check(p.run.run(), "next Run after failure");
    await ready(p);
    equal(
      p.run.getSnapshot().active.completion!.outcome,
      "SUCCESS",
      "next success",
    );
    p.run.edit();
  }
}
