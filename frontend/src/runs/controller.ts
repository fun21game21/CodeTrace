import {
  validateContract,
  validateWs,
} from "../../../contracts/typescript/index.ts";
import type {
  Completion,
  RunFinishedPayload,
  TraceDocument,
  WsMessage,
} from "../../../contracts/typescript/index.ts";
import { TraceCore } from "../trace/index.ts";
import type { Immutable } from "../trace/index.ts";
import type {
  Health,
  RunDependencies,
  RunState,
  SocketPort,
  RunLifecycleEvent,
} from "./types.ts";

const TERMINAL = new Set([
  "SUCCEEDED",
  "FAILED",
  "LIMIT_EXCEEDED",
  "INTERRUPTED",
]);
const NEXT: Readonly<Record<string, readonly string[]>> = {
  STARTING: ["RUNNING", "FINALIZING"],
  RUNNING: ["WAITING_INPUT", "FINALIZING"],
  WAITING_INPUT: ["RUNNING", "FINALIZING"],
  FINALIZING: [...TERMINAL],
};
export const utf8Bytes = (text: string): number =>
  new TextEncoder().encode(text).length;
function requireValid(condition: boolean, message: string): asserts condition {
  if (!condition) throw new Error(message);
}
function freezeJson<T>(value: T): Immutable<T> {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freezeJson(child);
    Object.freeze(value);
  }
  return value as Immutable<T>;
}
function healthOf(value: unknown): Health {
  requireValid(
    value !== null && typeof value === "object" && !Array.isArray(value),
    "Invalid health response",
  );
  const h = value as Record<string, unknown>;
  requireValid(
    Object.keys(h).sort().join(",") ===
      "apiVersion,available,limits,pythonVersion,reason" &&
      h.apiVersion === 1 &&
      typeof h.available === "boolean" &&
      (h.reason === null || typeof h.reason === "string") &&
      h.pythonVersion === "3.13.16",
    "Invalid health response",
  );
  validateContract(h.limits, "trace", "ResourceLimits");
  return freezeJson(value as Health);
}
const emptyTrace = (): RunState["trace"] =>
  Object.freeze({
    phase: "EMPTY",
    handle: null,
    session: null,
    runId: null,
    eventCount: null,
    loadError: null,
    releasePending: false,
    releaseError: null,
  });
interface Connection {
  socket: SocketPort;
  candidate: string;
  clientRequestId: string;
  inSeq: number;
  outSeq: number;
  ready: boolean;
  sent: boolean;
  accepted: boolean;
  finished: boolean;
  startingSeen: boolean;
  inputCounter: number;
  inputSubmitted: string | null;
  consoleBytes: number;
}

/** Owns transport and immutable Run source; React contains no protocol logic. */
export class RunController {
  private state: RunState;
  private listeners = new Set<() => void>();
  private lifecycleListeners = new Set<(event: RunLifecycleEvent) => void>();
  private disposalListeners = new Set<() => void>();
  private dependencies: RunDependencies;
  private connection: Connection | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private publication: RunFinishedPayload | null = null;
  private retained = false;
  private disposed = false;
  private loadGeneration = 0;
  private healthGeneration = 0;
  private releasing = false;

  constructor(dependencies: RunDependencies, draftCode = "") {
    this.dependencies = dependencies;
    this.state = Object.freeze({
      health: null,
      healthPhase: "LOADING",
      healthError: null,
      editor: Object.freeze({
        draftCode,
        mode: "editing",
        runSourceCode: null,
      }),
      active: Object.freeze({
        runId: null,
        clientRequestId: null,
        status: "IDLE",
        sourceCode: null,
        pendingInput: null,
        liveConsole: Object.freeze([]),
        completion: null,
        transportLost: false,
      }),
      trace: emptyTrace(),
      connecting: false,
      transportError: null,
    });
  }
  getSnapshot = (): RunState => this.state;
  get isDisposed(): boolean {
    return this.disposed;
  }
  onDispose(listener: () => void): () => void {
    if (this.disposed) {
      listener();
      return () => {};
    }
    this.disposalListeners.add(listener);
    return () => {
      this.disposalListeners.delete(listener);
    };
  }
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  subscribeLifecycle(listener: (event: RunLifecycleEvent) => void): () => void {
    if (this.disposed) {
      listener({ type: "dispose" });
      return () => {};
    }
    this.lifecycleListeners.add(listener);
    return () => {
      this.lifecycleListeners.delete(listener);
    };
  }
  private emitLifecycle(event: RunLifecycleEvent): void {
    for (const listener of this.lifecycleListeners) {
      // Persistence observers cannot turn a valid backend message into protocol failure.
      try {
        listener(Object.freeze(event));
      } catch {
        /* Side-effect observer failure. */
      }
    }
  }
  canLoadDraft(): boolean {
    const s = this.state;
    return (
      !this.disposed &&
      !s.connecting &&
      s.trace.phase !== "LOADING" &&
      (s.active.status === "IDLE" ||
        TERMINAL.has(s.active.status) ||
        s.active.completion !== null) &&
      (s.editor.mode === "editing" || this.canEdit())
    );
  }
  loadDraft(code: string): boolean {
    if (!this.canLoadDraft()) return false;
    if (this.state.editor.mode === "run_source") this.edit();
    else this.emitLifecycle({ type: "reset" });
    this.setDraft(code);
    return true;
  }
  private update(patch: Partial<RunState>): void {
    if (this.disposed) return;
    this.state = Object.freeze({ ...this.state, ...patch });
    for (const listener of this.listeners) listener();
  }
  private active(patch: Partial<RunState["active"]>): void {
    this.update({ active: Object.freeze({ ...this.state.active, ...patch }) });
  }
  private trace(patch: Partial<RunState["trace"]>): void {
    this.update({ trace: Object.freeze({ ...this.state.trace, ...patch }) });
  }
  async refreshHealth(): Promise<void> {
    const generation = ++this.healthGeneration;
    this.update({ healthPhase: "LOADING", healthError: null });
    try {
      const response = await this.dependencies.fetch("/api/health");
      requireValid(response.status === 200, "Backend health is unavailable");
      const health = healthOf(await response.json());
      if (generation === this.healthGeneration)
        this.update({ health, healthPhase: "READY", healthError: null });
    } catch {
      if (generation === this.healthGeneration)
        this.update({
          health: null,
          healthPhase: "FAILED",
          healthError: "Не удалось проверить локальный backend.",
        });
    }
  }
  setDraft(code: string): void {
    if (this.state.editor.mode === "editing")
      this.update({
        editor: Object.freeze({ ...this.state.editor, draftCode: code }),
      });
  }
  canRun(): boolean {
    const s = this.state;
    return (
      s.healthPhase === "READY" &&
      s.health?.available === true &&
      s.editor.mode === "editing" &&
      !s.connecting &&
      !this.retained &&
      s.trace.phase !== "LOADING" &&
      (s.active.status === "IDLE" ||
        TERMINAL.has(s.active.status) ||
        s.active.completion !== null) &&
      utf8Bytes(s.editor.draftCode) <= s.health.limits.code_bytes
    );
  }
  canEdit(): boolean {
    return (
      this.state.editor.mode === "run_source" &&
      this.state.trace.phase !== "LOADING" &&
      (TERMINAL.has(this.state.active.status) ||
        this.state.active.completion !== null)
    );
  }
  edit(): void {
    if (!this.canEdit()) return;
    const source = this.state.editor.runSourceCode!;
    const abandonFailedTrace =
      this.state.trace.phase === "LOAD_FAILED" && this.retained;
    ++this.loadGeneration;
    this.dependencies.traceLoader?.dispose();
    // Release intent survives presentation reset; ownership ends only after 204.
    // releasePending + releasing/releaseError distinguish requested, in-flight and failed.
    this.update({
      editor: Object.freeze({
        draftCode: source,
        mode: "editing",
        runSourceCode: source,
      }),
      trace: Object.freeze({
        ...emptyTrace(),
        releasePending: this.state.trace.releasePending || abandonFailedTrace,
        releaseError: this.state.trace.releaseError,
      }),
    });
    this.emitLifecycle({ type: "reset" });
    if (abandonFailedTrace) void this.retryRelease();
  }
  run(): boolean {
    if (!this.canRun()) return false;
    const candidate = this.state.editor.draftCode;
    this.connection?.socket.close();
    this.connection = null;
    this.update({ connecting: true, transportError: null });
    try {
      const connection: Connection = {
        socket: this.dependencies.socket(),
        candidate,
        clientRequestId: this.dependencies.uuid(),
        inSeq: 1,
        outSeq: 1,
        ready: false,
        sent: false,
        accepted: false,
        finished: false,
        startingSeen: false,
        inputCounter: 0,
        inputSubmitted: null,
        consoleBytes: 0,
      };
      this.connection = connection;
      connection.socket.onMessage((data) => {
        if (this.connection === connection && !this.disposed)
          this.receive(data, connection);
      });
      connection.socket.onClose(() => {
        if (this.connection === connection && !connection.finished)
          this.lost(
            "Соединение с backend потеряно. Автоматический повтор запуска отключён.",
          );
      });
      connection.socket.onError(() => {
        if (this.connection === connection && !connection.finished)
          this.lost("Не удалось связаться с локальным backend.");
      });
      this.timer = setTimeout(() => {
        if (
          this.connection === connection &&
          !connection.accepted &&
          !connection.finished
        )
          this.lost(
            "Backend не подтвердил запуск. Повторная отправка не выполнялась.",
          );
      }, this.dependencies.handshakeTimeoutMs ?? 30000);
      return true;
    } catch {
      this.lost("Не удалось открыть соединение с локальным backend.");
      return false;
    }
  }
  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
  private lost(message: string): void {
    this.clearTimer();
    const connection = this.connection;
    this.connection = null;
    this.update({
      connecting: false,
      transportError: message,
      active: Object.freeze({
        ...this.state.active,
        transportLost:
          connection?.accepted === true || this.state.active.transportLost,
      }),
    });
    connection?.socket.close();
  }
  private send(
    connection: Connection,
    message: Omit<WsMessage, "seq" | "apiVersion">,
  ): void {
    const envelope = { apiVersion: 1, seq: connection.outSeq, ...message };
    validateWs(envelope);
    const raw = JSON.stringify(envelope);
    requireValid(
      utf8Bytes(raw) <= this.state.health!.limits.ws_message_bytes,
      "Message exceeds byte limit",
    );
    connection.socket.send(raw);
    ++connection.outSeq;
  }
  private receive(data: unknown, connection: Connection): void {
    try {
      requireValid(
        typeof data === "string" &&
          utf8Bytes(data) <= this.state.health!.limits.ws_message_bytes,
        "Invalid server message size",
      );
      const message = validateWs(JSON.parse(data));
      requireValid(
        Number.isSafeInteger(message.seq) && message.seq === connection.inSeq++,
        "Invalid server seq",
      );
      if (!connection.ready) {
        requireValid(
          message.type === "socket_ready",
          "socket_ready required first",
        );
        connection.ready = true;
        this.update({
          health: freezeJson({
            ...this.state.health!,
            limits: message.payload.limits,
          }),
        });
        requireValid(
          utf8Bytes(connection.candidate) <= message.payload.limits.code_bytes,
          "Source exceeds byte limit",
        );
        requireValid(!connection.sent, "start already sent");
        this.send(connection, {
          type: "start",
          runId: null,
          payload: {
            clientRequestId: connection.clientRequestId,
            code: connection.candidate,
          },
        });
        connection.sent = true;
        return;
      }
      if (!connection.accepted) {
        requireValid(
          message.runId === null || message.type === "run_accepted",
          "Unexpected runId",
        );
        if (message.type === "protocol_error") {
          this.lost(message.payload.code);
          return;
        }
        requireValid(
          message.type === "run_accepted" || message.type === "run_rejected",
          "Expected start result",
        );
        requireValid(
          message.payload.clientRequestId === connection.clientRequestId,
          "Wrong clientRequestId",
        );
        this.clearTimer();
        if (message.type === "run_rejected") {
          connection.finished = true;
          this.update({
            connecting: false,
            transportError: message.payload.code,
          });
          connection.socket.close();
          return;
        }
        requireValid(
          message.runId !== connection.clientRequestId,
          "RunId must be created by backend",
        );
        this.dependencies.traceLoader?.dispose();
        connection.accepted = true;
        this.publication = null;
        this.retained = false;
        ++this.loadGeneration;
        this.update({
          connecting: false,
          editor: Object.freeze({
            ...this.state.editor,
            mode: "run_source",
            runSourceCode: connection.candidate,
          }),
          active: Object.freeze({
            runId: message.runId,
            clientRequestId: connection.clientRequestId,
            sourceCode: connection.candidate,
            status: "STARTING",
            pendingInput: null,
            liveConsole: Object.freeze([]),
            completion: null,
            transportLost: false,
          }),
          trace: emptyTrace(),
        });
        this.emitLifecycle({
          type: "accepted",
          run: Object.freeze({
            runId: message.runId!,
            sourceCode: connection.candidate,
            acceptedAt: message.payload.acceptedAt,
          }),
        });
        return;
      }
      requireValid(
        message.runId === this.state.active.runId && !connection.finished,
        "Wrong runId or finished socket",
      );
      this.apply(message, connection);
    } catch {
      this.lost("Backend прислал некорректное сообщение протокола.");
    }
  }
  private apply(message: WsMessage, connection: Connection): void {
    const active = this.state.active;
    switch (message.type) {
      case "status": {
        const status = message.payload.status;
        if (
          status === "STARTING" &&
          active.status === "STARTING" &&
          !connection.startingSeen
        )
          connection.startingSeen = true;
        else
          requireValid(
            NEXT[active.status]?.includes(status) === true,
            "Invalid status transition",
          );
        this.active({
          status,
          pendingInput:
            status === "FINALIZING" || TERMINAL.has(status)
              ? null
              : active.pendingInput,
        });
        this.emitLifecycle({ type: "status", runId: active.runId!, status });
        break;
      }
      case "console": {
        requireValid(!TERMINAL.has(active.status), "Console after terminal");
        const entry = message.payload.entry;
        requireValid(
          entry.index === active.liveConsole.length,
          "Invalid Console index",
        );
        connection.consoleBytes += utf8Bytes(entry.text);
        requireValid(
          connection.consoleBytes <= this.state.health!.limits.console_bytes,
          "Console byte limit",
        );
        this.active({
          liveConsole: Object.freeze([
            ...active.liveConsole,
            freezeJson(entry),
          ]),
        });
        break;
      }
      case "input_request":
        requireValid(
          active.status === "WAITING_INPUT" &&
            active.pendingInput === null &&
            message.payload.requestId === `q${++connection.inputCounter}` &&
            message.payload.consoleOffset <= active.liveConsole.length,
          "Invalid input request lifecycle",
        );
        this.active({
          pendingInput: Object.freeze({
            ...message.payload,
            text: "",
            submitting: false,
            error: null,
          }),
        });
        break;
      case "input_accepted":
        requireValid(
          connection.inputSubmitted === message.payload.requestId,
          "Unexpected input ACK",
        );
        connection.inputSubmitted = null;
        this.active({
          pendingInput:
            active.pendingInput?.requestId === message.payload.requestId
              ? null
              : active.pendingInput,
        });
        break;
      case "input_rejected":
        requireValid(
          connection.inputSubmitted === message.payload.requestId,
          "Unexpected input rejection",
        );
        connection.inputSubmitted = null;
        if (active.pendingInput?.requestId === message.payload.requestId)
          this.active({
            pendingInput: Object.freeze({
              ...active.pendingInput,
              submitting: false,
              error: message.payload.code,
            }),
          });
        break;
      case "run_finished":
        requireValid(
          TERMINAL.has(active.status),
          "Publication before terminal",
        );
        requireValid(
          !message.payload.traceAvailable ||
            (message.payload.traceUrl === `/api/runs/${active.runId}/trace` &&
              message.payload.eventCount! <=
                this.state.health!.limits.max_events &&
              message.payload.traceBytes! <=
                this.state.health!.limits.trace_bytes),
          "Invalid trace URL or size",
        );
        connection.finished = true;
        this.publication = message.payload;
        this.retained = message.payload.traceAvailable;
        this.active({
          completion: freezeJson(message.payload.completion),
          pendingInput: null,
        });
        if (message.payload.traceAvailable) void this.download();
        else
          this.trace({
            phase: "EMPTY",
            loadError: `Результат без trace: ${message.payload.completion.reason}`,
          });
        break;
      case "protocol_error":
        this.lost(message.payload.code);
        break;
      default:
        throw new Error("Unexpected server message type");
    }
  }
  setInput(text: string): void {
    const pending = this.state.active.pendingInput;
    if (pending && !pending.submitting)
      this.active({
        pendingInput: Object.freeze({ ...pending, text, error: null }),
      });
  }
  submitInput(): boolean {
    const pending = this.state.active.pendingInput,
      connection = this.connection;
    if (
      !pending ||
      pending.submitting ||
      !connection ||
      connection.finished ||
      this.state.active.status !== "WAITING_INPUT"
    )
      return false;
    if (
      /[\r\n]/.test(pending.text) ||
      utf8Bytes(pending.text) > this.state.health!.limits.input_value_bytes
    ) {
      this.active({
        pendingInput: Object.freeze({
          ...pending,
          error: "Нужна одна строка в пределах UTF-8 byte limit.",
        }),
      });
      return false;
    }
    try {
      this.send(connection, {
        type: "input_response",
        runId: this.state.active.runId,
        payload: { requestId: pending.requestId, text: pending.text },
      });
      connection.inputSubmitted = pending.requestId;
      this.active({
        pendingInput: Object.freeze({
          ...pending,
          submitting: true,
          error: null,
        }),
      });
      return true;
    } catch {
      this.lost("Не удалось отправить ответ. Автоматический повтор отключён.");
      return false;
    }
  }
  canRetryDownload(): boolean {
    return (
      this.state.editor.mode === "run_source" &&
      this.state.active.runId !== null &&
      this.state.trace.phase !== "LOADING" &&
      this.state.trace.phase !== "READY" &&
      (this.publication?.traceAvailable === true ||
        this.state.active.transportLost)
    );
  }
  async download(): Promise<void> {
    if (!this.canRetryDownload()) return;
    const { runId, sourceCode } = this.state.active;
    const generation = ++this.loadGeneration;
    this.trace({
      phase: "LOADING",
      loadError: null,
      runId,
      handle: null,
      session: null,
      eventCount: null,
    });
    try {
      let core: TraceCore | null = null;
      let session: import("../execution/types.ts").LoadedTraceSession | null =
        null;
      let completion: Immutable<Completion>;
      if (this.dependencies.traceLoader) {
        session = await this.dependencies.traceLoader.load({
          traceUrl: `/api/runs/${runId}/trace`,
          expectedRunId: runId!,
          expectedSourceCode: sourceCode!,
          expectedEventCount: this.publication?.eventCount ?? null,
        });
        if (generation !== this.loadGeneration || this.disposed) {
          session.dispose();
          return;
        }
        requireValid(
          session.runId === runId &&
            session.sourceCode === sourceCode &&
            (this.publication?.eventCount == null ||
              session.totalSteps === this.publication.eventCount),
          "Trace session ownership mismatch",
        );
        completion = session.completion;
      } else {
        const response = await this.dependencies.fetch(
          `/api/runs/${runId}/trace`,
        );
        requireValid(response.status === 200, `GET trace: ${response.status}`);
        const parsed = await response.json();
        core = TraceCore.load(parsed);
        requireValid(
          core.runId === runId && core.sourceCode === sourceCode,
          "Trace belongs to another Run/source",
        );
        if (
          this.publication?.eventCount !== null &&
          this.publication?.eventCount !== undefined
        )
          requireValid(
            core.totalSteps === this.publication.eventCount,
            "Trace eventCount mismatch",
          );
        if (generation !== this.loadGeneration || this.disposed) return;
        this.retained = true;
        completion = freezeJson((parsed as TraceDocument).completion);
      }
      if (generation !== this.loadGeneration || this.disposed) {
        session?.dispose();
        return;
      }
      this.retained = true;
      this.update({
        editor: Object.freeze({
          ...this.state.editor,
          mode: "run_source",
          runSourceCode: sourceCode,
        }),
        active: Object.freeze({
          ...this.state.active,
          completion: freezeJson(completion as Completion),
          pendingInput: null,
        }),
        trace: Object.freeze({
          phase: "READY",
          handle: core,
          session,
          runId,
          eventCount: session?.totalSteps ?? core!.totalSteps,
          loadError: null,
          releasePending: true,
          releaseError: null,
        }),
      });
      await this.retryRelease();
    } catch {
      if (generation === this.loadGeneration) {
        this.dependencies.traceLoader?.dispose();
        this.trace({
          phase: "LOAD_FAILED",
          loadError:
            "Не удалось загрузить или проверить trace. Можно повторить GET без запуска Python.",
        });
      }
    }
  }
  async retryRelease(): Promise<void> {
    if (
      !this.state.trace.releasePending ||
      this.releasing ||
      this.state.active.runId === null
    )
      return;
    this.releasing = true;
    const runId = this.state.active.runId;
    try {
      const response = await this.dependencies.fetch(
        `/api/runs/${runId}/release`,
        { method: "POST" },
      );
      requireValid(response.status === 204, "Release failed");
      this.retained = false;
      this.trace({ releasePending: false, releaseError: null });
    } catch {
      this.trace({
        releasePending: true,
        releaseError:
          "Не удалось освободить результат предыдущего запуска. Повторите освобождение результата.",
      });
    } finally {
      this.releasing = false;
    }
  }
  dispose(): void {
    this.emitLifecycle({ type: "dispose" });
    this.lifecycleListeners.clear();
    this.disposed = true;
    this.dependencies.traceLoader?.dispose();
    ++this.loadGeneration;
    this.clearTimer();
    this.connection?.socket.close();
    this.connection = null;
    this.listeners.clear();
    for (const listener of this.disposalListeners) listener();
    this.disposalListeners.clear();
  }
}
