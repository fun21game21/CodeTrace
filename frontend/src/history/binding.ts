import type { RunController } from "../runs/controller.ts";
import type { HistoryController } from "./controller.ts";
export function bindRunHistory(
  run: RunController,
  history: HistoryController,
): () => void {
  const unsubscribe = run.subscribeLifecycle((event) => {
    if (event.type === "accepted") history.accepted(event.run);
    else if (event.type === "status") history.status(event.runId, event.status);
    else if (event.type === "reset") history.reset();
    else history.dispose();
  });
  return unsubscribe;
}
export function openHistoryEntry(
  run: RunController,
  history: HistoryController,
  id: string,
): boolean {
  const entry = history.entry(id);
  return entry ? run.loadDraft(entry.code) : false;
}
