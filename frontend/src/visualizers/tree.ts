import type { SelectedStepView } from "../trace/index.ts";
import type { StoredObject, StoredValue } from "../trace/types.ts";
import { lookup } from "./bindings.ts";
import { formatValue } from "./value_format.ts";
import { unavailable } from "./types.ts";
import type {
  TreeViewModel,
  VisualizerConfig,
  VisualizerUnavailable,
} from "./types.ts";
import { treeLayout } from "./layout/tree_layout.ts";
export function treeAdapter(
  view: SelectedStepView,
  c: Extract<VisualizerConfig, { kind: "tree" }>,
): TreeViewModel | VisualizerUnavailable {
  const objects = new Map(view.state.objects.map((o) => [o.id, o])),
    root = lookup(view, c.root);
  if (!root)
    return unavailable("MISSING_BINDING", "Переменная ещё не определена");
  if (!("$ref" in root) && root.type === "none")
    return {
      available: true,
      kind: "tree",
      rootId: null,
      nodes: [],
      edges: [],
      currentId: null,
      visitedIds: [],
      notes: [],
      width: 200,
      height: 140,
    };
  if (!("$ref" in root) || objects.get(root.$ref)?.kind !== "instance")
    return unavailable(
      "WRONG_TYPE",
      "Корень должен быть Ref на instance или None.",
    );
  const seen = new Set<string>(),
    active = new Set<string>(),
    nodes: { id: string; label: string }[] = [],
    edges: TreeViewModel["edges"][number][] = [];
  const pending: { id: string; exit: boolean }[] = [
    { id: root.$ref, exit: false },
  ];
  while (pending.length) {
    const t = pending.pop()!;
    if (t.exit) {
      active.delete(t.id);
      continue;
    }
    if (active.has(t.id)) return unavailable("CYCLE", "Дерево содержит цикл.");
    if (seen.has(t.id))
      return unavailable(
        "SHARED_CHILD",
        "Общий child у нескольких родителей: это не binary tree.",
      );
    if (seen.size >= 200)
      return unavailable("LIMIT", "Больше 200 узлов. Используйте Variables.");
    const o = objects.get(t.id);
    if (o?.kind !== "instance")
      return unavailable("WRONG_TYPE", "Каждый узел должен быть instance.");
    const attrs = new Map(o.attrs.map((b) => [b.name, b.value]));
    const value = attrs.get(c.valueField),
      left = attrs.get(c.leftField),
      right = attrs.get(c.rightField);
    if (!value || !left || !right)
      return unavailable(
        "MISSING_FIELD",
        "В узле отсутствует value/left/right field из настройки.",
      );
    const child = (v: StoredValue): string | null | false =>
      !("$ref" in v)
        ? v.type === "none"
          ? null
          : false
        : objects.get(v.$ref)?.kind === "instance"
          ? v.$ref
          : false;
    const l = child(left),
      r = child(right);
    if (l === false || r === false)
      return unavailable(
        "WRONG_TYPE",
        "Child должен быть None или Ref на instance.",
      );
    seen.add(t.id);
    active.add(t.id);
    nodes.push({ id: t.id, label: formatValue(value, objects) });
    pending.push({ id: t.id, exit: true });
    if (l) edges.push({ from: t.id, to: l, side: "left" });
    if (r) edges.push({ from: t.id, to: r, side: "right" });
    if (r) pending.push({ id: r, exit: false });
    if (l) pending.push({ id: l, exit: false });
  }
  const notes: string[] = [],
    visited = new Set<string>();
  let currentId: string | null = null;
  if (c.current) {
    const v = lookup(view, c.current);
    if (v) {
      if ("$ref" in v && seen.has(v.$ref)) currentId = v.$ref;
      else notes.push("Current не указывает на узел дерева.");
    }
  }
  if (c.visited) {
    const v = lookup(view, c.visited),
      o = v && "$ref" in v ? objects.get(v.$ref) : undefined;
    if (v) {
      if (!isNodeCollection(o))
        notes.push("Visited: нужен list/tuple/set/deque с Ref узлов.");
      else {
        let ignored = 0;
        for (const item of o.items)
          if ("$ref" in item && seen.has(item.$ref)) visited.add(item.$ref);
          else ignored++;
        if (ignored)
          notes.push("Visited: пропущено несовпадающих маркеров: " + ignored);
      }
    }
  }
  const layout = treeLayout(root.$ref, edges);
  return {
    available: true,
    kind: "tree",
    rootId: root.$ref,
    nodes: nodes.map((n) => ({
      ...n,
      ...layout.positions.get(n.id)!,
      current: n.id === currentId,
      visited: visited.has(n.id),
    })),
    edges,
    currentId,
    visitedIds: nodes.filter((n) => visited.has(n.id)).map((n) => n.id),
    notes,
    width: layout.width,
    height: layout.height,
  };
}
export function isNodeCollection(
  o: StoredObject | undefined,
): o is Extract<StoredObject, { kind: "list" | "tuple" | "set" | "deque" }> {
  return !!o && ["list", "tuple", "set", "deque"].includes(o.kind);
}
