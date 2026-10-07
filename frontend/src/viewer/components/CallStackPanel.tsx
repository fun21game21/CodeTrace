import type { SelectedStepView } from "../../trace/index.ts";
import type { ViewerController } from "../controller.ts";

export function CallStackPanel({
  view,
  controller,
}: {
  view: SelectedStepView;
  controller: ViewerController;
}) {
  return (
    <section className="stack-panel" aria-labelledby="stack-title">
      <h2 id="stack-title">Стек вызовов</h2>
      <button
        aria-pressed={view.selectedFrameId === null}
        onClick={() => controller.selectFrame(null)}
      >
        Globals
      </button>
      <ol className="stack-list">
        {view.callStack.map((frame) => (
          <li key={frame.id}>
            <button
              aria-pressed={view.selectedFrameId === frame.id}
              onClick={() => controller.selectFrame(frame.id)}
            >
              <span>
                {frame.name} <code>{frame.id}</code>
              </span>
              <small>
                {frame.kind}
                {frame.callSite?.lineNumber
                  ? " · вызов на строке " + frame.callSite.lineNumber
                  : ""}
                {view.event.frameId === frame.id ? " · текущее событие" : ""}
              </small>
            </button>
          </li>
        ))}
      </ol>
      {view.callStack.length === 0 && (
        <p className="muted">Активных кадров нет.</p>
      )}
    </section>
  );
}
