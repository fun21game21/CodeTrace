import { RunController, utf8Bytes } from "../runs/controller.ts";
import type { RunState } from "../runs/types.ts";
const STATUS: Record<string, string> = {
  IDLE: "Готов к работе",
  STARTING: "Запуск…",
  RUNNING: "Выполняется",
  WAITING_INPUT: "Ожидание ввода",
  FINALIZING: "Завершение",
  SUCCEEDED: "Успешно",
  FAILED: "Ошибка",
  LIMIT_EXCEEDED: "Лимит",
  INTERRUPTED: "Прервано",
};

interface Props {
  controller: RunController;
  state: RunState;
}
export function RunStatus({ controller, state }: Props) {
  return (
    <section className="status-panel" aria-labelledby="status-title">
      <h2 id="status-title">Статус</h2>
      <p className="run-status" role="status">
        {state.active.transportLost
          ? "Связь потеряна"
          : STATUS[state.active.status]}
      </p>
      <p className="muted">
        {state.healthPhase === "LOADING"
          ? "Проверка backend…"
          : state.health?.available
            ? "Backend готов"
            : `Backend недоступен: ${state.health?.reason ?? state.healthError ?? "нет ответа"}`}
      </p>
      <button
        className="text-button"
        type="button"
        onClick={() => void controller.refreshHealth()}
        disabled={state.healthPhase === "LOADING"}
      >
        Проверить backend
      </button>
      {state.active.completion && (
        <p className="completion">
          {state.active.completion.outcome} · {state.active.completion.reason}
        </p>
      )}
      {state.transportError && (
        <p className="error" role="alert">
          {state.transportError}
        </p>
      )}
    </section>
  );
}
export function RunInput({ controller, state }: Props) {
  const pending = state.active.pendingInput;
  return (
    <>
      {" "}
      {pending &&
        state.active.status === "WAITING_INPUT" &&
        !state.active.transportLost && (
          <section className="input-panel" aria-labelledby="input-title">
            <h2 id="input-title">Ввод для input()</h2>
            <p className="prompt">
              {pending.prompt || "Программа ожидает строку."}
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                controller.submitInput();
              }}
            >
              <label htmlFor="python-input">Ответ</label>
              <input
                id="python-input"
                autoFocus
                autoComplete="off"
                value={pending.text}
                disabled={pending.submitting}
                onChange={(event) => controller.setInput(event.target.value)}
              />
              <div className="input-footer">
                <small>
                  {utf8Bytes(pending.text)} /{" "}
                  {state.health?.limits.input_value_bytes} UTF-8 bytes
                </small>
                <button disabled={pending.submitting}>
                  {pending.submitting ? "Отправка…" : "Отправить"}
                </button>
              </div>
              <small className="muted">
                Пустая строка допустима. Ожидание до{" "}
                {new Date(pending.deadlineAt).toLocaleTimeString()}.
              </small>
              {pending.error && (
                <p className="error" role="alert">
                  {pending.error}
                </p>
              )}
            </form>
          </section>
        )}
    </>
  );
}
export function RunActions({ controller, state }: Props) {
  const source =
    state.editor.mode === "run_source"
      ? state.editor.runSourceCode!
      : state.editor.draftCode;
  return (
    <footer className="actions">
      <div className="run-actions">
        <button
          className="primary"
          disabled={!controller.canRun()}
          onClick={() => controller.run()}
        >
          {state.connecting ? "Соединение…" : "Запустить"}
        </button>
        <button
          disabled={!controller.canEdit()}
          onClick={() => controller.edit()}
        >
          Редактировать
        </button>
        <small className="muted">
          {utf8Bytes(source)} / {state.health?.limits.code_bytes ?? "—"} UTF-8
          bytes
        </small>
      </div>
      <div className="trace-status" aria-live="polite">
        {state.trace.phase === "LOADING" && (
          <span>Загрузка и проверка trace…</span>
        )}
        {state.trace.phase === "READY" && (
          <strong>TRACE READY · {state.trace.eventCount} событий</strong>
        )}
        {state.trace.loadError && (
          <p className="error">{state.trace.loadError}</p>
        )}
        {controller.canRetryDownload() && (
          <button onClick={() => void controller.download()}>
            {state.active.transportLost
              ? "Проверить результат"
              : "Повторить загрузку результата"}
          </button>
        )}
        {state.trace.releaseError && (
          <p className="error">{state.trace.releaseError}</p>
        )}
        {state.trace.releasePending && (
          <button onClick={() => void controller.retryRelease()}>
            Повторить освобождение результата
          </button>
        )}
      </div>
    </footer>
  );
}
export function LiveConsole({ state }: { state: RunState }) {
  const ready = state.trace.phase === "READY";
  return (
    <section className="console-panel" aria-labelledby="console-title">
      <h2 id="console-title">
        {ready ? "Завершённый live output" : "Live Console"}
      </h2>
      {ready ? (
        <p className="muted">Подготовка пошагового Console…</p>
      ) : state.active.liveConsole.length === 0 ? (
        <p className="console-empty">Вывод программы появится здесь.</p>
      ) : (
        <pre aria-label="Live Console output">
          {state.active.liveConsole.map((entry) => (
            <span key={entry.index} className={"console-" + entry.kind}>
              {entry.text}
            </span>
          ))}
        </pre>
      )}
    </section>
  );
}
