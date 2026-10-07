import assert from "node:assert/strict";
import test from "node:test";
import { validateConfig } from "../../src/visualizers/config.ts";
import { adapt } from "../../src/visualizers/index.ts";
import type {
  VisualizerConfig,
  ArrayViewModel,
  TreeViewModel,
  GraphViewModel,
} from "../../src/visualizers/types.ts";
import { lookup, validSelector } from "../../src/visualizers/bindings.ts";
import { formatValue } from "../../src/visualizers/value_format.ts";
import { TraceCore } from "../../src/trace/index.ts";
import { fixture } from "../trace/helpers.ts";
import type {
  Value,
  ObjectRecord,
} from "../../../contracts/typescript/index.ts";
import {
  int,
  float,
  bool,
  str,
  none,
  ref,
  list,
  binding,
  view,
  array,
  tree,
  treeConfig,
  graph,
  graphConfig,
  selector,
} from "./helpers.ts";
const arrayConfig: Extract<VisualizerConfig, { kind: "array" }> = {
  kind: "array",
  mode: "cells",
  indices: [],
  array: selector("a"),
};
function arrayModel(
  items: Value[],
  mode: "cells" | "bars" = "cells",
): ArrayViewModel {
  const r = adapt(array(items), { ...arrayConfig, mode });
  assert(r.available && r.kind === "array");
  return r;
}
for (const [name, items, mode, effective] of [
  ["empty", [], "cells", "cells"],
  ["plain ints", [int(1), int(2)], "cells", "cells"],
  ["mixed", [int(1), str("x"), bool(true), none], "cells", "cells"],
  ["positive bars", [int(1), int(2)], "bars", "bars"],
  ["negative bars", [int(-1), int(-2)], "bars", "bars"],
  ["mixed signs", [int(-2), int(3)], "bars", "bars"],
  ["zeros", [int(0), int(0)], "bars", "bars"],
  ["equal", [int(2), int(2)], "bars", "bars"],
  ["huge int", [int("999999999999999999999")], "bars", "cells"],
  ["nan", [float("nan")], "bars", "cells"],
  ["inf", [float("inf")], "bars", "cells"],
  ["-inf", [float("-inf")], "bars", "cells"],
  ["negative zero", [float("-0.0")], "bars", "bars"],
  ["bool bars", [bool(true)], "bars", "cells"],
  ["string bars", [str("1")], "bars", "cells"],
  [
    "max float signs",
    [float("-1.7976931348623157e+308"), float("1.7976931348623157e+308")],
    "bars",
    "bars",
  ],
] as [string, Value[], "cells" | "bars", "cells" | "bars"][])
  test("Array " + name, () => {
    const r = arrayModel(items, mode);
    assert.equal(r.effectiveMode, effective);
    assert(r.scale.max > r.scale.min);
    assert.equal(r.elements.length, items.length);
    assert(r.elements.every((e) => e.changed));
  });
test("Array labels preserve encoded scalar types/text", () => {
  const r = arrayModel([
    none,
    bool(true),
    bool(false),
    int("123456789012345678901234567890"),
    float("-0.0"),
    str('<script>\n"x"'),
  ]);
  assert.deepEqual(
    r.elements.map((e) => e.label),
    [
      "None",
      "True",
      "False",
      "123456789012345678901234567890",
      "-0.0",
      JSON.stringify('<script>\n"x"'),
    ],
  );
});
for (const kind of [
  "tuple",
  "deque",
  "set",
  "dict",
  "instance",
  "opaque",
] as const)
  test("Array rejects explicit " + kind, () => {
    const o: ObjectRecord =
      kind === "dict"
        ? { id: "o1", kind, entries: [] }
        : kind === "instance"
          ? { id: "o1", kind, className: "A", attrs: [] }
          : kind === "opaque"
            ? { id: "o1", kind, pythonType: "A", reason: "unsupported_type" }
            : kind === "deque"
              ? { id: "o1", kind, items: [int(1)], maxlen: null }
              : { id: "o1", kind, items: [int(1)] };
    const r = adapt(view([o], [binding("a", ref("o1"))]), arrayConfig);
    assert(!r.available && r.code === "WRONG_TYPE");
  });
for (const [name, items] of [
  ["bool", [bool(true)]],
  ["mixed", [int(1), str("x")]],
  ["nested", [ref("o2")]],
] as [string, Value[]][])
  test("Auto ignores " + name, () => {
    const r = adapt(array(items, [list("o2", [int(1)])]), {
      kind: "array",
      mode: "cells",
      indices: [],
    });
    assert(!r.available);
  });
test("Auto ignores tuple, accepts empty, chooses first code-point binding", () => {
  const r = adapt(
    view(
      [
        list("o1", [int(1)]),
        list("o2", []),
        { id: "o3", kind: "tuple", items: [int(1)] },
      ],
      [
        binding("z", ref("o1")),
        binding("a", ref("o2")),
        binding("A", ref("o3")),
      ],
    ),
    { kind: "array", mode: "cells", indices: [] },
  );
  assert(r.available && r.kind === "array");
  assert.equal(r.binding, "a");
  assert.equal(r.objectId, "o2");
});
test("manual nested cells preserves Ref/alias identity", () => {
  const r = adapt(
    array([ref("o2"), ref("o2")], [list("o2", [int(1)])]),
    arrayConfig,
  );
  assert(r.available && r.kind === "array");
  assert(r.elements.every((e) => e.label.includes("o2")));
  assert.deepEqual(r.elements[0]!.value, r.elements[1]!.value);
});
test("Array uses exact TRACE position metadata on random seek", () => {
  const core = TraceCore.load(JSON.parse(requireFixture("scalar")).trace);
  core.selectStep({ step: 6, requestId: 1 });
  const r = adapt(core.selectStep({ step: 4, requestId: 2 }), arrayConfig);
  assert(r.available && r.kind === "array");
  assert.deepEqual(
    r.elements.map((e) => e.changed),
    [false, true],
  );
});
import { readFileSync } from "node:fs";
const requireFixture = (name: string) =>
  readFileSync(
    new URL(`../visualizer_fixtures/fixtures/${name}.json`, import.meta.url),
    "utf8",
  );
for (const [name, value, valid, outside] of [
  ["valid", int(1), true, false],
  ["hidden", int(201), true, true],
  ["negative", int(-1), true, true],
  ["huge", int("999999999999999999999"), true, true],
  ["bool", bool(true), false, false],
  ["missing", null, false, false],
] as [string, Value | null, boolean, boolean][])
  test("Array marker " + name, () => {
    const v = array(
        Array.from({ length: 205 }, () => int(1)),
        [],
        value ? [binding("i", value)] : [],
      ),
      r = adapt(v, { ...arrayConfig, indices: [selector("i")] });
    assert(r.available && r.kind === "array");
    assert.equal(r.elements.length, 200);
    assert.equal(r.hiddenCount, 5);
    assert.equal(r.markerIndices.length, valid ? 1 : 0);
    if (valid) assert.equal(r.markerIndices[0]!.outsideVisible, outside);
    if (name === "bool") assert(r.notes.length);
  });
test("Array rejects over two markers and expressions", () => {
  assert(
    !adapt(array([]), {
      ...arrayConfig,
      indices: [selector("i"), selector("j"), selector("k")],
    }).available,
  );
  assert(
    !adapt(array([]), { ...arrayConfig, array: selector("a[0]") }).available,
  );
});
function treeModel(n: number, skew = false): TreeViewModel {
  const data = tree(n, skew),
    r = adapt(view(data.objects, data.globals), treeConfig);
  assert(r.available && r.kind === "tree");
  return r;
}
for (const [name, n, skew] of [
  ["None", 0, false],
  ["single", 1, false],
  ["balanced", 7, false],
  ["skew", 12, true],
  ["200 nodes", 200, false],
] as [string, number, boolean][])
  test("Tree " + name, () => {
    const r = treeModel(n, skew);
    assert.equal(r.nodes.length, n);
    assert.equal(r.edges.length, Math.max(0, n - 1));
    assert.equal(new Set(r.nodes.map((n) => n.x)).size, n);
    assert.deepEqual(r, treeModel(n, skew));
  });
for (const [name, mutation, code] of [
  [
    "missing value",
    (o: Extract<ObjectRecord, { kind: "instance" }>) => {
      o.attrs = o.attrs.filter((b) => b.name !== "value");
    },
    "MISSING_FIELD",
  ],
  [
    "missing child",
    (o: Extract<ObjectRecord, { kind: "instance" }>) => {
      o.attrs = o.attrs.filter((b) => b.name !== "left");
    },
    "MISSING_FIELD",
  ],
  [
    "scalar child",
    (o: Extract<ObjectRecord, { kind: "instance" }>) => {
      o.attrs.find((b) => b.name === "left")!.value = int(1);
    },
    "WRONG_TYPE",
  ],
  [
    "self cycle",
    (o: Extract<ObjectRecord, { kind: "instance" }>) => {
      o.attrs.find((b) => b.name === "left")!.value = ref("o1");
    },
    "CYCLE",
  ],
] as [
  string,
  (o: Extract<ObjectRecord, { kind: "instance" }>) => void,
  string,
][])
  test("Tree invalid " + name, () => {
    const d = tree(1);
    mutation(d.objects[0] as Extract<ObjectRecord, { kind: "instance" }>);
    const r = adapt(view(d.objects, d.globals), treeConfig);
    assert(!r.available);
    assert.equal(r.code, code);
  });
test("Tree shared child rejected without fake duplication", () => {
  const d = tree(2),
    o = d.objects[0] as Extract<ObjectRecord, { kind: "instance" }>;
  o.attrs.find((b) => b.name === "right")!.value = ref("o2");
  const r = adapt(view(d.objects, d.globals), treeConfig);
  assert(!r.available && r.code === "SHARED_CHILD");
});
test("Tree deeper cycle rejected", () => {
  const d = tree(3, true),
    o = d.objects[2] as Extract<ObjectRecord, { kind: "instance" }>;
  o.attrs.find((b) => b.name === "left")!.value = ref("o1");
  const r = adapt(view(d.objects, d.globals), treeConfig);
  assert(!r.available && r.code === "CYCLE");
});
test("Tree 201 limit", () => {
  const d = tree(201),
    r = adapt(view(d.objects, d.globals), treeConfig);
  assert(!r.available && r.code === "LIMIT");
});
for (const value of [
  int(-5),
  int("123456789012345678901234567890"),
  str("<script>"),
  float("nan"),
])
  test("Tree exact value " + JSON.stringify(value), () => {
    const d = tree(1),
      o = d.objects[0] as Extract<ObjectRecord, { kind: "instance" }>;
    o.attrs.find((b) => b.name === "value")!.value = value;
    const r = adapt(view(d.objects, d.globals), treeConfig);
    assert(r.available && r.kind === "tree");
    assert.equal(r.nodes[0]!.label, formatValue(value, new Map()));
  });
for (const kind of ["list", "tuple", "set", "deque"] as const)
  test("Tree visited " + kind + " and marker-only layout stability", () => {
    const d = tree(3),
      items = [ref("o2"), ref("o3")];
    d.objects.push(
      kind === "deque"
        ? { id: "o1000", kind, items, maxlen: null }
        : { id: "o1000", kind, items },
    );
    d.globals.push(
      binding("visited", ref("o1000")),
      binding("current", ref("o2")),
    );
    const v = view(d.objects, d.globals),
      r = adapt(v, {
        ...treeConfig,
        current: selector("current"),
        visited: selector("visited"),
      });
    assert(r.available && r.kind === "tree");
    assert.deepEqual(r.visitedIds, ["o2", "o3"]);
    assert.equal(r.currentId, "o2");
    assert.deepEqual(
      r.nodes.map(({ id, x, y }) => ({ id, x, y })),
      treeModel(3).nodes.map(({ id, x, y }) => ({ id, x, y })),
    );
    for (const o of d.objects)
      if (o.kind === "instance")
        o.attrs.find((b) => b.name === "value")!.value = str("new label");
    const relabeled = adapt(view(d.objects, d.globals), treeConfig);
    assert(relabeled.available && relabeled.kind === "tree");
    assert.deepEqual(
      relabeled.nodes.map(({ id, x, y }) => ({ id, x, y })),
      r.nodes.map(({ id, x, y }) => ({ id, x, y })),
    );
  });
for (const [name, marker] of [
  ["missing", null],
  ["outside", ref("o999")],
  ["scalar", int(1)],
] as [string, Value | null][])
  test("Tree current " + name + " ignored", () => {
    const d = tree(1);
    if (marker) {
      d.globals.push(binding("current", marker));
      if (name === "outside") d.objects.push(list("o999", []));
    }
    const r = adapt(view(d.objects, d.globals), {
      ...treeConfig,
      current: selector("current"),
    });
    assert(r.available && r.kind === "tree");
    assert.equal(r.currentId, null);
    if (marker) assert(r.notes.length);
  });
test("Tree wrong root and wrong-kind child", () => {
  const r = adapt(
    view([list("o1", [])], [binding("root", ref("o1"))]),
    treeConfig,
  );
  assert(!r.available && r.code === "WRONG_TYPE");
  const d = tree(1);
  (d.objects[0] as Extract<ObjectRecord, { kind: "instance" }>).attrs.find(
    (b) => b.name === "left",
  )!.value = ref("o2");
  d.objects.push(list("o2", []));
  assert(!adapt(view(d.objects, d.globals), treeConfig).available);
});
function graphModel(rows: [Value, Value[]][]): GraphViewModel {
  const d = graph(rows),
    r = adapt(view(d.objects, d.globals), graphConfig);
  assert(r.available && r.kind === "graph");
  return r;
}
for (const [name, rows, vertices, edges] of [
  ["empty", [], 0, 0],
  ["int", [[int(1), [int(2)]]], 2, 1],
  ["str", [[str("a"), [str("b")]]], 2, 1],
  [
    "typed int/str",
    [
      [int(1), [str("1")]],
      [str("1"), []],
    ],
    2,
    1,
  ],
  ["huge int", [[int("999999999999999999999"), []]], 1, 0],
  ["duplicate", [[int(1), [int(2), int(2)]]], 2, 1],
  ["self loop", [[int(1), [int(1)]]], 1, 1],
] as [string, [Value, Value[]][], number, number][])
  test("Graph " + name, () => {
    const r = graphModel(rows);
    assert.equal(r.vertices.length, vertices);
    assert.equal(r.edges.length, edges);
    assert.deepEqual(r, graphModel(rows));
    if (name === "self loop") assert(r.edges[0]!.selfLoop);
    if (name === "typed int/str")
      assert.notEqual(r.vertices[0]!.id, r.vertices[1]!.id);
  });
for (const [name, key, neighbors] of [
  ["bool key", bool(true), []],
  ["bool neighbor", int(1), [bool(true)]],
  ["float key", float("1.0"), []],
  ["none neighbor", int(1), [none]],
] as [string, Value, Value[]][])
  test("Graph rejects " + name, () => {
    const d = graph([[key, neighbors]]),
      r = adapt(view(d.objects, d.globals), graphConfig);
    assert(!r.available && r.code === "WRONG_TYPE");
  });
for (const kind of ["tuple", "deque", "set"] as const)
  test("Graph rejects adjacency " + kind, () => {
    const d = graph([[int(1), [int(2)]]]);
    d.objects[1] =
      kind === "deque"
        ? { id: "o2", kind, items: [int(2)], maxlen: null }
        : { id: "o2", kind, items: [int(2)] };
    assert(!adapt(view(d.objects, d.globals), graphConfig).available);
  });
for (const count of [200, 201])
  test("Graph vertex bound " + count, () => {
    const d = graph(Array.from({ length: count }, (_, i) => [int(i), []])),
      r = adapt(view(d.objects, d.globals), graphConfig);
    assert.equal(r.available, count === 200);
  });
for (const kind of ["list", "tuple", "deque", "set"] as const)
  test("Graph markers/ordered strips " + kind, () => {
    const d = graph([[int(1), [str("1"), int(2)]]]),
      items = [int(2), str("1"), int(1)];
    d.objects.push(
      kind === "deque"
        ? { id: "o1000", kind, items, maxlen: null }
        : { id: "o1000", kind, items },
    );
    d.globals.push(
      binding("visited", ref("o1000")),
      binding("current", str("1")),
    );
    const v = view(d.objects, d.globals),
      r = adapt(v, {
        ...graphConfig,
        current: selector("current"),
        visited: selector("visited"),
        queue: selector("visited"),
        stack: selector("visited"),
      });
    assert(r.available && r.kind === "graph");
    assert.equal(r.vertices.filter((v) => v.visited).length, 3);
    assert.equal(r.vertices.filter((v) => v.current).length, 1);
    assert.equal(r.queue.length, kind === "set" ? 0 : 3);
    assert.deepEqual(r.queue, r.stack);
    assert.deepEqual(
      r.vertices.map(({ id, x, y }) => ({ id, x, y })),
      graphModel([[int(1), [str("1"), int(2)]]]).vertices.map(
        ({ id, x, y }) => ({ id, x, y }),
      ),
    );
  });
test("Graph no reverse inference and topology ordering independent of source insertion", () => {
  const a = graphModel([
      [int(2), []],
      [int(1), [int(2)]],
    ]),
    b = graphModel([
      [int(1), [int(2)]],
      [int(2), []],
    ]);
  assert.deepEqual(a, b);
  assert.equal(a.edges.length, 1);
  assert.equal(a.edges[0]!.from, JSON.stringify(["int", "1"]));
  const prefix = "x".repeat(121),
    duplicateLabels = graphModel([[str(prefix + "a"), [str(prefix + "b")]]]);
  assert.equal(duplicateLabels.vertices.length, 2);
  assert.equal(
    duplicateLabels.vertices[0]!.label,
    duplicateLabels.vertices[1]!.label,
  );
  assert.notEqual(
    duplicateLabels.vertices[0]!.id,
    duplicateLabels.vertices[1]!.id,
  );
  assert.notEqual(duplicateLabels.edges[0]!.from, duplicateLabels.edges[0]!.to);
});
for (const name of [
  "module",
  "function",
  "class",
  "recursion",
  "return_root",
]) {
  const f = name === "module" ? "assignment" : name;
  test("CallStack actual oracle " + name, () => {
    const core = TraceCore.load(fixture(f + "-128").trace);
    let returning = false;
    for (let step = 1; step <= core.totalSteps; step++) {
      const v = core.selectStep({ step, requestId: 1 }),
        r = adapt(v, { kind: "call_stack" });
      assert(r.available && r.kind === "call_stack");
      assert.deepEqual(
        r.frames.map((f) => f.id),
        v.callStack.map((f) => f.id),
      );
      assert.deepEqual(
        r.frames.map((f) => f.kind),
        v.callStack.map((f) => f.kind),
      );
      assert.deepEqual(
        r.frames.map((f) => f.callSite),
        v.callStack.map((f) => f.callSite),
      );
      if (v.event.kind === "RETURN") {
        assert(r.frames.at(-1)!.returning);
        assert.equal(
          r.frames.at(-1)!.returnLabel,
          v.event.payload.returnValueKnown
            ? formatValue(
                v.event.payload.returnValue,
                new Map(v.state.objects.map((o) => [o.id, o])),
              )
            : "выход из функции",
        );
        returning = true;
      }
      if (v.event.kind === "RUN_END") assert.equal(r.frames.length, 0);
    }
    assert(returning);
  });
}
test("current_frame ignores selected caller and module resolves globals", () => {
  const core = TraceCore.load(fixture("frame_changes-128").trace),
    v = core.selectStep({ step: 4, requestId: 1, requestedFrameId: "f1" });
  const top = v.callStack.at(-1)!;
  assert.deepEqual(
    lookup(v, selector("x", "current_frame")),
    top.locals.find((b) => b.name === "x")?.value,
  );
  assert.equal(v.selectedFrameId, "f1");
  const module = core.selectStep({ step: 1, requestId: 1 });
  assert.deepEqual(
    lookup(module, selector("g", "current_frame")),
    module.globals.find((b) => b.name === "g")?.value,
  );
  const finished = TraceCore.load(fixture("assignment-128").trace);
  const end = finished.selectStep({ step: finished.totalSteps, requestId: 1 });
  assert(end.globals.length > 0);
  assert.equal(
    lookup(end, selector(end.globals[0]!.name, "current_frame")),
    undefined,
  );
});
for (const name of ["__proto__", "constructor", "Я", "a"])
  test("BindingSelector name " + name, () => {
    assert(validSelector(selector(name)));
    assert.deepEqual(
      lookup(view([], [binding(name, int(1))]), selector(name)),
      int(1),
    );
  });
for (const name of ["a.b", "a[0]", "a()", "x + y", ""])
  test("BindingSelector rejects expression " + name, () =>
    assert(!validSelector(selector(name))),
  );
for (const [name, value, label] of [
  ["None", none, "None"],
  ["bool", bool(true), "True"],
  ["huge int", int("999999999999999999999"), "999999999999999999999"],
  ["nan", float("nan"), "nan"],
  ["inf", float("inf"), "inf"],
  ["-inf", float("-inf"), "-inf"],
  ["quoted newline", str('"x"\ny'), JSON.stringify('"x"\ny')],
] as [string, Value, string][])
  test("Formatter " + name, () =>
    assert.equal(formatValue(value, new Map()), label),
  );
test("Formatter cycles, shared identity, opaque and bounded truncation", () => {
  const objects = new Map(
    [list("o1", [ref("o1"), ref("o2"), ref("o2")]), list("o2", [int(1)])].map(
      (o) => [o.id, o],
    ),
  );
  const label = formatValue(ref("o1"), objects);
  assert(label.includes("cycle"));
  assert(label.includes("o2"));
  assert(label.length < 260);
  assert(formatValue(str("x".repeat(20000)), objects).includes("обрезано"));
  assert(
    formatValue(
      ref("o3"),
      new Map([
        [
          "o3",
          {
            id: "o3",
            kind: "opaque",
            pythonType: "A",
            reason: "unsupported_type",
          },
        ],
      ]),
    ).includes("opaque"),
  );
});
test("config accessors rejected without invocation; adapters leave immutable input unchanged", () => {
  let called = 0;
  const c = Object.defineProperty({}, "kind", {
    get() {
      called++;
      return "array";
    },
  }) as VisualizerConfig;
  const v = array([int(1)]),
    before = JSON.stringify(v);
  assert(!adapt(v, c).available);
  assert.equal(called, 0);
  assert.equal(JSON.stringify(v), before);
});
for (const [kind, data, config] of [
  [
    "array",
    { objects: [list("o1", [int(1)])], globals: [binding("a", ref("o1"))] },
    arrayConfig,
  ],
  ["tree", tree(30), treeConfig],
  [
    "graph",
    graph(Array.from({ length: 20 }, (_, i) => [int(i), [int((i + 1) % 20)]])),
    graphConfig,
  ],
] as const)
  test("Repeated canonical determinism " + kind, () => {
    const v = view([...data.objects], [...data.globals]),
      first = adapt(v, config);
    for (let i = 0; i < 20; i++) assert.deepEqual(adapt(v, config), first);
    assert(Object.isFrozen(first));
  });
for (const kind of ["array", "tree", "graph"] as const)
  test("required binding missing " + kind, () => {
    const c =
      kind === "array"
        ? arrayConfig
        : kind === "tree"
          ? treeConfig
          : graphConfig;
    const r = adapt(view([], []), c);
    assert(!r.available && r.code === "MISSING_BINDING");
  });
test("Tree visited outside/invalid markers remain available with bounded notes", () => {
  const d = tree(1);
  d.objects.push(list("o999", [ref("o1"), int(4)]));
  d.globals.push(binding("visited", ref("o999")));
  const r = adapt(view(d.objects, d.globals), {
    ...treeConfig,
    visited: selector("visited"),
  });
  assert(r.available && r.kind === "tree");
  assert.deepEqual(r.visitedIds, ["o1"]);
  assert(r.notes.length === 1);
  const bad = adapt(
    view(d.objects, [
      ...d.globals.filter((b) => b.name !== "visited"),
      binding("visited", int(1)),
    ]),
    { ...treeConfig, visited: selector("visited") },
  );
  assert(bad.available && bad.kind === "tree" && bad.notes.length === 1);
});
test("Graph ordered marker strips keep sequence and duplicates", () => {
  const d = graph([[int(1), [int(2)]]]);
  d.objects.push(list("o999", [int(2), int(1), int(2)]));
  d.globals.push(binding("q", ref("o999")), binding("c", int(9)));
  const r = adapt(view(d.objects, d.globals), {
    ...graphConfig,
    queue: selector("q"),
    stack: selector("q"),
    current: selector("c"),
  });
  assert(r.available && r.kind === "graph");
  assert.deepEqual(
    r.queue,
    [
      ["int", "2"],
      ["int", "1"],
      ["int", "2"],
    ].map((v) => JSON.stringify(v)),
  );
  assert(r.notes.length);
  assert(!r.vertices.some((v) => v.current));
});
test("Graph rejects scalar adjacency value", () => {
  const d = graph([[int(1), []]]);
  (d.objects[0] as Extract<ObjectRecord, { kind: "dict" }>).entries[0]!.value =
    int(2);
  assert(!adapt(view(d.objects, d.globals), graphConfig).available);
});
test("CallStack selected Globals and recursive identity are independent of names", () => {
  const core = TraceCore.load(fixture("recursion-128").trace);
  for (let step = 1; step <= core.totalSteps; step++) {
    const v = core.selectStep({ step, requestId: 1, requestedFrameId: null }),
      r = adapt(v, { kind: "call_stack" });
    assert(r.available && r.kind === "call_stack");
    assert(r.frames.every((f) => !f.selected));
    assert.equal(new Set(r.frames.map((f) => f.id)).size, r.frames.length);
  }
});
test("Nested Array metadata marks outer position without marking every list cell", () => {
  const core = TraceCore.load(JSON.parse(requireFixture("nested")).trace),
    r = adapt(core.selectStep({ step: 5, requestId: 1 }), {
      ...arrayConfig,
      array: selector("outer"),
    });
  assert(r.available && r.kind === "array");
  assert.deepEqual(
    r.elements.map((e) => e.changed),
    [true],
  );
});

test("Selector rejects inherited getters without invoking them", () => {
  let calls = 0;
  const selector = Object.create({
    get scope() {
      calls++;
      return "globals";
    },
    get name() {
      calls++;
      return "a";
    },
  });
  assert.equal(
    validateConfig({
      kind: "array",
      mode: "cells",
      indices: [],
      array: selector,
    }),
    false,
  );
  assert.equal(calls, 0);
});
