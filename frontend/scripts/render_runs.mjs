import { build } from "vite";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
const temporary = await mkdtemp(join(tmpdir(), "codetrace-runs-render-"));
try {
  await build({
    configFile: false,
    ssr: { noExternal: true },
    define: {
      "import.meta.url": JSON.stringify(
        new URL("../tests/runs/render_smoke.ts", import.meta.url).href,
      ),
    },
    resolve: {
      alias: {
        "../editor/MonacoEditor.tsx": new URL(
          "../tests/runs/editor_stub.ts",
          import.meta.url,
        ).pathname,
      },
    },
    build: {
      ssr: new URL("../tests/runs/render_smoke.ts", import.meta.url).pathname,
      outDir: temporary,
      emptyOutDir: true,
      rollupOptions: { output: { entryFileNames: "render_smoke.mjs" } },
    },
  });
  const result = spawnSync(
    process.execPath,
    [join(temporary, "render_smoke.mjs")],
    { stdio: "inherit", env: process.env },
  );
  if (result.status !== 0) process.exitCode = result.status ?? 1;
} finally {
  await rm(temporary, { recursive: true, force: true });
}
