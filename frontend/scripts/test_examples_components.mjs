import { build } from "vite";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
const temporary = await mkdtemp(
  join(tmpdir(), "codetrace-examples-components-"),
);
try {
  await build({
    configFile: false,
    ssr: { noExternal: true },
    define: {
      "import.meta.url": JSON.stringify(
        new URL("../tests/examples/components.suite.ts", import.meta.url).href,
      ),
    },
    resolve: {
      alias: {
        "../editor/MonacoEditor.tsx": new URL(
          "../tests/viewer/editor_stub.ts",
          import.meta.url,
        ).pathname,
      },
    },
    build: {
      ssr: new URL("../tests/examples/components.suite.ts", import.meta.url)
        .pathname,
      outDir: temporary,
      emptyOutDir: true,
      rollupOptions: { output: { entryFileNames: "components.mjs" } },
    },
  });
  const result = spawnSync(
    process.execPath,
    ["--test", "--test-reporter=spec", join(temporary, "components.mjs")],
    { stdio: "inherit", env: process.env },
  );
  if (result.status !== 0) process.exitCode = result.status ?? 1;
} finally {
  await rm(temporary, { recursive: true, force: true });
}
