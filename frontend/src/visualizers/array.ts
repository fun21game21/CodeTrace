import type { SelectedStepView } from "../trace/index.ts";
import { lookup, nameOrder } from "./bindings.ts";
import { formatValue, numeric } from "./value_format.ts";
import { unavailable } from "./types.ts";
import type {
  ArrayViewModel,
  VisualizerConfig,
  VisualizerUnavailable,
} from "./types.ts";
export function arrayAdapter(
  view: SelectedStepView,
  c: Extract<VisualizerConfig, { kind: "array" }>,
): ArrayViewModel | VisualizerUnavailable {
  const objects = new Map(view.state.objects.map((o) => [o.id, o]));
  const candidate = c.array
    ? { name: c.array.name, value: lookup(view, c.array) }
    : [...view.globals]
        .sort((a, b) => nameOrder(a.name, b.name))
        .find((b) => {
          if (!("$ref" in b.value)) return false;
          const o = objects.get(b.value.$ref);
          return (
            o?.kind === "list" &&
            o.items.every(
              (v) => !("$ref" in v) && (v.type === "int" || v.type === "float"),
            )
          );
        });
  if (!candidate?.value)
    return unavailable(
      "MISSING_BINDING",
      c.array
        ? "Переменная ещё не определена"
        : "Подходящий числовой list не найден. Выберите переменную.",
    );
  const value = candidate.value,
    object = "$ref" in value ? objects.get(value.$ref) : undefined;
  if (object?.kind !== "list")
    return unavailable("WRONG_TYPE", "Массив должен быть обычным list.");
  const changes = new Set(
    view.arrayPositionChanges.find((e) => e.objectId === object.id)?.indices ??
      [],
  );
  const elements = object.items.slice(0, 200).map((v, index) => ({
    index,
    value: v,
    label: formatValue(v, objects),
    changed: changes.has(index),
    numeric: numeric(v),
  }));
  const suitable = elements.every((e) => e.numeric !== null);
  const effectiveMode = c.mode === "bars" && suitable ? "bars" : "cells";
  const notes: string[] = [],
    markers: ArrayViewModel["markerIndices"][number][] = [];
  for (const selector of c.indices) {
    const v = lookup(view, selector);
    if (!v) continue;
    if ("$ref" in v || v.type !== "int") {
      notes.push("Маркер " + selector.name + ": нужен int.");
      continue;
    }
    const n = Number(v.value),
      index = Number.isSafeInteger(n) ? n : null;
    markers.push({
      name: selector.name,
      indexText: v.value,
      index,
      outsideVisible: index === null || index < 0 || index >= elements.length,
    });
  }
  const ns = elements.flatMap((e) => (e.numeric === null ? [] : [e.numeric]));
  let min = Math.min(0, ...ns),
    max = Math.max(0, ...ns);
  if (min === max) {
    min = 0;
    max = 1;
  }
  return {
    available: true,
    kind: "array",
    objectId: object.id,
    binding: candidate.name,
    elements,
    markerIndices: markers,
    hiddenCount: object.items.length - elements.length,
    effectiveMode,
    ...(c.mode === "bars" && !suitable
      ? {
          modeReason:
            "Столбцы требуют конечных int/float в безопасной числовой шкале. Показаны ячейки.",
        }
      : {}),
    scale: { min, max },
    notes,
  };
}
