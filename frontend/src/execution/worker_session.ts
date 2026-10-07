import type { SelectedStepView, StepRequest } from "../trace/index.ts";
import type { LoadTiming, Reply, WorkerPort } from "./protocol.ts";
import type {
  LoadedTraceSession,
  TraceLoader,
  TraceLoadRequest,
} from "./types.ts";
import { freezeView } from "./freeze.ts";

/** One owner: replacing/retrying always terminates the old Worker first. */
export class WorkerTraceLoader implements TraceLoader {
  private current: WorkerSession | null = null;
  private factory: () => WorkerPort;
  private identity: () => string;
  private measure: boolean;
  constructor(
    factory: () => WorkerPort,
    identity: () => string = () => crypto.randomUUID(),
    measure = false,
  ) {
    this.measure = measure;
    this.factory = factory;
    this.identity = identity;
  }
  load(request: TraceLoadRequest): Promise<LoadedTraceSession> {
    this.dispose();
    const session = new WorkerSession(
      this.factory(),
      this.identity(),
      request,
      this.measure,
    );
    this.current = session;
    return session.loaded.catch((error) => {
      session.dispose();
      if (this.current === session) this.current = null;
      throw error;
    });
  }
  dispose(): void {
    this.current?.dispose();
    this.current = null;
  }
}
export class WorkerSession implements LoadedTraceSession {
  readonly runId: string;
  readonly sourceCode: string;
  totalSteps = 0;
  completion!: LoadedTraceSession["completion"];
  readonly executor = {
    select: (request: StepRequest) => this.select(request),
  };
  readonly loaded: Promise<LoadedTraceSession>;
  /** Counts/timing only, useful to explicit development probes; never collects telemetry. */
  loadTiming: LoadTiming | null = null;
  lastFreezeMs = 0;
  lastSelectionMs = 0;
  private dead = false;
  private validated = false;
  private pending = new Map<
    number,
    {
      resolve: (v: SelectedStepView) => void;
      reject: (e: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private resolveLoad!: (session: LoadedTraceSession) => void;
  private rejectLoad!: (error: Error) => void;
  private loadTimer: ReturnType<typeof setTimeout>;
  private worker: WorkerPort;
  readonly identity: string;
  private expected: TraceLoadRequest;
  private measure: boolean;
  constructor(
    worker: WorkerPort,
    identity: string,
    expected: TraceLoadRequest,
    measure = false,
  ) {
    this.measure = measure;
    this.worker = worker;
    this.identity = identity;
    this.expected = expected;
    this.runId = expected.expectedRunId;
    this.sourceCode = expected.expectedSourceCode;
    this.loaded = new Promise((resolve, reject) => {
      this.resolveLoad = resolve;
      this.rejectLoad = reject;
    });
    this.loadTimer = setTimeout(() => this.fail(), 90000);
    worker.addEventListener("message", (event) => this.receive(event.data));
    worker.addEventListener("error", () => this.fail());
    worker.addEventListener("messageerror", () => this.fail());
    try {
      worker.postMessage({
        type: "load",
        sessionId: identity,
        request: expected,
        ...(measure ? { measure: true } : {}),
      });
    } catch {
      this.fail();
    }
  }
  private receive(reply: Reply): void {
    if (this.dead) return;
    if (!reply || typeof reply !== "object") {
      this.fail();
      return;
    }
    if (reply.sessionId !== this.identity) return;
    if (reply.type === "loaded") {
      if (
        this.validated ||
        reply.validated !== true ||
        reply.runId !== this.runId ||
        reply.sourceCode !== this.sourceCode ||
        !Number.isSafeInteger(reply.totalSteps) ||
        reply.totalSteps < 1 ||
        (this.expected.expectedEventCount !== null &&
          reply.totalSteps !== this.expected.expectedEventCount)
      ) {
        this.fail();
        return;
      }
      clearTimeout(this.loadTimer);
      this.totalSteps = reply.totalSteps;
      this.completion = freezeView(reply.completion);
      this.loadTiming = reply.timing
        ? Object.freeze({ ...reply.timing })
        : null;
      this.validated = true;
      this.resolveLoad(this);
      return;
    }
    if (reply.type === "error") {
      if (reply.requestId === null) {
        this.fail();
        return;
      }
      const job = this.pending.get(reply.requestId);
      if (!job) {
        this.fail();
        return;
      }
      this.pending.delete(reply.requestId);
      clearTimeout(job.timer);
      job.reject(new Error("Не удалось выбрать шаг."));
      return;
    }
    if (reply.type !== "selected") {
      this.fail();
      return;
    }
    const job = this.pending.get(reply.requestId);
    if (
      !job ||
      !reply.view ||
      typeof reply.view !== "object" ||
      reply.view.requestId !== reply.requestId ||
      reply.view.runId !== this.runId ||
      reply.view.totalSteps !== this.totalSteps
    ) {
      this.fail();
      return;
    }
    this.pending.delete(reply.requestId);
    clearTimeout(job.timer);
    try {
      const start = this.measure ? performance.now() : 0;
      const view = freezeView(reply.view);
      this.lastFreezeMs = this.measure ? performance.now() - start : 0;
      this.lastSelectionMs = reply.selectionMs ?? 0;
      job.resolve(view);
    } catch {
      job.reject(new Error("Некорректный результат выбора шага."));
      this.fail();
    }
  }
  select(request: StepRequest): Promise<SelectedStepView> {
    if (this.dead || !this.validated || this.pending.has(request.requestId))
      return Promise.reject(new Error("Trace session недоступна."));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(), 30000);
      this.pending.set(request.requestId, { resolve, reject, timer });
      try {
        this.worker.postMessage({
          type: "select",
          sessionId: this.identity,
          request,
        });
      } catch {
        this.fail();
      }
    });
  }
  private fail(): void {
    this.dispose();
  }
  dispose(): void {
    if (this.dead) return;
    this.dead = true;
    clearTimeout(this.loadTimer);
    this.worker.terminate();
    this.rejectLoad(new Error("Trace session закрыта или недоступна."));
    for (const job of this.pending.values()) {
      clearTimeout(job.timer);
      job.reject(new Error("Trace session закрыта или недоступна."));
    }
    this.pending.clear();
  }
}
