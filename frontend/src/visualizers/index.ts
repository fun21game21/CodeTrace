import type { SelectedStepView } from "../trace/index.ts";
import { freezeView } from "../execution/freeze.ts";
import { validateConfig } from "./config.ts";
import { unavailable } from "./types.ts";
import type { VisualizerConfig, VisualizerResult } from "./types.ts";
import { arrayAdapter } from "./array.ts";
import { treeAdapter } from "./tree.ts";
import { graphAdapter } from "./graph.ts";
import { callStackAdapter } from "./callstack.ts";
export * from "./types.ts";
export function adapt(
  view: SelectedStepView,
  config: VisualizerConfig,
): VisualizerResult {
  if (!validateConfig(config))
    return unavailable(
      "INVALID_CONFIG",
      "Некорректная настройка визуализации.",
    );
  const result =
    config.kind === "none"
      ? { available: true as const, kind: "none" as const }
      : config.kind === "array"
        ? arrayAdapter(view, config)
        : config.kind === "tree"
          ? treeAdapter(view, config)
          : config.kind === "graph"
            ? graphAdapter(view, config)
            : callStackAdapter(view);
  return freezeView(result);
}
