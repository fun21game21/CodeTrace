import type { VisualizerConfig } from "./types.ts";
import { validSelector } from "./bindings.ts";
import { freezeView } from "../execution/freeze.ts";
function plainData(v: object): boolean {
  const proto = Object.getPrototypeOf(v);
  return (
    (proto === Object.prototype ||
      proto === null ||
      (Array.isArray(v) && proto === Array.prototype)) &&
    Reflect.ownKeys(v).every(
      (k) =>
        typeof k === "string" &&
        "value" in Object.getOwnPropertyDescriptor(v, k)!,
    )
  );
}
export const NONE: VisualizerConfig = Object.freeze({ kind: "none" });
export function validateConfig(config: unknown): config is VisualizerConfig {
  if (!config || typeof config !== "object" || !plainData(config)) return false;
  const c = config as VisualizerConfig;
  if (c.kind === "none" || c.kind === "call_stack") return true;
  if (c.kind === "array")
    return (
      (c.mode === "cells" || c.mode === "bars") &&
      (c.array === undefined || validSelector(c.array)) &&
      Array.isArray(c.indices) &&
      plainData(c.indices) &&
      c.indices.length <= 2 &&
      c.indices.every(validSelector)
    );
  if (c.kind === "tree")
    return (
      validSelector(c.root) &&
      [c.valueField, c.leftField, c.rightField].every(
        (v) => typeof v === "string" && v.length > 0 && v.length <= 128,
      ) &&
      (c.current === undefined || validSelector(c.current)) &&
      (c.visited === undefined || validSelector(c.visited))
    );
  if (c.kind === "graph")
    return (
      validSelector(c.adjacency) &&
      [c.current, c.visited, c.queue, c.stack].every(
        (v) => v === undefined || validSelector(v),
      )
    );
  return false;
}
export function ownConfig(c: VisualizerConfig): VisualizerConfig {
  const selector = (s: import("./types.ts").BindingSelector) => ({
    scope: s.scope,
    name: s.name,
  });
  if (c.kind === "none" || c.kind === "call_stack")
    return Object.freeze({ kind: c.kind });
  if (c.kind === "array")
    return freezeView({
      kind: c.kind,
      mode: c.mode,
      indices: c.indices.map(selector),
      ...(c.array ? { array: selector(c.array) } : {}),
    });
  if (c.kind === "tree")
    return freezeView({
      kind: c.kind,
      root: selector(c.root),
      valueField: c.valueField,
      leftField: c.leftField,
      rightField: c.rightField,
      ...(c.current ? { current: selector(c.current) } : {}),
      ...(c.visited ? { visited: selector(c.visited) } : {}),
    });
  return freezeView({
    kind: c.kind,
    adjacency: selector(c.adjacency),
    ...(c.current ? { current: selector(c.current) } : {}),
    ...(c.visited ? { visited: selector(c.visited) } : {}),
    ...(c.queue ? { queue: selector(c.queue) } : {}),
    ...(c.stack ? { stack: selector(c.stack) } : {}),
  });
}
