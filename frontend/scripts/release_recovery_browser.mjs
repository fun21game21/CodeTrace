/** release recovery browser regression against an installed production bundle.
 * Only the trace GET is fault-injected; app actions use rendered controls.
 */
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtemp, rm, stat, writeFile, mkdir, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import net from "node:net";
import { EXAMPLES } from "../src/examples/catalog.ts";

const [bundleArg, outputArg, expected = "after"] = process.argv.slice(2);
const bundle = resolve(bundleArg ?? "dist/local");
const output = resolve(outputArg ?? ".test-results");
assert(bundle && output && ["before", "after"].includes(expected));
const require = process.env.CODETRACE_PLAYWRIGHT_MODULE
  ? createRequire(resolve(process.env.CODETRACE_PLAYWRIGHT_MODULE))
  : createRequire(import.meta.url);
const { chromium } = require("playwright");
const origin = "http://127.0.0.1:8000";
const report = {
  expected,
  startedAt: new Date().toISOString(),
  status: "RUNNING",
  scenarios: [],
  requests: [],
  sockets: [],
  cleanup: {},
};
const command = (args) =>
  execFileSync("docker", args, { encoding: "utf8", timeout: 20000 }).trim();
const containers = () =>
  command(["ps", "-aq", "--filter", "label=codetrace.managed=true"]);
const delay = () => new Promise((r) => setTimeout(r, 40));
async function until(read, predicate, label) {
  const end = Date.now() + 25000;
  let last;
  while (Date.now() < end) {
    last = await read();
    if (predicate(last)) return last;
    await delay();
  }
  throw Error(label + ": " + JSON.stringify(last));
}
async function portFree() {
  const server = net.createServer();
  await new Promise((ok, bad) => {
    server.once("error", bad);
    server.listen(8000, "127.0.0.1", ok);
  });
  await new Promise((ok) => server.close(ok));
  return true;
}
// Read-only observation, no production exports, controller actions or state writes.
function snapshot() {
  const el = document.querySelector(".workspace");
  if (!el) return null;
  let fiber = el[Object.keys(el).find((k) => k.startsWith("__reactFiber$"))];
  while (fiber && !fiber.memoizedProps?.controller?.getSnapshot)
    fiber = fiber.return;
  if (!fiber) return null;
  const run = fiber.memoizedProps.controller,
    r = run.getSnapshot();
  let viewer;
  for (let hook = fiber.memoizedState; hook; hook = hook.next) {
    const v = hook.memoizedState;
    if (v?.getSnapshot && "committedStep" in v.getSnapshot()) viewer = v;
  }
  const v = viewer?.getSnapshot();
  return {
    runId: r.active.runId,
    status: r.active.status,
    outcome: r.active.completion?.outcome,
    tracePhase: r.trace.phase,
    editor: r.editor,
    canRun: run.canRun(),
    releasePending: r.trace.releasePending,
    releaseError: r.trace.releaseError,
    viewerPhase: v?.phase,
    console: v?.committedView?.consoleEntries.map((e) => e.text).join(""),
    step: v?.committedStep,
    totalSteps: r.trace.eventCount,
  };
}
async function history(page, remove = false) {
  return page.evaluate(
    (remove) =>
      new Promise((ok, bad) => {
        const q = indexedDB.open("codetrace-history", 1);
        q.onerror = () => bad(q.error);
        q.onsuccess = () => {
          const db = q.result,
            tx = db.transaction("entries", remove ? "readwrite" : "readonly"),
            store = tx.objectStore("entries"),
            get = store.getAll();
          get.onsuccess = () => {
            if (remove) for (const e of get.result) store.delete(e.id);
          };
          tx.oncomplete = () => {
            db.close();
            ok(get.result);
          };
          tx.onabort = () => {
            db.close();
            bad(tx.error);
          };
        };
      }),
    remove,
  );
}
await mkdir(output, { recursive: true });
let backend,
  context,
  page,
  profile,
  backendExit,
  failure,
  serverLog = "",
  blockTrace = true;
const startSeconds = Math.floor(Date.now() / 1000),
  blocked = [],
  workers = [],
  browserErrors = [];
try {
  await portFree();
  assert.equal(containers(), "");
  backend = spawn(
    join(bundle, "venv/bin/python"),
    ["-I", join(bundle, "run.py")],
    { cwd: tmpdir(), stdio: ["ignore", "pipe", "pipe"] },
  );
  backend.on("exit", (code) => {
    backendExit = code;
  });
  backend.stdout.on("data", (b) => {
    serverLog += b;
  });
  backend.stderr.on("data", (b) => {
    serverLog += b;
  });
  await until(
    async () => {
      try {
        return await (await fetch(origin + "/api/health")).json();
      } catch {
        return null;
      }
    },
    (h) => h?.available,
    "health",
  );
  profile = await mkdtemp(
    join(tmpdir(), "codetrace-release-recovery-browser-"),
  );
  report.profile = profile;
  context = await chromium.launchPersistentContext(profile, {
    headless: true,
    viewport: { width: 1440, height: 1000 },
    serviceWorkers: "block",
  });
  report.browserVersion = context.browser().version();
  page = context.pages()[0];
  page.setDefaultTimeout(25000);
  page.on("pageerror", (e) => browserErrors.push(String(e)));
  context.on("request", (r) =>
    report.requests.push({ url: r.url(), method: r.method() }),
  );
  context.on("response", (r) => {
    const record = report.requests.findLast(
      (x) =>
        x.url === r.url() &&
        x.method === r.request().method() &&
        x.status === undefined,
    );
    if (record) record.status = r.status();
  });
  page.on("websocket", (ws) => {
    const s = { url: ws.url(), sent: [], received: [] };
    report.sockets.push(s);
    ws.on("framesent", (e) => s.sent.push(JSON.parse(e.payload)));
    ws.on("framereceived", (e) => s.received.push(JSON.parse(e.payload)));
  });
  page.on("worker", (w) => workers.push(w.url()));
  await context.route("**/api/runs/*/trace", async (route) => {
    if (blockTrace) {
      blocked.push(route.request().url());
      await route.abort("failed");
    } else await route.continue();
  });
  await page.goto(origin);
  const read = () => page.evaluate(snapshot);
  await until(read, (s) => s?.canRun, "initial Run enabled");
  assert.deepEqual(await history(page), []);
  report.initialHistoryEmpty = true;
  const runButton = page.getByRole("button", {
    name: "Запустить",
    exact: true,
  });
  const choose = async (id) => {
    await page.getByLabel("Примеры").selectOption(id);
    const source = EXAMPLES.find((e) => e.id === id).sourceCode;
    await until(
      read,
      (s) => s?.editor.draftCode === source,
      "exact example source",
    );
    return source;
  };
  const releaseRequests = (id) =>
    report.requests.filter(
      (r) =>
        r.method === "POST" && r.url === `${origin}/api/runs/${id}/release`,
    );
  const firstSource = await choose("recursive-factorial");
  await runButton.click();
  const first = await until(
    read,
    (s) => s?.tracePhase === "LOAD_FAILED",
    "fault injected LOAD_FAILED",
  );
  assert.equal(first.status, "SUCCEEDED");
  assert.equal(blocked.length, 1);
  assert.equal(releaseRequests(first.runId).length, 0);
  assert.equal(report.sockets.length, 1);
  assert.equal(report.sockets[0].sent.length, 1);
  await page
    .getByRole("button", { name: "Редактировать", exact: true })
    .click();
  await until(read, (s) => s?.editor.mode === "editing", "Edit");
  if (expected === "before") {
    // A fresh health response cannot repair the old stranded controller.
    await page
      .getByRole("button", { name: "Проверить backend", exact: true })
      .click();
    await until(
      () =>
        page
          .getByRole("button", { name: "Проверить backend", exact: true })
          .isEnabled(),
      Boolean,
      "health response",
    );
    const state = await read();
    assert.equal(state.canRun, false);
    assert.equal(state.tracePhase, "EMPTY");
    assert.equal(state.releasePending, false);
    assert.equal(
      await page
        .getByRole("button", {
          name: "Повторить освобождение результата",
          exact: true,
        })
        .count(),
      0,
    );
    assert.equal(releaseRequests(first.runId).length, 0);
    report.scenarios.push({
      kind: "Edit-before",
      runId: first.runId,
      state,
      releasePosts: 0,
      defectReproduced: true,
    });
  } else {
    const edited = await until(
      read,
      (s) => s?.canRun,
      "Run enabled after release",
    );
    assert.equal(edited.editor.draftCode, firstSource);
    assert.equal(edited.tracePhase, "EMPTY");
    assert.equal(edited.viewerPhase, "EMPTY");
    assert.equal(
      page.workers().filter((w) => w.url().includes("trace.worker-")).length,
      0,
    );
    await until(
      () => Promise.resolve(releaseRequests(first.runId)),
      (rs) => rs.length === 1 && rs[0].status === 204,
      "first release 204",
    );
    assert.equal(await runButton.isEnabled(), true);
    report.scenarios.push({
      kind: "Edit-after",
      runId: first.runId,
      state: edited,
      releasePosts: 1,
      releaseStatus: 204,
    });
    blockTrace = false;
    const secondSource = await choose("loop");
    await runButton.click();
    const second = await until(
      read,
      (s) =>
        s?.tracePhase === "READY" &&
        s.viewerPhase === "READY" &&
        !s.releasePending,
      "second Run READY",
    );
    assert.notEqual(first.runId, second.runId);
    assert.equal(second.status, "SUCCEEDED");
    assert.equal(second.outcome, "SUCCESS");
    await page
      .getByLabel("Шаг", { exact: true })
      .fill(String(second.totalSteps));
    await page.getByLabel("Шаг", { exact: true }).press("Enter");
    const final = await until(
      read,
      (s) => s?.step === second.totalSteps,
      "second final Step",
    );
    assert.equal(final.console, "Сумма: 15\n");
    report.scenarios.push({
      kind: "second-real-Run",
      runId: second.runId,
      state: final,
      source: secondSource,
    });
    // Cheap native History variant: third real Run, failed GET, open first source.
    await page
      .getByRole("button", { name: "Редактировать", exact: true })
      .click();
    await choose("function");
    blockTrace = true;
    await runButton.click();
    const third = await until(
      read,
      (s) => s?.tracePhase === "LOAD_FAILED",
      "History variant LOAD_FAILED",
    );
    const entries = await until(
      () => history(page),
      (rows) => rows.length === 3,
      "native History has three sources",
    );
    const old = entries.find((e) => e.code === firstSource);
    assert(old);
    const row = page
      .locator("[data-history-id]")
      .filter({ hasText: "def factorial" });
    await row.getByRole("button", { name: "Открыть", exact: true }).click();
    const opened = await until(
      read,
      (s) => s?.canRun && s.editor.draftCode === firstSource,
      "History source editable and Run enabled",
    );
    await until(
      () => Promise.resolve(releaseRequests(third.runId)),
      (rs) => rs.length === 1 && rs[0].status === 204,
      "History release 204",
    );
    assert.equal(
      page.workers().filter((w) => w.url().includes("trace.worker-")).length,
      0,
    );
    assert.equal(report.sockets.length, 3);
    assert.equal(blocked.length, 2);
    report.scenarios.push({
      kind: "History-Open-after",
      runId: third.runId,
      state: opened,
      releasePosts: 1,
      noExecutionOnOpen: true,
    });
  }
  await page.screenshot({
    path: join(output, `browser-${expected}.png`),
    fullPage: true,
  });
  assert.deepEqual(browserErrors, []);
} catch (error) {
  failure = error;
  report.error = String(error.stack ?? error);
} finally {
  if (page && !page.isClosed()) {
    // Entire isolated profile belongs to this regression; no user database is opened.
    await history(page, true).catch((e) => {
      report.cleanup.historyError = String(e);
    });
    report.cleanup.ownedHistoryRecords = (
      await history(page).catch(() => ["unknown"])
    ).length;
  }
  if (expected === "before" && report.scenarios[0]?.runId) {
    // Test cleanup, explicitly not an application repair action.
    const response = await fetch(
      `${origin}/api/runs/${report.scenarios[0].runId}/release`,
      { method: "POST", headers: { Origin: origin } },
    );
    report.cleanup.harnessReleaseStatus = response.status;
  }
  if (context) {
    await context.close();
    report.cleanup.browserClosed = true;
  }
  if (profile) {
    await rm(profile, { recursive: true, force: true });
    report.cleanup.profileRemoved = await stat(profile).then(
      () => false,
      () => true,
    );
  }
  if (backend) {
    if (backendExit === undefined) backend.kill("SIGINT");
    await until(
      () => Promise.resolve(backendExit),
      (x) => x !== undefined,
      "backend exit",
    );
    report.cleanup.backendStopped = backendExit === 0;
  }
  report.cleanup.portFree = await portFree();
  report.cleanup.managedContainers = containers()
    .split("\n")
    .filter(Boolean).length;
  const events = command([
    "events",
    "--since",
    String(startSeconds),
    "--until",
    String(Math.floor(Date.now() / 1000)),
    "--filter",
    "event=start",
    "--filter",
    "label=codetrace.managed=true",
    "--format",
    "{{json .}}",
  ]);
  report.dockerStarts = events ? events.split("\n").map(JSON.parse) : [];
  report.blockedTraceGets = blocked;
  report.workerUrls = workers;
  report.browserErrors = browserErrors;
  for (const s of report.scenarios) {
    s.dockerStarts = report.dockerStarts.filter(
      (e) => e.Actor.Attributes["codetrace.run_id"] === s.runId,
    ).length;
    s.wsStarts = report.sockets
      .filter((ws) =>
        ws.received.some(
          (m) => m.type === "run_accepted" && m.runId === s.runId,
        ),
      )
      .flatMap((ws) => ws.sent.filter((m) => m.type === "start")).length;
    if (s.dockerStarts !== 1 || s.wsStarts !== 1)
      failure ??= Error("Execution count mismatch");
  }
  report.cleanup.runtimeResultDirs = 0;
  for (const root of new Set([tmpdir(), "/private/tmp"])) {
    report.cleanup.runtimeResultDirs += (await readdir(root)).filter((n) =>
      n.startsWith("codetrace-result-v1-"),
    ).length;
  }
  if (
    report.cleanup.runtimeResultDirs ||
    !report.cleanup.backendStopped ||
    !report.cleanup.profileRemoved
  )
    failure ??= Error("Lifecycle cleanup failed");
  report.executionProof =
    "One WS start and Docker start per accepted Run, unchanged Runner single-exec path; no exec-call instrumentation.";
  if (report.cleanup.managedContainers || report.cleanup.ownedHistoryRecords)
    failure ??= Error("Cleanup failed");
  report.status = failure ? "FAIL" : "PASS";
  report.finishedAt = new Date().toISOString();
  await writeFile(
    join(output, `browser-${expected}.json`),
    JSON.stringify(report, null, 2) + "\n",
  );
  await writeFile(join(output, `browser-${expected}-server.log`), serverLog);
}
if (failure) throw failure;
console.log(
  JSON.stringify({
    status: report.status,
    expected,
    scenarios: report.scenarios.map((s) => s.kind),
    cleanup: report.cleanup,
  }),
);
