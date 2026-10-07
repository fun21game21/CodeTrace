import assert from "node:assert/strict";
import test from "node:test";
import { attached, flush } from "../viewer/helpers.ts";
import { selector } from "./helpers.ts";
const config = {
  kind: "array" as const,
  mode: "cells" as const,
  indices: [],
  array: selector("a"),
};
test("config reselection publishes view + immutable config atomically", async () => {
  const h = await attached("frame_changes", true);
  try {
    const old = h.viewer.getSnapshot();
    assert(h.viewer.setVisualizerConfig(config));
    await flush();
    const pending = h.viewer.getSnapshot();
    assert.equal(pending.committedView, old.committedView);
    assert.equal(pending.visualizerConfig, old.visualizerConfig);
    assert(pending.latestRequestId > old.latestRequestId);
    assert.equal(h.d.calls.at(-1)!.step, old.committedStep);
    h.d.resolve(1);
    await flush();
    const done = h.viewer.getSnapshot();
    assert.equal(done.visualizerConfig.kind, "array");
    assert.equal(done.committedView!.requestId, done.latestRequestId);
    assert(Object.isFrozen(done.visualizerConfig));
    assert(Object.isFrozen((done.visualizerConfig as typeof config).indices));
  } finally {
    h.viewer.dispose();
  }
});
test("stale config response ignored; newest pending config wins", async () => {
  const h = await attached("frame_changes", true);
  try {
    const old = h.viewer.getSnapshot();
    h.viewer.setVisualizerConfig(config);
    await flush();
    h.viewer.setVisualizerConfig({ kind: "call_stack" });
    await flush();
    h.d.resolve(1);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedView, old.committedView);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
    h.d.resolve(2);
    await flush();
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "call_stack");
  } finally {
    h.viewer.dispose();
  }
});
test("config persists through Step/frame navigation, detach resets", async () => {
  const h = await attached();
  try {
    assert(h.viewer.setVisualizerConfig({ kind: "call_stack" }));
    await flush();
    h.viewer.seek(4);
    await flush();
    h.viewer.selectFrame("f1");
    await flush();
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "call_stack");
    h.viewer.detach();
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
    h.viewer.attach(h.core);
    await flush();
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
  } finally {
    h.viewer.dispose();
  }
});
test("failed config retains committed view/config and stops Play", async () => {
  const h = await attached("frame_changes", true);
  try {
    const old = h.viewer.getSnapshot();
    h.viewer.setVisualizerConfig(config);
    await flush();
    h.d.reject(1);
    await flush();
    const s = h.viewer.getSnapshot();
    assert.equal(s.committedView, old.committedView);
    assert.equal(s.visualizerConfig, old.visualizerConfig);
    assert(s.selectionError);
    assert(!s.playing);
  } finally {
    h.viewer.dispose();
  }
});
test("invalid config does not dispatch or mutate; config is detached from caller", async () => {
  const h = await attached();
  try {
    const old = h.viewer.getSnapshot();
    assert(
      !h.viewer.setVisualizerConfig({
        ...config,
        indices: [selector("i"), selector("j"), selector("k")],
      }),
    );
    assert.equal(h.viewer.getSnapshot(), old);
    const c = { ...config, array: selector("a") };
    h.viewer.setVisualizerConfig(c);
    c.array.name = "b";
    await flush();
    assert.equal(
      (h.viewer.getSnapshot().visualizerConfig as typeof c).array.name,
      "a",
    );
  } finally {
    h.viewer.dispose();
  }
});
test("pending config carried by a newer Step command without interim publication", async () => {
  const h = await attached("frame_changes", true);
  try {
    h.viewer.setVisualizerConfig(config);
    await flush();
    h.viewer.seek(4);
    await flush();
    h.d.resolve(1);
    await flush();
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "none");
    h.d.resolve(2);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, 4);
    assert.equal(h.viewer.getSnapshot().visualizerConfig.kind, "array");
  } finally {
    h.viewer.dispose();
  }
});
