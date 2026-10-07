import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { adapt } from "../../src/visualizers/index.ts";
import { ArrayVisualizer } from "../../src/visualizers/components/ArrayVisualizer.tsx";
import { TreeVisualizer } from "../../src/visualizers/components/TreeVisualizer.tsx";
import { GraphVisualizer } from "../../src/visualizers/components/GraphVisualizer.tsx";
import { CallStackVisualizer } from "../../src/visualizers/components/CallStackVisualizer.tsx";
import { VisualizerPanel } from "../../src/visualizers/components/VisualizerPanel.tsx";
import {
  array,
  int,
  float,
  str,
  tree,
  treeConfig,
  graph,
  graphConfig,
  view,
  selector,
} from "./helpers.ts";
const html = renderToStaticMarkup;
for (const mode of ["cells", "bars"] as const)
  test("Array component " + mode + " markers/200 cap", () => {
    const v = array(Array.from({ length: 205 }, (_, i) => int(i - 100))),
      r = adapt(v, { kind: "array", mode, indices: [] });
    assert(r.available && r.kind === "array");
    const s = html(createElement(ArrayVisualizer, { model: r }));
    assert(s.includes("Скрыто элементов: 5"));
    assert.equal((s.match(/data-index=/g) ?? []).length, 200);
    assert(s.includes('data-changed="true"'));
    assert(!s.includes("NaN"));
  });
test("Bars avoid overflow geometry with extreme finite mixed signs", () => {
  const r = adapt(
    array([
      float("-1.7976931348623157e+308"),
      float("1.7976931348623157e+308"),
    ]),
    { kind: "array", mode: "bars", indices: [] },
  );
  assert(r.available && r.kind === "array");
  const s = html(createElement(ArrayVisualizer, { model: r }));
  assert(!s.includes('="NaN"'));
  assert(!s.includes('="Infinity"'));
  assert(s.includes("bar-baseline"));
});
test("Cells HTML-looking strings are escaped", () => {
  const r = adapt(array([str("<script>alert(1)</script>")]), {
    array: { scope: "globals", name: "a" },
    kind: "array",
    mode: "cells",
    indices: [],
  });
  assert(r.available && r.kind === "array");
  const s = html(createElement(ArrayVisualizer, { model: r }));
  assert(s.includes("&lt;script&gt;"));
  assert(!s.includes("<script>"));
});
test("Tree SVG contains recorded nodes and left/right edges", () => {
  const d = tree(3),
    r = adapt(view(d.objects, d.globals), treeConfig);
  assert(r.available && r.kind === "tree");
  const s = html(createElement(TreeVisualizer, { model: r }));
  assert.equal((s.match(/data-node=/g) ?? []).length, 3);
  assert.equal((s.match(/<line /g) ?? []).length, 2);
  assert(s.includes(">L<") && s.includes(">R<"));
});
test("Tree SVG bounded to 200", () => {
  const d = tree(200),
    r = adapt(view(d.objects, d.globals), treeConfig);
  assert(r.available && r.kind === "tree");
  assert.equal(
    (
      html(createElement(TreeVisualizer, { model: r })).match(/data-node=/g) ??
      []
    ).length,
    200,
  );
});
test("Graph arrows/self-loop and typed labels render", () => {
  const d = graph([[int(1), [int(1), str("1")]]]),
    r = adapt(view(d.objects, d.globals), graphConfig);
  assert(r.available && r.kind === "graph");
  const s = html(createElement(GraphVisualizer, { model: r }));
  assert(s.includes("<marker "));
  assert(s.includes("marker-end"));
  assert(s.includes('data-self-loop="true"'));
  assert.equal((s.match(/data-vertex=/g) ?? []).length, 2);
  assert(s.includes("int 1") && s.includes("str &quot;1&quot;"));
});
test("CallStack component exact active module and empty final state", () => {
  const v = array([int(1)]),
    r = adapt(v, { kind: "call_stack" });
  assert(r.available && r.kind === "call_stack");
  assert(
    html(createElement(CallStackVisualizer, { model: r })).includes("module"),
  );
  assert(
    html(
      createElement(CallStackVisualizer, { model: { ...r, frames: [] } }),
    ).includes("Активных кадров нет"),
  );
});
for (const kind of ["none", "array", "tree", "graph", "call_stack"] as const)
  test("Panel settings choice " + kind + "/Apply/committed Step", () => {
    const v = array([int(1)]),
      config =
        kind === "none" || kind === "call_stack"
          ? { kind }
          : kind === "array"
            ? {
                kind,
                mode: "cells" as const,
                indices: [],
                array: selector("a"),
              }
            : kind === "tree"
              ? treeConfig
              : graphConfig;
    const s = html(
      createElement(VisualizerPanel, {
        view: v,
        config,
        onApply: () => true,
        pending: true,
      }),
    );
    assert(s.includes("Настройки визуализации"));
    assert(s.includes("Применить"));
    assert(s.includes("прежний шаг"));
    assert(s.includes("шаг 1"));
    assert.equal((s.match(/<option value=/g) ?? []).length >= 5, true);
  });
test("Panel unavailable reason leaves trace input untouched", () => {
  const v = array([int(1)]),
    before = JSON.stringify(v);
  const s = html(
    createElement(VisualizerPanel, {
      view: v,
      config: treeConfig,
      onApply: () => true,
    }),
  );
  assert(s.includes("Переменная ещё не определена"));
  assert.equal(JSON.stringify(v), before);
});
