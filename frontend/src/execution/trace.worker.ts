import type { Command } from "./protocol.ts";
import { createWorkerRuntime } from "./worker_runtime.ts";
const receive = createWorkerRuntime((reply) => self.postMessage(reply));
self.addEventListener("message", (event: MessageEvent<Command>) =>
  receive(event.data),
);
