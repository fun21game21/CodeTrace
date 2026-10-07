import { writeFileSync } from "node:fs";
import {
  array,
  int,
  tree,
  treeConfig,
  graph,
  graphConfig,
  view,
} from "./helpers.ts";
const t = tree(200),
  g = graph(
    Array.from({ length: 200 }, (_, i) => [
      int(i),
      Array.from({ length: 8 }, (_, j) => int((i + j + 1) % 200)),
    ]),
  ),
  dense = graph(
    Array.from({ length: 200 }, (_, i) => [
      int(i),
      Array.from({ length: 80 }, (_, j) => int((i + j + 1) % 200)),
    ]),
  );
const fixtures = [
  {
    name: "array-200",
    view: array(Array.from({ length: 200 }, (_, i) => int(i))),
    config: { kind: "array", mode: "cells", indices: [] },
  },
  { name: "tree-200", view: view(t.objects, t.globals), config: treeConfig },
  {
    name: "graph-200-1600-edges",
    view: view(g.objects, g.globals),
    config: graphConfig,
  },
  {
    name: "graph-200-16000-edges",
    view: view(dense.objects, dense.globals),
    config: graphConfig,
  },
];
writeFileSync(process.argv[2]!, JSON.stringify(fixtures));
console.log(
  "VISUALIZERS stress views generated: " +
    fixtures.map((f) => f.name).join(", "),
);
