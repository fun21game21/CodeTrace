import assert from "node:assert/strict";
import { RunController } from "../../src/runs/controller.ts";
import { TraceCore } from "../../src/trace/index.ts";
import type { HttpResult } from "../../src/runs/types.ts";
import type { LoadedTraceSession } from "../../src/execution/types.ts";
import {
  Socket,
  REQUEST,
  health,
  validTrace,
  finish,
  settled,
} from "./helpers.ts";

export const FA01_CODE = "  # exact\r\nx = '🙂'\r\n";
export function recoveryHarness() {
  const sockets: Socket[] = [],
    posts: string[] = [];
  let failLoad = true,
    loads = 0,
    liveWorkers = 0;
  let release: () => Promise<HttpResult> = async () => ({
    status: 204,
    json: async () => null,
  });
  let session: LoadedTraceSession | null = null;
  const dispose = () => {
    session?.dispose();
    session = null;
    liveWorkers = 0;
  };
  const run = new RunController(
    {
      uuid: () => REQUEST,
      socket: () => {
        const s = new Socket();
        sockets.push(s);
        return s;
      },
      fetch: async (url, options) => {
        if (options) {
          posts.push(url);
          return release();
        }
        assert.equal(url, "/api/health", "main must not fetch trace");
        return { status: 200, json: async () => health() };
      },
      traceLoader: {
        async load(request) {
          loads++;
          liveWorkers++;
          if (failLoad) throw Error("trace GET failed");
          const trace = validTrace(request.expectedSourceCode),
            core = TraceCore.load(trace);
          session = {
            identity: "test-worker",
            runId: trace.runId,
            sourceCode: trace.sourceCode,
            totalSteps: core.totalSteps,
            completion: trace.completion,
            executor: { select: async (r) => core.selectStep(r) },
            dispose: () => {
              liveWorkers = 0;
            },
          };
          return session;
        },
        dispose,
      },
    },
    FA01_CODE,
  );
  return {
    run,
    sockets,
    posts,
    counts: () => ({ loads, liveWorkers }),
    setLoadFailure: (value: boolean) => {
      failLoad = value;
    },
    setRelease: (handler: typeof release) => {
      release = handler;
    },
    async complete(traceAvailable = true) {
      await run.refreshHealth();
      assert(run.run());
      const socket = sockets.at(-1)!;
      socket.ready();
      socket.accept();
      socket.status("STARTING");
      finish(socket, run.getSnapshot().active.sourceCode!, traceAvailable);
      await settled();
      return socket;
    },
  };
}
