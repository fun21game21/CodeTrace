import { useMemo } from "react";
import type { SelectedStepView } from "../../trace/index.ts";
import type { VisualizerConfig } from "../types.ts";
import { adapt } from "../index.ts";
import { ArrayVisualizer } from "./ArrayVisualizer.tsx";
import { TreeVisualizer } from "./TreeVisualizer.tsx";
import { GraphVisualizer } from "./GraphVisualizer.tsx";
import { CallStackVisualizer } from "./CallStackVisualizer.tsx";
import { VisualizerSettings } from "./VisualizerSettings.tsx";
export function VisualizerPanel({
  view,
  config,
  onApply,
  onFrame,
  pending = false,
}: {
  view: SelectedStepView;
  config: VisualizerConfig;
  onApply: (c: VisualizerConfig) => boolean;
  onFrame?: (id: string | null) => void;
  pending?: boolean;
}) {
  const model = useMemo(() => adapt(view, config), [view, config]);
  return (
    <section className="visualizer-panel" aria-label="Визуализация">
      <h2>Визуализация · шаг {view.step}</h2>
      <VisualizerSettings config={config} onApply={onApply} />
      {pending && (
        <p className="pending-step" role="status">
          Подготовка… отображается прежний шаг и настройка.
        </p>
      )}
      {!model.available ? (
        <p className="viz-unavailable" role="status">
          {model.reason}
        </p>
      ) : model.kind === "none" ? (
        <p className="muted">Выберите структуру в настройках визуализации.</p>
      ) : model.kind === "array" ? (
        <ArrayVisualizer model={model} />
      ) : model.kind === "tree" ? (
        <TreeVisualizer model={model} />
      ) : model.kind === "graph" ? (
        <GraphVisualizer model={model} />
      ) : (
        <CallStackVisualizer model={model} {...(onFrame ? { onFrame } : {})} />
      )}
    </section>
  );
}
