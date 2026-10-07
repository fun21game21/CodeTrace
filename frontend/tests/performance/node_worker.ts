import { parentPort } from "node:worker_threads";
import { createWorkerRuntime } from "../../src/execution/worker_runtime.ts";
import type { Command } from "../../src/execution/protocol.ts";
const port = parentPort!;
port.on(
  "message",
  createWorkerRuntime((reply) => port.postMessage(reply)) as (
    message: Command,
  ) => void,
);
