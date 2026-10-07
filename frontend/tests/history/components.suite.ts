import assert from "node:assert/strict";
import test from "node:test";
import { createElement, isValidElement } from "react";
import type { ReactNode, ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { HistoryPanel } from "../../src/history/components/HistoryPanel.tsx";
import { codePreview, STATUS_LABELS } from "../../src/history/values.ts";
import type { HistoryEntry, HistorySnapshot } from "../../src/history/types.ts";
import {
  HistoryController,
  bindRunHistory,
  openHistoryEntry,
} from "../../src/history/index.ts";
import { App } from "../../src/app/App.tsx";
import { setup, finish } from "../runs/helpers.ts";
import { DelayedStore, T1, drain } from "./helpers.ts";
type Element = ReactElement<Record<string, unknown>>;
function nodes(node: ReactNode): Element[] {
  if (Array.isArray(node))
    return node.flatMap((item) => nodes(item as ReactNode));
  if (!isValidElement(node)) return [];
  const e = node as Element;
  return [e, ...nodes(e.props.children as ReactNode)];
}
const entry = (code = "x = 1\n"): HistoryEntry => ({
  id: crypto.randomUUID(),
  code,
  createdAt: T1,
  lastRunAt: T1,
  status: "SUCCEEDED",
});
const snapshot = (
  entries: readonly HistoryEntry[] = [],
  phase: HistorySnapshot["phase"] = "READY",
  warning: string | null = null,
): HistorySnapshot => ({ entries, phase, warning });
const panel = (
  s: HistorySnapshot,
  canOpen = true,
  onOpen: (id: string) => void = () => {},
  onDelete: (id: string) => void = () => {},
) => HistoryPanel({ snapshot: s, canOpen, onOpen, onDelete });
for (const status of Object.keys(
  STATUS_LABELS,
) as (keyof typeof STATUS_LABELS)[])
  test("History Russian status label " + status, () => {
    const e = { ...entry(), status },
      html = renderToStaticMarkup(panel(snapshot([e])));
    assert(html.includes(STATUS_LABELS[status]));
    assert(html.includes('data-history-status="' + status + '"'));
  });
test("empty history explains accepted Run trigger", () => {
  const html = renderToStaticMarkup(panel(snapshot()));
  assert(html.includes("Принятые запуски появятся здесь"));
  assert(html.includes("0 / 100"));
});
test("loading state is visible without fake empty success", () => {
  const html = renderToStaticMarkup(panel(snapshot([], "LOADING")));
  assert(html.includes("Загрузка истории"));
  assert(!html.includes("Принятые запуски появятся"));
});
test("unavailable warning is announced and no database success claimed", () => {
  const html = renderToStaticMarkup(
    panel(snapshot([], "UNAVAILABLE", "IndexedDB denied")),
  );
  assert(html.includes('role="status"'));
  assert(html.includes("IndexedDB denied"));
});
test("HTML-looking source renders escaped plain text", () => {
  const html = renderToStaticMarkup(
    panel(
      snapshot([
        entry("<script>alert(1)</script>\n<img src=x onerror=alert(2)>\n"),
      ]),
    ),
  );
  assert(html.includes("&lt;script&gt;"));
  assert(!html.includes("<script>"));
  assert(!html.includes("<img"));
});
test("preview bounded to three lines/160 chars, full source untouched", () => {
  const code = "x\n".repeat(200),
    e = entry(code),
    html = renderToStaticMarkup(panel(snapshot([e])));
  assert.equal(e.code, code);
  assert.equal(codePreview(code), "x\nx\nx…");
  assert(!html.includes(code));
  assert.equal(codePreview("x".repeat(65536)).length, 161);
});
test("Unicode preview does not normalize or trim meaningful whitespace", () => {
  const code = "  e\u0301 \n";
  assert.equal(codePreview(code), code);
});
test("Open and Delete are distinct buttons and do not bubble through a row action", () => {
  const e = entry();
  let opened: string | null = null,
    deleted: string | null = null;
  const elements = nodes(
    panel(
      snapshot([e]),
      true,
      (id) => {
        opened = id;
      },
      (id) => {
        deleted = id;
      },
    ),
  );
  const buttons = elements.filter((n) => n.type === "button");
  assert.equal(buttons.length, 2);
  assert(!elements.find((n) => n.type === "li")!.props.onClick);
  (buttons[1]!.props.onClick as () => void)();
  assert.equal(deleted, e.id);
  assert.equal(opened, null);
  (buttons[0]!.props.onClick as () => void)();
  assert.equal(opened, e.id);
});
test("active Run disables Open but permits individual Delete", () => {
  const buttons = nodes(panel(snapshot([entry()]), false)).filter(
    (n) => n.type === "button",
  );
  assert.equal(buttons[0]!.props.disabled, true);
  assert.equal(buttons[1]!.props.disabled, false);
});
test("unavailable retained rows disable both history actions", () => {
  const buttons = nodes(
    panel(snapshot([entry()], "UNAVAILABLE", "warning")),
  ).filter((n) => n.type === "button");
  assert(buttons.every((n) => n.props.disabled));
});
test("UI preserves deterministic controller ordering and uses backend timestamp in time tag", () => {
  const a = entry("FIRST"),
    b = entry("SECOND"),
    html = renderToStaticMarkup(panel(snapshot([b, a])));
  assert(html.indexOf("SECOND") < html.indexOf("FIRST"));
  assert(html.includes('dateTime="' + T1 + '"'));
});
test("App keeps Run usable with unavailable history", async () => {
  const h = setup("x=1"),
    store = new DelayedStore();
  store.failOpen = true;
  const history = new HistoryController(store);
  await history.initialize();
  await h.controller.refreshHealth();
  const html = renderToStaticMarkup(
    createElement(App, { controller: h.controller, history }),
  );
  assert(html.includes("История недоступна"));
  assert(h.controller.canRun());
  assert(html.includes('class="primary"'));
  h.controller.dispose();
  history.dispose();
});
test("App READY history load switches editor to editable and hides Viewer", async () => {
  const h = setup("ACTIVE"),
    store = new DelayedStore(),
    history = new HistoryController(store);
  await history.initialize();
  bindRunHistory(h.controller, history);
  await h.controller.refreshHealth();
  h.controller.run();
  const socket = h.sockets[0]!;
  socket.ready();
  socket.accept();
  store.finish();
  finish(socket, "ACTIVE");
  await drain();
  const before = [h.sockets.length, h.calls.length],
    id = history.getSnapshot().entries[0]!.id;
  assert(openHistoryEntry(h.controller, history, id));
  const html = renderToStaticMarkup(
    createElement(App, { controller: h.controller, history }),
  );
  assert(html.includes('data-read-only="false"'));
  assert(!html.includes('class="viewer-controls"'));
  assert.deepEqual([h.sockets.length, h.calls.length], before);
  h.controller.dispose();
  history.dispose();
});
test("CRLF preview changes presentation line endings without claiming truncation", () => {
  assert.equal(codePreview("x=1\r\n"), "x=1\n");
});

import { RunActions } from "../../src/app/RunPanels.tsx";
import { recoveryHarness } from "../runs/release_recovery_helpers.ts";

for (const action of ["Edit", "History Open"] as const)
  test(`release recovery UI ${action} after failed download retains Retry Release and enables Run after 204`, async () => {
    const h = recoveryHarness(),
      store = new DelayedStore(),
      history = new HistoryController(store),
      e = entry("print('history')\r\n");
    store.entries = [e];
    await history.initialize();
    try {
      await h.complete();
      h.setRelease(async () => ({ status: 500, json: async () => null }));
      const actions = () =>
        RunActions({ controller: h.run, state: h.run.getSnapshot() });
      const button = (label: string) =>
        nodes(actions()).find(
          (n) => n.type === "button" && n.props.children === label,
        )!;
      if (action === "Edit")
        (button("Редактировать").props.onClick as () => void)();
      else {
        const historyButtons = nodes(
          panel(history.getSnapshot(), h.run.canLoadDraft(), (id) => {
            openHistoryEntry(h.run, history, id);
          }),
        ).filter((n) => n.type === "button");
        (historyButtons[0]!.props.onClick as () => void)();
      }
      await drain();
      const html = renderToStaticMarkup(
        createElement(App, { controller: h.run, history }),
      );
      assert(html.includes('data-read-only="false"'));
      assert(
        html.includes("Не удалось освободить результат предыдущего запуска"),
      );
      assert(!html.includes("Trace загружена"));
      assert.equal(button("Запустить").props.disabled, true);
      const retry = button("Повторить освобождение результата");
      assert(retry);
      h.setRelease(async () => ({ status: 204, json: async () => null }));
      (retry.props.onClick as () => void)();
      await drain();
      assert.equal(button("Запустить").props.disabled, false);
      assert(
        !renderToStaticMarkup(actions()).includes(
          "Повторить освобождение результата",
        ),
      );
      assert.equal(h.posts.length, 2);
      assert.equal(h.sockets.length, 1);
      if (action === "History Open")
        assert.equal(h.run.getSnapshot().editor.draftCode, e.code);
    } finally {
      h.run.dispose();
      history.dispose();
    }
  });
