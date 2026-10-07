import type { TraceCore, StepRequest } from "../trace/index.ts";
import type { TraceSelectionExecutor } from "./types.ts";

/** Main-thread TRACE adapter. The Promise boundary carries failures and request IDs. */
export class DirectTraceSelectionExecutor implements TraceSelectionExecutor {
  private core: TraceCore;
  constructor(core: TraceCore) {
    this.core = core;
  }
  async select(request: StepRequest) {
    return this.core.selectStep(request);
  }
}
