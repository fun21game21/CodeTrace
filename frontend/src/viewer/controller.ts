import { TraceCore } from "../trace/index.ts";
import type { TraceHandle } from "../execution/types.ts";
import type { StepRequest } from "../trace/index.ts";
import { DirectTraceSelectionExecutor } from "./executor.ts";
import { browserScheduler } from "./scheduler.ts";
import { SPEEDS } from "./types.ts";
import { NONE, ownConfig, validateConfig } from "../visualizers/config.ts";
import type { VisualizerConfig } from "../visualizers/types.ts";
import type {
  Scheduler,
  Speed,
  TraceSelectionExecutor,
  ViewerOptions,
  ViewerSnapshot,
} from "./types.ts";

const empty = (id: number): ViewerSnapshot =>
  Object.freeze({
    phase: "EMPTY",
    runId: null,
    totalSteps: 0,
    committedStep: null,
    pendingStep: null,
    latestRequestId: id,
    playing: false,
    speed: 5,
    selectedFrameId: null,
    committedView: null,
    directStepDraft: "",
    stepValidationMessage: null,
    selectionError: null,
    sliderTarget: null,
    visualizerConfig: NONE,
  });
interface Job {
  core: TraceHandle;
  epoch: number;
  request: StepRequest;
  config: VisualizerConfig;
}

/** One in-flight selection and one replaceable desired request. No network port. */
export class ViewerController {
  private state = empty(0);
  private listeners = new Set<() => void>();
  private core: TraceHandle | null = null;
  private executor: TraceSelectionExecutor | null = null;
  private scheduler: Scheduler;
  private factory: (core: TraceCore) => TraceSelectionExecutor;
  private epoch = 0;
  private counter = 0;
  private disposed = false;
  private flight: Job | null = null;
  private desired: Job | null = null;
  private playTimer: unknown = null;
  private sliderTimer: unknown = null;
  private plannedConfig: VisualizerConfig = NONE;
  constructor(options: ViewerOptions = {}) {
    this.scheduler = options.scheduler ?? browserScheduler;
    this.factory =
      options.executor ?? ((core) => new DirectTraceSelectionExecutor(core));
  }
  getSnapshot = (): ViewerSnapshot => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  isAttached(core: TraceHandle | null): boolean {
    return core !== null && this.core === core && !this.disposed;
  }
  private publish(patch: Partial<ViewerSnapshot>): void {
    if (this.disposed) return;
    this.state = Object.freeze({ ...this.state, ...patch });
    for (const listener of this.listeners) listener();
  }
  private cancelPlay(): void {
    if (this.playTimer !== null) this.scheduler.cancel(this.playTimer);
    this.playTimer = null;
  }
  private cancelSlider(): void {
    if (this.sliderTimer !== null) this.scheduler.cancel(this.sliderTimer);
    this.sliderTimer = null;
  }
  attach(core: TraceHandle): void {
    if (this.disposed || this.core === core) return;
    this.detach();
    this.core = core;
    this.executor =
      core instanceof TraceCore ? this.factory(core) : core.executor;
    this.publish({
      phase: "ATTACHED",
      runId: core.runId,
      totalSteps: core.totalSteps,
    });
    this.request(1, undefined);
  }
  detach(): void {
    this.cancelPlay();
    this.cancelSlider();
    ++this.epoch;
    this.core = null;
    this.executor = null;
    this.flight = null;
    this.desired = null;
    this.plannedConfig = NONE;
    if (this.state.phase !== "EMPTY") {
      this.state = empty(this.counter);
      for (const listener of this.listeners) listener();
    }
  }
  private ready(): boolean {
    return !this.disposed && this.state.phase === "READY";
  }
  private valid(step: number): boolean {
    return (
      this.core !== null &&
      Number.isSafeInteger(step) &&
      step >= 1 &&
      step <= this.core.totalSteps
    );
  }
  private request(step: number, frame: string | null | undefined): void {
    if (!this.core || !this.executor) return;
    const request: StepRequest = Object.freeze({
      step,
      requestId: ++this.counter,
      ...(frame === undefined ? {} : { requestedFrameId: frame }),
    });
    this.desired = {
      core: this.core,
      epoch: this.epoch,
      request,
      config: this.plannedConfig,
    };
    this.publish({
      pendingStep: step,
      latestRequestId: request.requestId,
      selectionError: null,
    });
    this.pump();
  }
  private pump(): void {
    if (this.flight || !this.desired || !this.executor || this.disposed) return;
    const job = this.desired,
      executor = this.executor;
    this.desired = null;
    this.flight = job;
    // The adapter may throw before returning a Promise; both forms share the failure path.
    void Promise.resolve()
      .then(() => executor.select(job.request))
      .then(
        (view) => {
          if (!this.current(job)) return;
          if (
            view.requestId !== job.request.requestId ||
            view.step !== job.request.step ||
            view.runId !== job.core.runId ||
            view.totalSteps !== job.core.totalSteps
          ) {
            this.failed(job);
            return;
          }
          // Every panel derives from this single view reference in the same publication.
          this.publish({
            phase: "READY",
            committedView: view,
            visualizerConfig: job.config,
            committedStep: view.step,
            selectedFrameId: view.selectedFrameId,
            pendingStep: null,
            directStepDraft: String(view.step),
            stepValidationMessage: null,
            selectionError: null,
            sliderTarget:
              this.state.sliderTarget === view.step
                ? null
                : this.state.sliderTarget,
            playing: this.state.playing && view.step < view.totalSteps,
          });
        },
        () => this.failed(job),
      )
      .finally(() => {
        if (this.flight !== job) return;
        this.flight = null;
        if (this.desired) this.pump();
        else this.schedulePlay();
      });
  }
  private current(job: Job): boolean {
    return (
      !this.disposed &&
      this.core === job.core &&
      this.epoch === job.epoch &&
      this.state.latestRequestId === job.request.requestId
    );
  }
  private failed(job: Job): void {
    if (!this.current(job)) return;
    this.plannedConfig = this.state.visualizerConfig;
    this.cancelPlay();
    this.cancelSlider();
    this.publish({
      pendingStep: null,
      playing: false,
      sliderTarget: null,
      directStepDraft:
        this.state.committedStep === null
          ? ""
          : String(this.state.committedStep),
      selectionError: "Не удалось выбрать шаг. Предыдущий шаг сохранён.",
    });
  }
  pause(): void {
    this.cancelPlay();
    if (this.state.playing) this.publish({ playing: false });
  }
  seek(step: number): boolean {
    if (!this.ready() || !this.valid(step)) return false;
    this.pause();
    this.cancelSlider();
    this.publish({ sliderTarget: null, stepValidationMessage: null });
    this.request(step, this.state.selectedFrameId);
    return true;
  }
  previous(): boolean {
    return this.state.committedStep !== null && this.state.committedStep > 1
      ? this.seek(this.state.committedStep - 1)
      : false;
  }
  next(): boolean {
    return this.state.committedStep !== null &&
      this.state.committedStep < this.state.totalSteps
      ? this.seek(this.state.committedStep + 1)
      : false;
  }
  setStepDraft(text: string): void {
    if (this.ready())
      this.publish({ directStepDraft: text, stepValidationMessage: null });
  }
  commitStepDraft(): boolean {
    if (!this.ready()) return false;
    const text = this.state.directStepDraft;
    const step = /^[0-9]+$/.test(text) ? Number(text) : NaN;
    if (!this.valid(step)) {
      this.publish({
        directStepDraft: String(this.state.committedStep),
        stepValidationMessage:
          "Введите целый шаг от 1 до " + this.state.totalSteps + ".",
      });
      return false;
    }
    // Enter followed by blur should not dispatch a duplicate selection.
    if (
      this.state.pendingStep === step ||
      (this.state.committedStep === step && this.state.pendingStep === null)
    ) {
      this.publish({
        directStepDraft: String(step),
        stepValidationMessage: null,
      });
      return true;
    }
    return this.seek(step);
  }
  sliderInput(step: number): boolean {
    if (!this.ready() || !this.valid(step)) return false;
    this.pause();
    this.publish({ sliderTarget: step });
    if (this.sliderTimer === null) {
      const epoch = this.epoch;
      this.sliderTimer = this.scheduler.schedule(() => {
        this.sliderTimer = null;
        if (epoch === this.epoch) this.flushSlider();
      }, 50);
    }
    return true;
  }
  sliderCommit(step: number): boolean {
    if (!this.ready() || !this.valid(step)) return false;
    this.pause();
    this.cancelSlider();
    this.publish({ sliderTarget: step });
    this.flushSlider();
    return true;
  }
  private flushSlider(): void {
    const step = this.state.sliderTarget;
    if (step === null || !this.ready()) return;
    if (this.state.pendingStep === step) return;
    if (step === this.state.committedStep && this.state.pendingStep === null) {
      this.publish({ sliderTarget: null });
      return;
    }
    this.request(step, this.state.selectedFrameId);
  }
  play(): boolean {
    if (
      !this.ready() ||
      this.state.committedStep === null ||
      this.state.committedStep >= this.state.totalSteps
    )
      return false;
    this.cancelSlider();
    this.publish({ playing: true, sliderTarget: null });
    this.schedulePlay();
    return true;
  }
  private schedulePlay(): void {
    if (
      this.playTimer !== null ||
      !this.state.playing ||
      this.flight ||
      this.desired ||
      this.state.pendingStep !== null ||
      !this.ready()
    )
      return;
    const epoch = this.epoch;
    this.playTimer = this.scheduler.schedule(() => {
      this.playTimer = null;
      if (
        epoch === this.epoch &&
        this.state.playing &&
        this.state.committedStep !== null
      )
        this.request(this.state.committedStep + 1, this.state.selectedFrameId);
    }, 1000 / this.state.speed);
  }
  setSpeed(speed: number): boolean {
    if (this.disposed || !(SPEEDS as readonly number[]).includes(speed))
      return false;
    this.publish({ speed: speed as Speed });
    this.cancelPlay();
    this.schedulePlay();
    return true;
  }
  selectFrame(frameId: string | null): boolean {
    if (!this.ready() || this.state.committedStep === null) return false;
    this.pause();
    this.cancelSlider();
    this.publish({ sliderTarget: null });
    this.request(this.state.committedStep, frameId);
    return true;
  }
  setVisualizerConfig(config: VisualizerConfig): boolean {
    if (
      !this.ready() ||
      this.state.committedStep === null ||
      !validateConfig(config)
    )
      return false;
    this.pause();
    this.cancelSlider();
    this.plannedConfig = ownConfig(config);
    this.request(this.state.committedStep, this.state.selectedFrameId);
    return true;
  }
  dispose(): void {
    if (this.disposed) return;
    this.detach();
    this.disposed = true;
    this.listeners.clear();
  }
}
