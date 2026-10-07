import type {
  NormalizedState,
  ObjectRecord,
  Value,
  TraceDocument,
  Binding,
} from "../../../contracts/typescript/index.ts";
import { readFileSync } from "node:fs";
function minimal(): TraceDocument {
  return JSON.parse(
    readFileSync(
      new URL(
        "../../../contracts/fixtures/valid/trace-minimal-success.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as TraceDocument;
}
import { TraceCore } from "../../src/trace/index.ts";
import { identityOrder, nameOrder } from "../../src/trace/validation.ts";
export const int = (n: number | string): Value => ({
  kind: "scalar",
  type: "int",
  value: String(n),
});
export const str = (value: string): Value => ({
  kind: "scalar",
  type: "str",
  value,
});
export const bool = (value: boolean): Value => ({
  kind: "scalar",
  type: "bool",
  value,
});
export const float = (value: string): Value => ({
  kind: "scalar",
  type: "float",
  value,
});
export const none: Value = { kind: "scalar", type: "none", value: null };
export const ref = (id: string): Value => ({ $ref: id });
export const binding = (name: string, value: Value): Binding => ({
  name,
  value,
});
export const list = (id: string, items: Value[]): ObjectRecord => ({
  id,
  kind: "list",
  items,
});
export function trace(states: Partial<NormalizedState>[]): TraceDocument {
  const t = minimal(),
    initial = t.states[0]!;
  const frame = (t.states[1] as Extract<typeof initial, { kind: "delta" }>)
    .changes.framesUpsert[0]!;
  t.sourceCode = "pass";
  t.checkpointInterval = 1;
  t.limits.checkpoint_interval = 1;
  const snapshots = states.map(
    (s, i): NormalizedState => ({
      version: i + 1,
      globals: [],
      objects: [],
      stack: ["f1"],
      frames: [frame],
      eventRoots: [],
      ...s,
    }),
  );
  for (const s of snapshots) {
    s.globals = [...s.globals].sort((a, b) => nameOrder(a.name, b.name));
    s.objects = [...s.objects]
      .sort((a, b) => identityOrder(a.id, b.id))
      .map((o) =>
        o.kind === "instance"
          ? {
              ...o,
              attrs: [...o.attrs].sort((a, b) => nameOrder(a.name, b.name)),
            }
          : o,
      );
  }
  t.states = [
    initial,
    ...snapshots.map((state) => ({
      kind: "checkpoint" as const,
      version: state.version,
      state,
    })),
  ];
  const final = {
    ...snapshots.at(-1)!,
    version: snapshots.length + 1,
    stack: [],
    frames: [],
    eventRoots: [],
  };
  t.states.push({ kind: "checkpoint", version: final.version, state: final });
  t.events = snapshots.map((s) => ({
    index: s.version - 1,
    kind: "LINE" as const,
    stateVersion: s.version,
    frameId: "f1",
    source: { fileName: "<codetrace-user>" as const, lineNumber: 1 },
    consoleOffset: 0,
    payload: {},
  }));
  t.events.push({
    index: snapshots.length,
    kind: "RUN_END",
    stateVersion: final.version,
    frameId: null,
    source: null,
    consoleOffset: 0,
    payload: {},
  });
  return t;
}
export function view(objects: ObjectRecord[], globals: Binding[], step = 1) {
  return TraceCore.load(trace([{ objects, globals }])).selectStep({
    step,
    requestId: 1,
  });
}
export const selector = (
  name: string,
  scope: "globals" | "current_frame" = "globals",
) => ({ scope, name });
export function array(
  items: Value[],
  extras: ObjectRecord[] = [],
  globals: Binding[] = [],
) {
  return view(
    [list("o1", items), ...extras],
    [binding("a", ref("o1")), ...globals],
  );
}
export function tree(count: number, skew = false) {
  const objects: ObjectRecord[] = Array.from({ length: count }, (_, i) => {
    const child = (n: number) => (n < count ? ref("o" + (n + 1)) : none);
    return {
      id: "o" + (i + 1),
      kind: "instance",
      className: "Node",
      attrs: [
        binding("left", child(skew ? i + 1 : 2 * i + 1)),
        binding("right", skew ? none : child(2 * i + 2)),
        binding("value", int(i)),
      ],
    };
  });
  return { objects, globals: [binding("root", count ? ref("o1") : none)] };
}
export const treeConfig = {
  kind: "tree" as const,
  root: selector("root"),
  valueField: "value",
  leftField: "left",
  rightField: "right",
};
export function graph(rows: [Value, Value[]][]) {
  const objects: ObjectRecord[] = [
    {
      id: "o1",
      kind: "dict",
      entries: rows.map(([key], i) => ({ key, value: ref("o" + (i + 2)) })),
    },
    ...rows.map(([, items], i) => list("o" + (i + 2), items)),
  ];
  return { objects, globals: [binding("g", ref("o1"))] };
}
export const graphConfig = { kind: "graph" as const, adjacency: selector("g") };
