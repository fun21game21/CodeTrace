import type {
  Completion,
  TraceDocument,
} from "../../../contracts/typescript/index.ts";
import { TraceCore } from "../trace/index.ts";
import type { Command, Reply } from "./protocol.ts";
import type { TraceLoadRequest } from "./types.ts";
/** Same runtime is exercised by the browser Worker and isolated worker-thread tests. */
export function createWorkerRuntime(
  send: (reply: Reply) => void,
  fetchTrace: typeof fetch = fetch,
) {
  let core: TraceCore | null = null,
    sessionId: string | null = null,
    disposed = false,
    loading = false;
  let abort: AbortController | null = null;
  let measuring = false;
  const now = () => (measuring ? performance.now() : 0);
  const fail = (
    session: string,
    requestId: number | null,
    code: "LOAD_FAILED" | "SELECT_FAILED" | "PROTOCOL_ERROR",
  ) =>
    send({
      type: "error",
      sessionId: session,
      requestId,
      code,
      message:
        code === "LOAD_FAILED"
          ? "Не удалось загрузить trace."
          : "Не удалось выбрать шаг.",
    });
  async function load(request: TraceLoadRequest, identity: string) {
    abort = new AbortController();
    const timer = setTimeout(() => abort?.abort(), 20000);
    try {
      const fetchStart = now();
      const response = await fetchTrace(request.traceUrl, {
        cache: "no-store",
        signal: abort.signal,
      });
      if (response.status !== 200) throw Error("HTTP");
      const raw = await response.text(),
        fetchMs = now() - fetchStart;
      const parseStart = now();
      const parsed: unknown = JSON.parse(raw),
        jsonParseMs = now() - parseStart;
      const coreStart = now();
      const loaded = TraceCore.load(parsed),
        traceCoreLoadMs = now() - coreStart;
      if (
        loaded.runId !== request.expectedRunId ||
        loaded.sourceCode !== request.expectedSourceCode ||
        (request.expectedEventCount !== null &&
          loaded.totalSteps !== request.expectedEventCount)
      )
        throw Error("Ownership");
      if (disposed || sessionId !== identity) return;
      core = loaded;
      const completion = (parsed as TraceDocument).completion as Completion;
      send({
        type: "loaded",
        sessionId: identity,
        runId: core.runId,
        sourceCode: core.sourceCode,
        totalSteps: core.totalSteps,
        validated: true,
        completion,
        ...(measuring
          ? { timing: { fetchMs, jsonParseMs, traceCoreLoadMs } }
          : {}),
      });
    } catch {
      if (!disposed && sessionId === identity)
        fail(identity, null, "LOAD_FAILED");
    } finally {
      clearTimeout(timer);
      loading = false;
      abort = null;
    }
  }
  return (command: Command): void => {
    if (disposed) return;
    if (command.type === "load") {
      if (sessionId !== null || loading) {
        fail(command.sessionId, null, "PROTOCOL_ERROR");
        return;
      }
      measuring = command.measure === true;
      sessionId = command.sessionId;
      loading = true;
      void load(command.request, command.sessionId);
      return;
    }
    if (command.sessionId !== sessionId) return;
    if (command.type === "dispose") {
      disposed = true;
      abort?.abort();
      core = null;
      return;
    }
    try {
      if (!core) throw Error("Not loaded");
      const start = now(),
        view = core.selectStep(command.request);
      send({
        type: "selected",
        sessionId: command.sessionId,
        requestId: command.request.requestId,
        ...(measuring ? { selectionMs: now() - start } : {}),
        view,
      });
    } catch {
      fail(command.sessionId, command.request.requestId, "SELECT_FAILED");
    }
  };
}
