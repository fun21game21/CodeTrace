import type { HistorySnapshot } from "../types.ts";
import { codePreview, STATUS_LABELS } from "../values.ts";
export function HistoryPanel({
  snapshot,
  canOpen,
  onOpen,
  onDelete,
}: {
  snapshot: HistorySnapshot;
  canOpen: boolean;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <section className="history-panel" aria-labelledby="history-title">
      <div className="history-heading">
        <h2 id="history-title">История исходников</h2>
        <span className="muted">{snapshot.entries.length} / 100</span>
      </div>
      <p className="muted">
        Локально в этом браузере. Открытие возвращает код в редактор.
      </p>
      {snapshot.warning && (
        <p role="status" className="error">
          {snapshot.warning}
        </p>
      )}
      {snapshot.phase === "LOADING" && (
        <p role="status" className="muted">
          Загрузка истории…
        </p>
      )}
      {snapshot.phase === "READY" && snapshot.entries.length === 0 && (
        <p className="muted">Принятые запуски появятся здесь.</p>
      )}
      <ol className="history-list" aria-label="Версии исходников">
        {snapshot.entries.map((entry) => (
          <li key={entry.id} data-history-id={entry.id}>
            <div className="history-meta">
              <time dateTime={entry.lastRunAt}>
                {new Date(entry.lastRunAt).toLocaleString("ru-RU")}
              </time>
              <span data-history-status={entry.status}>
                {STATUS_LABELS[entry.status]}
              </span>
            </div>
            <pre>{codePreview(entry.code) || "Пустой исходник"}</pre>
            <div className="history-actions">
              <button
                type="button"
                disabled={!canOpen || snapshot.phase !== "READY"}
                onClick={() => onOpen(entry.id)}
              >
                Открыть
              </button>
              <button
                type="button"
                disabled={snapshot.phase !== "READY"}
                onClick={() => onDelete(entry.id)}
              >
                Удалить
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
