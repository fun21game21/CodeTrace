import type { SelectedStepView } from "../../trace/index.ts";

export function ErrorsPanel({ view }: { view: SelectedStepView }) {
  const diagnostic = view.eventDiagnostic ?? view.completion?.primaryDiagnostic;
  return (
    <section className="errors-panel" aria-labelledby="errors-title">
      <h2 id="errors-title">Errors</h2>
      {diagnostic ? (
        <div className="diagnostic">
          <p>
            <strong>{diagnostic.typeName ?? diagnostic.category}</strong> ·{" "}
            {diagnostic.code}
          </p>
          <p className="diagnostic-text">{diagnostic.message}</p>
          {diagnostic.source?.lineNumber !== null &&
            diagnostic.source?.lineNumber !== undefined && (
              <p>
                Строка {diagnostic.source.lineNumber}
                {diagnostic.columnNumber === null
                  ? ""
                  : ", столбец " + diagnostic.columnNumber}
              </p>
            )}
          {diagnostic.traceback.length > 0 && (
            <ol>
              {diagnostic.traceback.map((frame, index) => (
                <li key={index}>
                  {frame.name} · строка{" "}
                  {frame.source.lineNumber ?? "неизвестна"}
                </li>
              ))}
            </ol>
          )}
          {diagnostic.omittedTracebackFrames > 0 && (
            <p>Ещё кадров: {diagnostic.omittedTracebackFrames}</p>
          )}
          {view.eventDiagnostic && (
            <p className="muted">
              Наблюдаемое исключение; оно могло быть обработано программой.
            </p>
          )}
        </div>
      ) : (
        <p className="muted">На этом шаге нет диагностики.</p>
      )}
      {view.completion && (
        <div className="step-completion">
          <p>
            {view.completion.outcome} · {view.completion.reason}
          </p>
          <p>
            Trace {view.completion.traceComplete ? "complete" : "partial"} ·
            state {view.completion.stateAccuracy}
          </p>
          {view.completion.stateAccuracy !== "final" && (
            <p className="muted">
              {view.completion.stateAccuracy === "last_observed"
                ? "Переменные — последнее наблюдение. Вывод мог появиться после него."
                : "Состояние Python недоступно."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
