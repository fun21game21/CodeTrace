import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { App } from "../../src/app/App.tsx";
import { begun, finish, health, settled, setup, waiting } from "./helpers.ts";

function markup(controller: ReturnType<typeof setup>["controller"]) {
  return renderToStaticMarkup(createElement(App, { controller }));
}
const unavailable = setup();
unavailable.setHttp(async () => ({
  status: 200,
  json: async () => health(false),
}));
await unavailable.controller.refreshHealth();
let html = markup(unavailable.controller);
assert(html.includes("CodeTrace"));
assert(/disabled=""[^>]*>Запустить/.test(html));
assert(html.includes('data-read-only="false"'));
assert(!html.includes('id="python-input"'));
unavailable.controller.dispose();
const active = await begun();
html = markup(active.controller);
assert(html.includes('data-read-only="true"'));
assert(/disabled=""[^>]*>Запустить/.test(html));
waiting(active.socket);
html = markup(active.controller);
assert(html.includes('id="python-input"'));
assert(html.includes("8192 UTF-8 bytes"));
active.controller.dispose();
const completed = await begun();
completed.socket.status("RUNNING");
completed.socket.emit("console", {
  entry: {
    index: 0,
    kind: "stdout",
    text: "<script>hello</script>",
    requestId: null,
  },
});
completed.socket.status("FINALIZING");
completed.socket.status("SUCCEEDED");
html = markup(completed.controller);
assert(html.includes("&lt;script&gt;hello&lt;/script&gt;"));
assert(!html.includes("<script>hello"));
completed.controller.dispose();
const ready = await begun();
finish(ready.socket);
await settled();
html = markup(ready.controller);
assert(html.includes("TRACE READY"));
assert(html.includes("Завершённый live output"));
assert(!html.includes('id="python-input"'));
ready.controller.dispose();
console.log(
  "React render smoke passed: root, unavailable/active Run disabled, conditional Input, read-only, text escaping, READY.",
);
