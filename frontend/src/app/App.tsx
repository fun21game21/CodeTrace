import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ComponentType } from "react";
import { MonacoEditor } from "../editor/MonacoEditor.tsx";
import type { EditorProps } from "../editor/MonacoEditor.tsx";
import type { RunController } from "../runs/controller.ts";
import { ViewerController } from "../viewer/controller.ts";
import { bindRunViewer } from "../viewer/binding.ts";
import { ViewerControls } from "../viewer/components/ViewerControls.tsx";
import { VariablesPanel } from "../viewer/components/VariablesPanel.tsx";
import { CallStackPanel } from "../viewer/components/CallStackPanel.tsx";
import { StepConsole } from "../viewer/components/StepConsole.tsx";
import { ErrorsPanel } from "../viewer/components/ErrorsPanel.tsx";
import { EventPanel } from "../viewer/components/EventPanel.tsx";
import { VisualizerPanel } from "../visualizers/components/VisualizerPanel.tsx";
import type { HistoryController } from "../history/controller.ts";
import { openHistoryEntry } from "../history/binding.ts";
import { HistoryPanel } from "../history/components/HistoryPanel.tsx";
import { ExamplesController } from "../examples/controller.ts";
import { ExamplesMenu } from "../examples/ExamplesMenu.tsx";
import { RunStatus, RunInput, RunActions, LiveConsole } from "./RunPanels.tsx";

export function App({
  controller,
  viewer: injectedViewer,
  Editor = MonacoEditor,
  history,
  examples: injectedExamples,
}: {
  controller: RunController;
  history?: HistoryController;
  examples?: ExamplesController;
  viewer?: ViewerController;
  Editor?: ComponentType<EditorProps>;
}) {
  const [ownedViewer] = useState(() => new ViewerController());
  const viewer = injectedViewer ?? ownedViewer;
  const examples = useMemo(
    () => injectedExamples ?? new ExamplesController(controller, viewer),
    [controller, viewer, injectedExamples],
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const snapshot = useSyncExternalStore(
    viewer.subscribe,
    viewer.getSnapshot,
    viewer.getSnapshot,
  );
  useEffect(() => {
    void controller.refreshHealth();
    const unbind = bindRunViewer(controller, viewer);
    const unbindExamples = examples.connect();
    return () => {
      unbindExamples();
      unbind();
    };
  }, [controller, viewer, examples]);
  const ready = state.trace.phase === "READY";
  // A new Run/owner change hides the old instance before the lifecycle effect runs.
  const current =
    ready && viewer.isAttached(state.trace.session ?? state.trace.handle);
  const view = current ? snapshot.committedView : null;
  const source =
    state.editor.mode === "run_source"
      ? state.editor.runSourceCode!
      : state.editor.draftCode;
  return (
    <div className="workspace">
      <header className="app-header">
        <div>
          <h1>
            CodeTrace<span>Python execution</span>
          </h1>
          <span className="local-label">Локально · CPython 3.13.16</span>
        </div>
        <RunStatus state={state} controller={controller} />
      </header>
      <main className="workbench">
        <section className="editor-panel" aria-labelledby="code-title">
          <div className="panel-heading">
            <h2 id="code-title">Python code</h2>
            <span>
              {state.editor.mode === "run_source"
                ? "Исходник запуска · только чтение"
                : "Редактор"}
            </span>
          </div>
          <ExamplesMenu
            disabled={!controller.canLoadDraft()}
            onSelect={(id) => {
              examples.select(id);
            }}
          />
          <Editor
            value={source}
            readOnly={state.editor.mode === "run_source"}
            highlight={view?.highlight ?? null}
            onChange={(code) => controller.setDraft(code)}
          />
        </section>
        <div className="center-panel">
          <EventPanel view={view} />
          {view && (
            <VisualizerPanel
              view={view}
              config={snapshot.visualizerConfig}
              pending={snapshot.pendingStep !== null}
              onApply={(c) => viewer.setVisualizerConfig(c)}
              onFrame={(id) => viewer.selectFrame(id)}
            />
          )}
        </div>
        <aside className="run-panel">
          {view ? (
            <>
              <VariablesPanel view={view} />
              <CallStackPanel view={view} controller={viewer} />
            </>
          ) : (
            <section className="viewer-empty">
              <h2>Переменные / Стек</h2>
              <p className="muted">
                {ready
                  ? "Подготовка первого шага…"
                  : "Доступны после загрузки trace."}
              </p>
            </section>
          )}
          {!ready && <RunInput state={state} controller={controller} />}
        </aside>
      </main>
      <RunActions state={state} controller={controller} />
      {current && <ViewerControls snapshot={snapshot} controller={viewer} />}
      <div className="output-panels">
        {view ? (
          <>
            <StepConsole view={view} />
            <ErrorsPanel view={view} />
          </>
        ) : (
          <LiveConsole state={state} />
        )}
      </div>
      {history && <AppHistory history={history} controller={controller} />}
    </div>
  );
}

function AppHistory({
  history,
  controller,
}: {
  history: HistoryController;
  controller: RunController;
}) {
  const snapshot = useSyncExternalStore(
    history.subscribe,
    history.getSnapshot,
    history.getSnapshot,
  );
  return (
    <HistoryPanel
      snapshot={snapshot}
      canOpen={controller.canLoadDraft()}
      onOpen={(id) => {
        openHistoryEntry(controller, history, id);
      }}
      onDelete={(id) => {
        void history.delete(id);
      }}
    />
  );
}
