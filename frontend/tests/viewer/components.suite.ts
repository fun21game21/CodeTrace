import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createElement, isValidElement } from "react";
import type { ReactNode, ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { TraceCore } from "../../src/trace/index.ts";
import type { SelectedStepView, StepRequest } from "../../src/trace/index.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { ViewerControls } from "../../src/viewer/components/ViewerControls.tsx";
import { VariablesPanel } from "../../src/viewer/components/VariablesPanel.tsx";
import { CallStackPanel } from "../../src/viewer/components/CallStackPanel.tsx";
import { StepConsole } from "../../src/viewer/components/StepConsole.tsx";
import { ErrorsPanel } from "../../src/viewer/components/ErrorsPanel.tsx";
import { EventPanel } from "../../src/viewer/components/EventPanel.tsx";
import { App } from "../../src/app/App.tsx";
import { MonacoEditor } from "./editor_stub.ts";
import { begun, finish, setup, waiting, settled } from "../runs/helpers.ts";
type Element = ReactElement<Record<string, unknown>>;
function nodes(node: ReactNode): Element[] {
  if (Array.isArray(node))
    return node.flatMap((item) => nodes(item as ReactNode));
  if (!isValidElement(node)) return [];
  const element = node as Element;
  return [element, ...nodes(element.props.children as ReactNode)];
}
function text(node: ReactNode): string {
  if (Array.isArray(node))
    return node.map((item) => text(item as ReactNode)).join("");
  if (isValidElement(node))
    return text((node as Element).props.children as ReactNode);
  return typeof node === "string" || typeof node === "number"
    ? String(node)
    : "";
}
const html = (node: ReactNode) => renderToStaticMarkup(node);
const core = (name: string) =>
  TraceCore.load(
    JSON.parse(
      readFileSync(
        new URL("../trace/fixtures/" + name + "-128.json", import.meta.url),
        "utf8",
      ),
    ).trace,
  );
async function flush() {
  for (let i = 0; i < 16; i++) await Promise.resolve();
}
async function ready(name = "frame_changes", step = 4) {
  const c = core(name),
    viewer = new ViewerController();
  viewer.attach(c);
  await flush();
  viewer.seek(step);
  await flush();
  viewer.selectFrame("f999999");
  await flush();
  return { viewer, view: viewer.getSnapshot().committedView!, core: c };
}
test("controls disabled before attach", () => {
  const viewer = new ViewerController(),
    markup = html(
      createElement(ViewerControls, {
        controller: viewer,
        snapshot: viewer.getSnapshot(),
      }),
    );
  assert(markup.includes('id="direct-step"'));
  assert(markup.includes("disabled"));
  assert(!markup.includes("Шаг 1 /"));
  viewer.dispose();
});
test("initial Step, Previous state, range and exact speed options", async () => {
  const h = await ready("assignment", 1),
    tree = ViewerControls({
      controller: h.viewer,
      snapshot: h.viewer.getSnapshot(),
    });
  const all = nodes(tree),
    range = all.find((e) => e.props.type === "range")!;
  assert.equal(range.props.min, 1);
  assert.equal(range.props.max, h.core.totalSteps);
  assert.equal(range.props.step, 1);
  assert.equal(
    all.find((e) => e.props["aria-label"] === "Предыдущий шаг")!.props.disabled,
    true,
  );
  assert.equal(
    all.find((e) => e.props["aria-label"] === "Следующий шаг")!.props.disabled,
    false,
  );
  assert.deepEqual(
    all.filter((e) => e.type === "option").map((e) => e.props.value),
    [1, 2, 5, 10, 20],
  );
  assert(text(tree).includes("Шаг 1 / " + h.core.totalSteps));
  h.viewer.dispose();
});
for (const trigger of ["Enter", "blur"])
  test("direct Step component " + trigger + " handler commits", async () => {
    const h = await ready(),
      before = nodes(
        ViewerControls({
          controller: h.viewer,
          snapshot: h.viewer.getSnapshot(),
        }),
      );
    const input = before.find((e) => e.props.id === "direct-step")!;
    (input.props.onChange as (e: { target: { value: string } }) => void)({
      target: { value: "2" },
    });
    const all = nodes(
      ViewerControls({
        controller: h.viewer,
        snapshot: h.viewer.getSnapshot(),
      }),
    );
    if (trigger === "Enter")
      (
        all.find((e) => e.type === "form")!.props.onSubmit as (e: {
          preventDefault(): void;
        }) => void
      )({ preventDefault() {} });
    else
      (
        all.find((e) => e.props.id === "direct-step")!.props
          .onBlur as () => void
      )();
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, 2);
    h.viewer.dispose();
  });
test("invalid direct Step exposes labelled validation without navigation", async () => {
  const h = await ready();
  h.viewer.setStepDraft("1.5");
  h.viewer.commitStepDraft();
  const markup = html(
    createElement(ViewerControls, {
      controller: h.viewer,
      snapshot: h.viewer.getSnapshot(),
    }),
  );
  assert(markup.includes('aria-describedby="step-validation"'));
  assert(markup.includes('role="alert"'));
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
test("Next/Previous buttons invoke real Viewer path", async () => {
  const h = await ready();
  let all = nodes(
    ViewerControls({ controller: h.viewer, snapshot: h.viewer.getSnapshot() }),
  );
  (
    all.find((e) => e.props["aria-label"] === "Следующий шаг")!.props
      .onClick as () => void
  )();
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 5);
  all = nodes(
    ViewerControls({ controller: h.viewer, snapshot: h.viewer.getSnapshot() }),
  );
  (
    all.find((e) => e.props["aria-label"] === "Предыдущий шаг")!.props
      .onClick as () => void
  )();
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
test("range pointer release flushes and speed change uses controller", async () => {
  const h = await ready();
  const all = nodes(
    ViewerControls({ controller: h.viewer, snapshot: h.viewer.getSnapshot() }),
  );
  const range = all.find((e) => e.props.type === "range")!;
  (range.props.onChange as (e: { target: { value: string } }) => void)({
    target: { value: "2" },
  });
  (
    range.props.onPointerUp as (e: { currentTarget: { value: string } }) => void
  )({ currentTarget: { value: "2" } });
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  (
    all.find((e) => e.type === "select")!.props.onChange as (e: {
      target: { value: string };
    }) => void
  )({ target: { value: "20" } });
  assert.equal(h.viewer.getSnapshot().speed, 20);
  h.viewer.dispose();
});
test("final Step disables Next and Play", async () => {
  const h = await ready("assignment", 1);
  h.viewer.seek(h.core.totalSteps);
  await flush();
  const all = nodes(
    ViewerControls({ controller: h.viewer, snapshot: h.viewer.getSnapshot() }),
  );
  assert.equal(
    all.find((e) => e.props["aria-label"] === "Следующий шаг")!.props.disabled,
    true,
  );
  assert.equal(
    all.find((e) => e.props["aria-label"] === "Воспроизвести")!.props.disabled,
    true,
  );
  h.viewer.dispose();
});
test("variables Globals and selected locals are exact DTO rows", async () => {
  const h = await ready(),
    markup = html(createElement(VariablesPanel, { view: h.view }));
  assert(markup.includes("Globals"));
  assert(markup.includes("Locals"));
  for (const b of [...h.view.globals, ...h.view.selectedLocals])
    assert(markup.includes(">" + b.name + "<"));
  h.viewer.dispose();
});
test("module shows Globals without duplicate locals table", async () => {
  const h = await ready();
  h.viewer.selectFrame("f1");
  await flush();
  const markup = html(
    createElement(VariablesPanel, {
      view: h.viewer.getSnapshot().committedView!,
    }),
  );
  assert(markup.includes("Globals"));
  assert(!markup.includes("Locals"));
  h.viewer.dispose();
});
test("changed aliases both get visual and textual indicators", async () => {
  const h = await ready("binding_changes", 6),
    markup = html(createElement(VariablesPanel, { view: h.view }));
  assert(h.view.changedBindingNames.includes("a"));
  assert(h.view.changedBindingNames.includes("b"));
  const rows = nodes(VariablesPanel({ view: h.view })).filter(
    (e) => e.type === "tr" && e.props.className === "binding-changed",
  );
  assert(rows.some((e) => text(e).includes("aизменено")));
  assert(rows.some((e) => text(e).includes("bизменено")));
  assert(markup.includes("Изменено относительно предыдущего события"));
  h.viewer.dispose();
});
test("prototype names and huge numbers render safely", async () => {
  const c = core("specials"),
    v = c.selectStep({ step: c.totalSteps, requestId: 1 });
  const markup = html(createElement(VariablesPanel, { view: v }));
  for (const value of [
    "__proto__",
    "constructor",
    "prototype",
    "123456789012345678901234567890",
    "-0.0",
    "nan",
    "inf",
  ])
    assert(markup.includes(value), value);
});
test("stack order and recursive same names keep frame IDs", async () => {
  const c = core("recursion");
  let v = c.selectStep({ step: 1, requestId: 1 });
  for (let step = 1; step <= c.totalSteps; step++) {
    v = c.selectStep({ step, requestId: step });
    if (v.callStack.length >= 4) break;
  }
  const viewer = new ViewerController(),
    tree = CallStackPanel({ view: v, controller: viewer });
  const buttons = nodes(tree).filter((e) => e.type === "button");
  assert.equal(text(buttons[0]), "Globals");
  assert.deepEqual(
    buttons.slice(1).map((e) => text(e).match(/f\d+/)![0]),
    v.callStack.map((f) => f.id),
  );
  viewer.dispose();
});
for (const target of ["f2", null])
  test(
    "stack caller/Globals click publishes whole scope " + target,
    async () => {
      const h = await ready(),
        tree = CallStackPanel({ view: h.view, controller: h.viewer });
      const button = nodes(tree).find(
        (e) =>
          e.type === "button" &&
          (target === null ? text(e) === "Globals" : text(e).includes(target)),
      )!;
      (button.props.onClick as () => void)();
      await flush();
      assert.equal(h.viewer.getSnapshot().selectedFrameId, target);
      const markup = html(
        createElement(VariablesPanel, {
          view: h.viewer.getSnapshot().committedView!,
        }),
      );
      assert.equal(markup.includes("Locals"), target !== null);
      h.viewer.dispose();
    },
  );
for (const name of ["console", "input"])
  test("Step Console exact text/classes and rewind " + name, async () => {
    const c = core(name),
      final = c.selectStep({ step: c.totalSteps, requestId: 1 });
    const tree = StepConsole({ view: final }),
      pre = nodes(tree).find((e) => e.type === "pre")!;
    assert.equal(text(pre), final.consoleEntries.map((e) => e.text).join(""));
    assert(
      nodes(pre).some(
        (e) =>
          e.props.className ===
          "console-" + (name === "input" ? "input" : "stderr"),
      ),
    );
    const early = html(
      createElement(StepConsole, {
        view: c.selectStep({ step: 1, requestId: 2 }),
      }),
    );
    assert(early.includes("вывода ещё нет"));
    assert(!early.includes("<pre"));
  });
test("Console HTML-like source is escaped plain text", () => {
  const raw = JSON.parse(
    readFileSync(
      new URL("../trace/fixtures/console-128.json", import.meta.url),
      "utf8",
    ),
  ).trace;
  raw.console[0].text = "<script>bad()</script>";
  const c = TraceCore.load(raw),
    markup = html(
      createElement(StepConsole, {
        view: c.selectStep({ step: c.totalSteps, requestId: 1 }),
      }),
    );
  assert(markup.includes("&lt;script&gt;bad()&lt;/script&gt;"));
  assert(!markup.includes("<script>"));
});
test("EXCEPTION diagnostic is observed without terminal claims", () => {
  const c = core("handled");
  let v = c.selectStep({ step: 1, requestId: 0 });
  for (let step = 1; step <= c.totalSteps; step++) {
    v = c.selectStep({ step, requestId: step });
    if (v.event.kind === "EXCEPTION") break;
  }
  const markup = html(createElement(ErrorsPanel, { view: v }));
  assert(markup.includes("EXCEPTION_OBSERVED"));
  assert(markup.includes("могло быть обработано"));
  assert(!markup.includes("step-completion"));
});
test("terminal diagnostic details do not leak to early Steps", () => {
  const c = core("propagated"),
    first = c.selectStep({ step: 1, requestId: 1 }),
    final = c.selectStep({ step: c.totalSteps, requestId: 2 });
  const early = html(createElement(ErrorsPanel, { view: first })),
    end = html(createElement(ErrorsPanel, { view: final }));
  assert(!early.includes("UNCAUGHT_EXCEPTION"));
  assert(end.includes("UNCAUGHT_EXCEPTION"));
  assert(end.includes(final.completion!.primaryDiagnostic!.message));
  assert(end.includes("state final"));
});
test("partial RUN_END explains last observation", () => {
  const c = core("partial"),
    markup = html(
      createElement(ErrorsPanel, {
        view: c.selectStep({ step: c.totalSteps, requestId: 1 }),
      }),
    );
  assert(markup.includes("последнее наблюдение"));
  assert(markup.includes("state last_observed"));
});
test("event summary reports only recorded event metadata", async () => {
  const h = await ready("assignment", 2),
    markup = html(createElement(EventPanel, { view: h.view }));
  assert(markup.includes(h.view.event.kind));
  assert(markup.includes("State version"));
  assert(markup.includes("Перед выполнением"));
  assert(!markup.includes("swap"));
  h.viewer.dispose();
});
test("App before READY has no Viewer controls", () => {
  const h = setup(),
    markup = html(createElement(App, { controller: h.controller }));
  assert(!markup.includes('id="direct-step"'));
  assert(!markup.includes('id="python-input"'));
  h.controller.dispose();
});
test("active Run Input remains RUNS, no Step Console", async () => {
  const h = await begun();
  waiting(h.socket);
  const markup = html(createElement(App, { controller: h.controller }));
  assert(markup.includes('id="python-input"'));
  assert(markup.includes("Live Console"));
  assert(!markup.includes('id="direct-step"'));
  h.controller.dispose();
});
test("READY App uses one view for panels/Monaco and Edit clears it", async () => {
  const h = await begun(),
    viewer = new ViewerController(),
    unbind = bindRunViewer(h.controller, viewer);
  finish(h.socket);
  await settled();
  await flush();
  let markup = html(
    createElement(App, {
      controller: h.controller,
      viewer,
      Editor: MonacoEditor,
    }),
  );
  assert(markup.includes('id="direct-step"'));
  assert(markup.includes("Console — шаг 1"));
  assert(markup.includes('data-read-only="true"'));
  assert(!markup.includes('id="python-input"'));
  h.controller.edit();
  markup = html(
    createElement(App, {
      controller: h.controller,
      viewer,
      Editor: MonacoEditor,
    }),
  );
  assert(!markup.includes('id="direct-step"'));
  assert(markup.includes('data-read-only="false"'));
  assert(!markup.includes("Console — шаг"));
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  unbind();
  viewer.dispose();
  h.controller.dispose();
});
test("Monaco adapter follows committed view; stale pending highlight stays unchanged", async () => {
  const c = core("assignment");
  let resolve: ((v: SelectedStepView) => void) | null = null,
    request: StepRequest | null = null;
  const viewer = new ViewerController({
    executor: () => ({
      select(r) {
        if (r.step === 1) return Promise.resolve(c.selectStep(r));
        request = r;
        return new Promise<SelectedStepView>((r) => {
          resolve = r;
        });
      },
    }),
  });
  viewer.attach(c);
  await flush();
  const old = viewer.getSnapshot().committedView!;
  const render = () =>
    html(
      createElement(MonacoEditor, {
        value: c.sourceCode,
        readOnly: true,
        onChange() {},
        highlight: viewer.getSnapshot().committedView!.highlight,
      }),
    );
  const before = render();
  viewer.seek(3);
  await flush();
  assert.equal(render(), before);
  viewer.seek(4);
  assert(resolve !== null && request !== null);
  (resolve as (v: SelectedStepView) => void)(
    c.selectStep(request as StepRequest),
  );
  await flush();
  assert.equal(viewer.getSnapshot().committedView, old);
  assert.equal(render(), before);
  viewer.dispose();
});
test("Monaco adapter removes null decoration without editing exact source", () => {
  const c = core("assignment"),
    final = c.selectStep({ step: c.totalSteps, requestId: 1 });
  const markup = html(
    createElement(MonacoEditor, {
      value: c.sourceCode,
      readOnly: true,
      onChange() {},
      highlight: final.highlight,
    }),
  );
  assert(markup.includes('data-highlight-line=""'));
  assert(markup.includes('data-highlight-mode="none"'));
  assert(markup.includes('data-read-only="true"'));
});
