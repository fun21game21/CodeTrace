import { ViewerController } from "../../src/viewer/controller.ts";
import type {
  Scheduler,
  TraceSelectionExecutor,
} from "../../src/viewer/types.ts";
import { TraceCore } from "../../src/trace/index.ts";
import type { StepRequest, SelectedStepView } from "../../src/trace/index.ts";
import { fixture } from "../trace/helpers.ts";

export class FakeScheduler implements Scheduler {
  now = 0;
  private id = 0;
  timers = new Map<number, { at: number; callback: () => void }>();
  schedule(callback: () => void, delayMs: number) {
    const id = ++this.id;
    this.timers.set(id, { at: this.now + delayMs, callback });
    return id;
  }
  cancel(token: unknown) {
    this.timers.delete(token as number);
  }
  advance(ms: number) {
    const end = this.now + ms;
    while (true) {
      const entry = [...this.timers.entries()]
        .filter(([, t]) => t.at <= end)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!entry) break;
      this.now = entry[1].at;
      this.timers.delete(entry[0]);
      entry[1].callback();
    }
    this.now = end;
  }
}
export async function flush() {
  for (let i = 0; i < 16; i++) await Promise.resolve();
}
export function deferred(core: TraceCore) {
  const calls: StepRequest[] = [];
  const pending: {
    request: StepRequest;
    resolve(view: SelectedStepView): void;
    reject(error: Error): void;
  }[] = [];
  const executor: TraceSelectionExecutor = {
    select(request) {
      calls.push(request);
      return new Promise((resolve, reject) =>
        pending.push({ request, resolve, reject }),
      );
    },
  };
  return {
    calls,
    pending,
    executor,
    resolve(index = 0) {
      const job = pending[index]!;
      job.resolve(core.selectStep(job.request));
    },
    reject(index = 0) {
      pending[index]!.reject(new Error("selection failure"));
    },
  };
}
export async function attached(name = "frame_changes", delayed = false) {
  const core = TraceCore.load(fixture(name + "-128").trace);
  const clock = new FakeScheduler(),
    d = deferred(core);
  const viewer = new ViewerController({
    scheduler: clock,
    ...(delayed ? { executor: () => d.executor } : {}),
  });
  viewer.attach(core);
  await flush();
  if (delayed) {
    d.resolve();
    await flush();
  }
  return { core, clock, viewer, d };
}
