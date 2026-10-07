import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { adapt } from "../src/visualizers/index.ts";
import type { VisualizerConfig } from "../src/visualizers/types.ts";
import type { SelectedStepView } from "../src/trace/index.ts";
import { ArrayVisualizer } from "../src/visualizers/components/ArrayVisualizer.tsx";
import { TreeVisualizer } from "../src/visualizers/components/TreeVisualizer.tsx";
import { GraphVisualizer } from "../src/visualizers/components/GraphVisualizer.tsx";
import { stats } from "../tests/performance/metrics.ts";
import "../src/app/styles.css";
const root = createRoot(document.querySelector("#root")!);
async function main() {
  const cases = (await (await fetch("/stress-fixture.json")).json()) as {
    name: string;
    view: SelectedStepView;
    config: VisualizerConfig;
  }[];
  const results = [];
  for (const f of cases) {
    const samples = [];
    for (let i = 0; i < 25; i++) {
      const start = performance.now(),
        model = adapt(f.view, f.config),
        adaptMs = performance.now() - start;
      if (!model.available) throw Error(model.reason);
      const render = performance.now();
      flushSync(() =>
        root.render(
          model.kind === "array" ? (
            <ArrayVisualizer model={model} />
          ) : model.kind === "tree" ? (
            <TreeVisualizer model={model} />
          ) : model.kind === "graph" ? (
            <GraphVisualizer model={model} />
          ) : null,
        ),
      );
      const commitMs = performance.now() - render;
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
      const endToRenderMs = performance.now() - start;
      if (i >= 5) samples.push({ adaptMs, commitMs, endToRenderMs });
      flushSync(() => root.render(null));
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
    }
    results.push({
      name: f.name,
      samples,
      adapt: stats(samples.map((s) => s.adaptMs)),
      commit: stats(samples.map((s) => s.commitMs)),
      firstRender: stats(samples.map((s) => s.endToRenderMs)),
    });
  }
  const report = {
    date: new Date().toISOString(),
    userAgent: navigator.userAgent,
    production: import.meta.env.PROD,
    warmup: 5,
    samplesPerScenario: 20,
    results,
  };
  await fetch("/report", { method: "POST", body: JSON.stringify(report) });
  document.querySelector("#status")!.textContent =
    "VISUALIZERS stress complete";
}
void main().catch(async (error) => {
  document.querySelector("#status")!.textContent = "VISUALIZERS stress failed";
  await fetch("/failure", {
    method: "POST",
    body: JSON.stringify({ error: String(error) }),
  });
});
