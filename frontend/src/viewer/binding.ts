import type { RunController } from "../runs/controller.ts";
import type { ViewerController } from "./controller.ts";

/** Connects ownership only; selection has no transport capability. */
export function bindRunViewer(
  run: RunController,
  viewer: ViewerController,
): () => void {
  const sync = () => {
    const trace = run.getSnapshot().trace;
    const owner = trace.session ?? trace.handle;
    if (!run.isDisposed && trace.phase === "READY" && owner)
      viewer.attach(owner);
    else viewer.detach();
  };
  const unsubscribe = run.subscribe(sync);
  const unsubscribeDispose = run.onDispose(() => {
    unsubscribe();
    viewer.detach();
  });
  sync();
  return () => {
    unsubscribe();
    unsubscribeDispose();
    viewer.detach();
  };
}
