import type { RunController } from "../runs/controller.ts";
import type { ViewerController } from "../viewer/controller.ts";
import type { VisualizerConfig } from "../visualizers/types.ts";
import { ownConfig } from "../visualizers/config.ts";
import { EXAMPLES } from "./catalog.ts";

/** Ephemeral draft preset, captured at acceptance; never part of wire/storage. */
export class ExamplesController {
  private run: RunController;
  private viewer: ViewerController;
  private pending: VisualizerConfig | null = null;
  private accepted: { runId: string; config: VisualizerConfig } | null = null;
  constructor(run: RunController, viewer: ViewerController) {
    this.run = run;
    this.viewer = viewer;
  }
  get pendingConfig(): VisualizerConfig | null {
    return this.pending;
  }
  select(id: string): boolean {
    const example = EXAMPLES.find((entry) => entry.id === id);
    if (!example || !this.run.loadDraft(example.sourceCode)) return false;
    this.pending = ownConfig(example.visualizerConfig);
    return true;
  }
  connect(): () => void {
    const lifecycle = this.run.subscribeLifecycle((event) => {
      if (event.type === "accepted") {
        this.accepted = this.pending
          ? { runId: event.run.runId, config: this.pending }
          : null;
        this.pending = null;
      } else if (event.type === "reset" || event.type === "dispose") {
        this.pending = null;
        this.accepted = null;
      }
    });
    const selection = this.viewer.subscribe(() => {
      const preset = this.accepted;
      const trace = this.run.getSnapshot().trace;
      const view = this.viewer.getSnapshot();
      if (
        preset &&
        trace.phase === "READY" &&
        trace.runId === preset.runId &&
        view.phase === "READY" &&
        view.runId === preset.runId &&
        this.viewer.isAttached(trace.session ?? trace.handle)
      ) {
        // Consume BEFORE requesting: setVisualizerConfig publishes synchronously.
        this.accepted = null;
        this.viewer.setVisualizerConfig(preset.config);
      }
    });
    return () => {
      lifecycle();
      selection();
      this.pending = null;
      this.accepted = null;
    };
  }
}
