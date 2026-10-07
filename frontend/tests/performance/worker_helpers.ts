import { Worker } from "node:worker_threads";
import { createServer } from "node:http";
import { once } from "node:events";
import type {
  Command,
  Reply,
  WorkerPort,
} from "../../src/execution/protocol.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import type { TraceDocument } from "../../../contracts/typescript/index.ts";
export async function workerHarness() {
  let trace: unknown = null,
    status = 200,
    body: string | null = null,
    gets = 0,
    created = 0,
    terminated = 0,
    live = 0,
    peak = 0;
  const commands: Command[] = [],
    workers: Worker[] = [];
  const server = createServer((_request, response) => {
    gets++;
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(body ?? JSON.stringify(trace));
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw Error("No address");
  const url = `http://127.0.0.1:${address.port}/trace`;
  const factory = (): WorkerPort => {
    const worker = new Worker(new URL("./node_worker.ts", import.meta.url));
    workers.push(worker);
    created++;
    live++;
    peak = Math.max(peak, live);
    let dead = false;
    return {
      postMessage: (message) => {
        commands.push(structuredClone(message));
        worker.postMessage(message);
      },
      addEventListener(
        type: string,
        handler: ((e: { data: Reply }) => void) | (() => void),
      ) {
        if (type === "message")
          worker.on(type, (data) =>
            (handler as (e: { data: Reply }) => void)({ data }),
          );
        else worker.on(type, handler as () => void);
      },
      terminate() {
        if (!dead) {
          dead = true;
          live--;
          terminated++;
          void worker.terminate();
        }
      },
    };
  };
  const loader = new WorkerTraceLoader(factory);
  return {
    loader,
    commands,
    counts: () => ({ gets, created, terminated, live, peak }),
    setTrace(value: unknown) {
      trace = value;
      status = 200;
      body = null;
    },
    httpFailure() {
      status = 500;
    },
    corrupt() {
      body = "{";
    },
    request(t: TraceDocument) {
      return {
        traceUrl: url,
        expectedRunId: t.runId,
        expectedSourceCode: t.sourceCode,
        expectedEventCount: t.events.length,
      };
    },
    async close() {
      loader.dispose();
      await Promise.all(workers.map((w) => w.terminate()));
      await new Promise<void>((resolve, reject) =>
        server.close((e) => (e ? reject(e) : resolve())),
      );
    },
  };
}
export class FakeWorker implements WorkerPort {
  commands: Command[] = [];
  terminated = false;
  message: (e: { data: Reply }) => void = () => {};
  error: () => void = () => {};
  postMessage(message: Command) {
    this.commands.push(message);
  }
  addEventListener(
    type: string,
    handler: ((event: { data: Reply }) => void) | (() => void),
  ) {
    if (type === "message")
      this.message = handler as (event: { data: Reply }) => void;
    else this.error = handler as () => void;
  }
  terminate() {
    this.terminated = true;
  }
  reply(reply: Reply) {
    this.message({ data: structuredClone(reply) });
  }
}
