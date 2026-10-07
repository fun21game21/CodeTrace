import assert from "node:assert/strict";
import { test } from "node:test";
import { EXAMPLES } from "../../src/examples/catalog.ts";
import { ExamplesController } from "../../src/examples/controller.ts";
import { validateConfig } from "../../src/visualizers/config.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import {
  setup,
  finish,
  settled,
  validTrace,
  health,
  RUN,
} from "../runs/helpers.ts";
import { IDBFactory } from "fake-indexeddb";
import {
  HistoryController,
  IndexedDbHistoryStore,
  bindRunHistory,
  openHistoryEntry,
} from "../../src/history/index.ts";

const ids = [
  "loop",
  "list-mutation",
  "sorting",
  "function",
  "recursive-factorial",
  "binary-tree-dfs",
  "graph-bfs",
];
const titles = [
  "Цикл",
  "Изменение списка",
  "Сортировка",
  "Функция",
  "Рекурсивный факториал",
  "DFS бинарного дерева",
  "BFS графа",
];
test("exact seven stable IDs, titles, ordering, no trace fields", () => {
  assert.equal(EXAMPLES.length, 7);
  assert.deepEqual(
    EXAMPLES.map((e) => e.id),
    ids,
  );
  assert.deepEqual(
    EXAMPLES.map((e) => e.title),
    titles,
  );
  assert.equal(new Set(ids).size, 7);
  for (const e of EXAMPLES)
    assert.deepEqual(Object.keys(e).sort(), [
      "id",
      "sourceCode",
      "title",
      "visualizerConfig",
    ]);
});
for (const example of EXAMPLES) {
  test(`${example.id}: nonempty source and valid deeply immutable definition`, () => {
    assert(example.sourceCode.trim());
    assert.match(example.title, /[А-Яа-я]/);
    assert(validateConfig(example.visualizerConfig));
    const check = (value: unknown) => {
      if (value && typeof value === "object") {
        assert(Object.isFrozen(value));
        Object.values(value).forEach(check);
      }
    };
    check(EXAMPLES);
    assert.throws(() => Object.assign(example, { sourceCode: "changed" }));
  });
}
test("presets use only exact approved globals and explicit markers", () => {
  assert.deepEqual(
    EXAMPLES.map((e) => e.visualizerConfig.kind),
    ["none", "array", "array", "call_stack", "call_stack", "tree", "graph"],
  );
  assert.deepEqual(EXAMPLES[2]!.visualizerConfig, {
    kind: "array",
    array: { scope: "globals", name: "numbers" },
    mode: "bars",
    indices: [
      { scope: "globals", name: "i" },
      { scope: "globals", name: "j" },
    ],
  });
  assert.deepEqual(EXAMPLES[5]!.visualizerConfig, {
    kind: "tree",
    root: { scope: "globals", name: "root" },
    valueField: "value",
    leftField: "left",
    rightField: "right",
    current: { scope: "globals", name: "current" },
    visited: { scope: "globals", name: "visited" },
  });
  assert.deepEqual(EXAMPLES[6]!.visualizerConfig, {
    kind: "graph",
    adjacency: { scope: "globals", name: "graph" },
    current: { scope: "globals", name: "current" },
    visited: { scope: "globals", name: "visited" },
    queue: { scope: "globals", name: "queue" },
  });
});
function harness() {
  const h = setup();
  const viewer = new ViewerController();
  const examples = new ExamplesController(h.controller, viewer);
  const unbind = bindRunViewer(h.controller, viewer),
    disconnect = examples.connect();
  h.setHttp(async (url, method) => ({
    status: method === "POST" ? 204 : 200,
    json: async () =>
      url === "/api/health"
        ? health()
        : validTrace(h.controller.getSnapshot().active.sourceCode!),
  }));
  return {
    ...h,
    viewer,
    examples,
    dispose: () => {
      disconnect();
      unbind();
      viewer.dispose();
      h.controller.dispose();
    },
  };
}
async function accepted(h: ReturnType<typeof harness>) {
  await h.controller.refreshHealth();
  assert(h.controller.run());
  const socket = h.sockets.at(-1)!;
  socket.ready();
  socket.accept();
  return socket;
}
for (const example of EXAMPLES) {
  test(`${example.id}: selection edits only; acceptance/READY applies copied preset`, async () => {
    const h = harness();
    try {
      assert(h.examples.select(example.id));
      assert.equal(
        h.controller.getSnapshot().editor.draftCode,
        example.sourceCode,
      );
      assert.equal(h.sockets.length, 0);
      assert.equal(h.calls.length, 0);
      assert.notEqual(h.examples.pendingConfig, example.visualizerConfig);
      assert.deepEqual(h.examples.pendingConfig, example.visualizerConfig);
      assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
      const socket = await accepted(h);
      assert.equal(h.examples.pendingConfig, null);
      finish(socket, example.sourceCode);
      await settled();
      assert.equal(h.controller.getSnapshot().trace.phase, "READY");
      assert.equal(h.viewer.getSnapshot().committedStep, 1);
      assert.deepEqual(
        h.viewer.getSnapshot().visualizerConfig,
        example.visualizerConfig,
      );
      assert.equal(h.viewer.getSnapshot().pendingStep, null);
      const requests = h.viewer.getSnapshot().latestRequestId;
      await h.controller.refreshHealth();
      await settled();
      assert.equal(
        h.viewer.getSnapshot().latestRequestId,
        requests,
        "preset applied once",
      );
    } finally {
      h.dispose();
    }
  });
}
for (const status of ["STARTING", "RUNNING", "WAITING_INPUT", "FINALIZING"]) {
  test(`selection is no-op during ${status}`, async () => {
    const h = harness();
    try {
      h.examples.select("sorting");
      const socket = await accepted(h);
      if (status !== "STARTING") socket.status("RUNNING");
      if (status === "WAITING_INPUT") socket.status("WAITING_INPUT");
      if (status === "FINALIZING") socket.status("FINALIZING");
      const before = h.controller.getSnapshot();
      assert.equal(h.examples.select("graph-bfs"), false);
      assert.equal(h.controller.getSnapshot(), before);
    } finally {
      h.dispose();
    }
  });
}
test("connecting and unknown ID cannot replace draft", async () => {
  const h = harness();
  try {
    assert.equal(h.examples.select("unknown"), false);
    h.examples.select("loop");
    await h.controller.refreshHealth();
    h.controller.run();
    assert.equal(h.examples.select("sorting"), false);
    assert.equal(
      h.controller.getSnapshot().editor.draftCode,
      EXAMPLES[0]!.sourceCode,
    );
  } finally {
    h.dispose();
  }
});
test("manual edit is the exact executed candidate; incompatible preset is retained without rewriting", async () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    const manual = 'print("changed")\n';
    h.controller.setDraft(manual);
    const socket = await accepted(h);
    assert.equal(JSON.parse(socket.sent[0]!).payload.code, manual);
    finish(socket, manual);
    await settled();
    assert.equal(h.controller.getSnapshot().editor.runSourceCode, manual);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "array");
  } finally {
    h.dispose();
  }
});
test("reselection restores original source/config; old READY view, play and config reset", async () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    const socket = await accepted(h);
    finish(socket);
    await settled();
    h.viewer.setVisualizerConfig({
      kind: "tree",
      root: { scope: "globals", name: "other" },
      valueField: "v",
      leftField: "l",
      rightField: "r",
    });
    await settled();
    const before = h.calls.length;
    assert(h.examples.select("sorting"));
    assert.equal(h.controller.getSnapshot().trace.session, null);
    assert.equal(h.controller.getSnapshot().trace.handle, null);
    assert.equal(h.viewer.getSnapshot().phase, "EMPTY");
    assert.equal(h.viewer.getSnapshot().playing, false);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
    assert.equal(
      h.controller.getSnapshot().editor.draftCode,
      EXAMPLES[2]!.sourceCode,
    );
    assert.deepEqual(h.examples.pendingConfig, EXAMPLES[2]!.visualizerConfig);
    assert.equal(h.calls.length, before);
    assert.equal(h.sockets.length, 1);
  } finally {
    h.dispose();
  }
});
test("plain loadDraft in editing clears preset even if source text is identical", () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    assert(h.examples.pendingConfig);
    h.controller.loadDraft(EXAMPLES[2]!.sourceCode);
    assert.equal(h.examples.pendingConfig, null);
  } finally {
    h.dispose();
  }
});
test("Edit clears accepted preset before late trace/config publication", async () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    const socket = await accepted(h);
    finish(socket);
    await settled();
    h.controller.edit();
    assert.equal(h.examples.pendingConfig, null);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
    const again = await accepted(h);
    finish(again);
    await settled();
    assert.equal(h.viewer.getSnapshot().runId, RUN);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
  } finally {
    h.dispose();
  }
});
test("rejection creates no trace and keeps pending preset for explicit retry", async () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    await h.controller.refreshHealth();
    h.controller.run();
    const socket = h.sockets[0]!;
    socket.ready();
    const { clientRequestId } = JSON.parse(socket.sent[0]!).payload;
    socket.emit(
      "run_rejected",
      { clientRequestId, code: "RUN_BUSY", message: "Busy" },
      null,
    );
    assert(h.examples.pendingConfig);
    assert.equal(h.controller.getSnapshot().editor.mode, "editing");
    assert.equal(h.viewer.getSnapshot().phase, "EMPTY");
  } finally {
    h.dispose();
  }
});
test("History writes only on acceptance; Open resets preset with no network, exact five fields", async () => {
  const h = harness();
  const history = new HistoryController(
    new IndexedDbHistoryStore(new IDBFactory()),
    () => !h.controller.canLoadDraft(),
  );
  const unbind = bindRunHistory(h.controller, history);
  try {
    await history.initialize();
    h.examples.select("sorting");
    await settled();
    assert.equal(history.getSnapshot().entries.length, 0);
    const socket = await accepted(h);
    finish(socket);
    await settled();
    assert.equal(history.getSnapshot().entries.length, 1);
    const entry = history.getSnapshot().entries[0]!;
    assert.deepEqual(Object.keys(entry).sort(), [
      "code",
      "createdAt",
      "id",
      "lastRunAt",
      "status",
    ]);
    h.examples.select("graph-bfs");
    const calls = h.calls.length;
    assert(openHistoryEntry(h.controller, history, entry.id));
    assert.equal(h.examples.pendingConfig, null);
    assert.equal(h.calls.length, calls);
    assert.equal(h.sockets.length, 1);
    assert.equal(
      h.controller.getSnapshot().editor.draftCode,
      EXAMPLES[2]!.sourceCode,
    );
  } finally {
    unbind();
    history.dispose();
    h.dispose();
  }
});
test("no-trace terminal cannot apply preset; Edit drops it", async () => {
  const h = harness();
  try {
    h.examples.select("sorting");
    const socket = await accepted(h);
    finish(socket, "", false);
    await settled();
    assert.equal(h.viewer.getSnapshot().phase, "EMPTY");
    h.controller.edit();
    assert.equal(h.examples.pendingConfig, null);
  } finally {
    h.dispose();
  }
});
