import { WorkerTraceLoader } from "./worker_session.ts";
/** Measurements are opt-in for the explicit benchmark page only. */
export const browserTraceLoader = (measure = false) =>
  new WorkerTraceLoader(
    () =>
      new Worker(new URL("./trace.worker.ts", import.meta.url), {
        type: "module",
        name: "codetrace-trace",
      }),
    () => crypto.randomUUID(),
    measure,
  );
