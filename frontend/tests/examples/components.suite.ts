import assert from "node:assert/strict";
import test from "node:test";
import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ExamplesMenu } from "../../src/examples/ExamplesMenu.tsx";
import { App } from "../../src/app/App.tsx";
import { setup } from "../runs/helpers.ts";
function nodes(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap((n) => nodes(n as ReactNode));
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...nodes(element.props.children as ReactNode)];
}
test("Examples uses labelled native select and exactly seven selectable options", () => {
  const menu = ExamplesMenu({ disabled: false, onSelect: () => {} }),
    all = nodes(menu);
  const select = all.find((e) => e.type === "select")!;
  assert.equal(select.props["aria-label"], "Примеры");
  assert.equal(
    all.filter((e) => e.type === "option" && e.props.value !== "").length,
    7,
  );
  assert.equal(select.props.value, "");
});
test("selection callback forwards only ID; can reselect same example", () => {
  const ids: string[] = [];
  const menu = ExamplesMenu({
    disabled: false,
    onSelect: (id) => ids.push(id),
  });
  const select = nodes(menu).find((e) => e.type === "select")!;
  const change = select.props.onChange as (e: {
    target: { value: string };
  }) => void;
  change({ target: { value: "sorting" } });
  change({ target: { value: "sorting" } });
  assert.deepEqual(ids, ["sorting", "sorting"]);
});
test("disabled state uses native keyboard/pointer disabling", () => {
  const html = renderToStaticMarkup(
    ExamplesMenu({ disabled: true, onSelect: () => {} }),
  );
  assert.match(html, /<select[^>]*disabled=""/);
});
test("App initially has empty editor and no selected example/auto execution", () => {
  const h = setup();
  try {
    const html = renderToStaticMarkup(
      createElement(App, { controller: h.controller }),
    );
    assert.match(html, /aria-label="Примеры"/);
    assert.match(html, /Выбрать…/);
    assert.equal(h.controller.getSnapshot().editor.draftCode, "");
    assert.equal(h.sockets.length, 0);
    assert.equal(h.calls.length, 0);
  } finally {
    h.controller.dispose();
  }
});
