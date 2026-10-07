import type { SelectedStepView } from "../trace/index.ts";
import type { StoredValue } from "../trace/types.ts";
import type {
  VertexKey,
  GraphViewModel,
  VisualizerConfig,
  VisualizerUnavailable,
} from "./types.ts";
import { unavailable } from "./types.ts";
import { lookup, nameOrder } from "./bindings.ts";
import { isNodeCollection } from "./tree.ts";
import { graphLayout } from "./layout/graph_layout.ts";
export function vertexKey(v: StoredValue): VertexKey | null {
  return !("$ref" in v) && (v.type === "int" || v.type === "str")
    ? { type: v.type, value: v.value }
    : null;
}
export const vertexId = (k: VertexKey) => JSON.stringify([k.type, k.value]);
export const vertexOrder = (a: VertexKey, b: VertexKey) =>
  nameOrder(a.type, b.type) || nameOrder(a.value, b.value);
export function graphAdapter(
  view: SelectedStepView,
  c: Extract<VisualizerConfig, { kind: "graph" }>,
): GraphViewModel | VisualizerUnavailable {
  const objects = new Map(view.state.objects.map((o) => [o.id, o])),
    v = lookup(view, c.adjacency),
    o = v && "$ref" in v ? objects.get(v.$ref) : undefined;
  if (!v) return unavailable("MISSING_BINDING", "Переменная ещё не определена");
  if (o?.kind !== "dict")
    return unavailable(
      "WRONG_TYPE",
      "Граф: нужен dict[int | str, list[int | str]].",
    );
  const keys = new Map<string, VertexKey>(),
    edges = new Map<string, GraphViewModel["edges"][number]>();
  const add = (k: VertexKey) => {
    const id = vertexId(k);
    keys.set(id, k);
    return id;
  };
  for (const entry of o.entries) {
    const key = vertexKey(entry.key),
      items = "$ref" in entry.value ? objects.get(entry.value.$ref) : undefined;
    if (!key || items?.kind !== "list")
      return unavailable(
        "WRONG_TYPE",
        "Keys/neighbors: int или str; adjacency values: plain list, bool исключён.",
      );
    const from = add(key);
    for (const item of items.items) {
      const target = vertexKey(item);
      if (!target)
        return unavailable(
          "WRONG_TYPE",
          "Neighbor должен быть int или str, bool исключён.",
        );
      const to = add(target);
      edges.set(JSON.stringify([from, to]), {
        from,
        to,
        selfLoop: from === to,
      });
      if (keys.size > 200)
        return unavailable(
          "LIMIT",
          "Больше 200 вершин. Используйте Variables.",
        );
    }
    if (keys.size > 200)
      return unavailable("LIMIT", "Больше 200 вершин. Используйте Variables.");
  }
  const ordered = [...keys].sort((a, b) => vertexOrder(a[1], b[1])),
    notes: string[] = [],
    visited = new Set<string>();
  let current: string | null = null;
  if (c.current) {
    const val = lookup(view, c.current);
    if (val) {
      const k = vertexKey(val),
        id = k ? vertexId(k) : null;
      if (id && keys.has(id)) current = id;
      else notes.push("Current не совпадает с вершиной графа.");
    }
  }
  const marker = (
    selector: typeof c.visited,
    orderedOnly: boolean,
  ): string[] => {
    if (!selector) return [];
    const val = lookup(view, selector);
    if (!val) return [];
    const obj = "$ref" in val ? objects.get(val.$ref) : undefined;
    if (!isNodeCollection(obj) || (orderedOnly && obj.kind === "set")) {
      notes.push(
        selector.name +
          ": нужен " +
          (orderedOnly ? "list/tuple/deque" : "list/tuple/set/deque") +
          " int/str.",
      );
      return [];
    }
    const result: string[] = [];
    let ignored = 0;
    for (const item of obj.items) {
      const k = vertexKey(item),
        id = k ? vertexId(k) : null;
      if (id && keys.has(id)) result.push(id);
      else ignored++;
    }
    if (ignored) notes.push(selector.name + ": пропущено маркеров: " + ignored);
    return result;
  };
  for (const id of marker(c.visited, false)) visited.add(id);
  const queue = marker(c.queue, true),
    stack = marker(c.stack, true),
    layout = graphLayout(ordered.map(([id]) => id));
  const rank = new Map(ordered.map(([id], i) => [id, i]));
  return {
    available: true,
    kind: "graph",
    vertices: ordered.map(([id, key]) => ({
      id,
      key,
      label:
        key.type === "str"
          ? "str " +
            JSON.stringify(key.value.slice(0, 120)) +
            (key.value.length > 120 ? " …" : "")
          : "int " + key.value,
      ...layout.positions.get(id)!,
      current: id === current,
      visited: visited.has(id),
    })),
    edges: [...edges.values()].sort(
      (a, b) =>
        rank.get(a.from)! - rank.get(b.from)! ||
        rank.get(a.to)! - rank.get(b.to)!,
    ),
    queue,
    stack,
    notes,
    size: layout.size,
  };
}
