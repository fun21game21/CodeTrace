import type { RunStatus } from "../../../contracts/typescript/index.ts";
import type { HistoryEntry } from "./types.ts";
export const ACTIVE_STATUSES: ReadonlySet<RunStatus> = new Set([
  "STARTING",
  "RUNNING",
  "WAITING_INPUT",
  "FINALIZING",
]);
export const STATUS_LABELS: Readonly<Record<RunStatus, string>> = Object.freeze(
  {
    STARTING: "Запуск",
    RUNNING: "Выполняется",
    WAITING_INPUT: "Ожидание ввода",
    FINALIZING: "Завершение",
    SUCCEEDED: "Успешно",
    FAILED: "Ошибка",
    LIMIT_EXCEEDED: "Лимит",
    INTERRUPTED: "Прервано",
  },
);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const utc = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;
export function validTimestamp(value: unknown): value is string {
  if (typeof value !== "string" || !utc.test(value)) return false;
  const date = new Date(value);
  return (
    Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 19) === value.slice(0, 19)
  );
}
export function validStatus(value: unknown): value is RunStatus {
  return typeof value === "string" && Object.hasOwn(STATUS_LABELS, value);
}
export function historyEntry(value: unknown): HistoryEntry {
  if (
    !value ||
    typeof value !== "object" ||
    Object.getPrototypeOf(value) !== Object.prototype
  )
    throw new Error("Повреждена запись истории.");
  const r = value as Record<string, unknown>;
  if (
    Object.keys(r).sort().join(",") !== "code,createdAt,id,lastRunAt,status" ||
    typeof r.id !== "string" ||
    !uuid.test(r.id) ||
    typeof r.code !== "string" ||
    !validTimestamp(r.createdAt) ||
    !validTimestamp(r.lastRunAt) ||
    !validStatus(r.status)
  )
    throw new Error("Повреждена запись истории.");
  return Object.freeze({
    id: r.id,
    code: r.code,
    createdAt: r.createdAt,
    lastRunAt: r.lastRunAt,
    status: r.status,
  });
}
const timestampKey = (s: string) =>
  s.slice(0, 19) + s.slice(19, -1).replace(/^\./, "").padEnd(9, "0");
const compareText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
/** UTC precision preserved through nine digits; UUID ascending breaks ties. */
export function compareHistory(a: HistoryEntry, b: HistoryEntry): number {
  return (
    compareText(timestampKey(b.lastRunAt), timestampKey(a.lastRunAt)) ||
    compareText(timestampKey(b.createdAt), timestampKey(a.createdAt)) ||
    compareText(a.id, b.id)
  );
}
export function codePreview(code: string): string {
  const bounded = code.slice(0, 160);
  const lines = bounded.split(/\r\n|\r|\n/);
  const prefix = lines.slice(0, 3).join("\n");
  return code.length > 160 || lines.length > 3 ? prefix + "…" : prefix;
}
